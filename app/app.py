import csv
import base64
from datetime import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from io import StringIO
from pathlib import Path

import fitz
from PySide6.QtCore import QProcess, Qt, QTimer, QUrl, QSize, Signal, QEvent, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QPixmap, QShortcut, QKeySequence, QPainter
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QTextEdit,
    QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget, QColorDialog, QSlider, QStackedWidget,
    QScrollArea, QSizePolicy, QGridLayout)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from service_tools import ServiceMixin,song_sections,atomic_json
from word_finder import WordFinder
from update_engine import UpdateDownload
from release_info import VERSION,DEFAULT_UPDATE_URL
from agenda_core import THEMES, advance_alert, load_package_full, save_package
from projection import SlideCanvas, AspectFrame, MonitorLabel, PROFILE_DEFAULTS, slide_parts
from browser_bridge import BrowserSession
from web_media import validated_url
from media_import import MediaImportWorker
from capture_tools import CaptureToolsMixin
from camera_capture import CameraView
from bible_core import normalize_rows, search_rows, verse_slide, paginate, passage_reference
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None

DATA = Path(os.getenv('APPDATA', Path.home())) / 'ChableszShowVoiceCamera'
DATA.mkdir(parents=True, exist_ok=True)
MEDIA = DATA / 'slides'
MEDIA.mkdir(exist_ok=True)
ASSETS = DATA / 'agenda_media'
ASSETS.mkdir(exist_ok=True)
BIBLES = DATA / 'bibles'
BIBLES.mkdir(exist_ok=True)
VERSIONS = ['KJV', 'NKJV', 'AMP', 'NLT', 'Yoruba', 'TPT', 'RSV', 'NIV', 'ESV', 'CSB', 'WEB', 'ASV', 'YLT']
VISUAL_SETTINGS = {'background','text_color','font_size','font_family','text_align','max_verses','max_chars',
                   'image_fit','volume','alert_color','alert_motion','alert_position','alert_speed',
                   'alert_duration','background_image','brand','theme'}
VISUAL_SETTINGS.update(PROFILE_DEFAULTS)
VISUAL_SETTINGS.add('gradient_end')


def image_from_pdf(path, page):
    doc = fitz.open(path)
    pix = doc[page].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    target = MEDIA / (str(abs(hash((str(path), page, path.stat().st_mtime_ns)))) + '.png')
    pix.save(str(target))
    doc.close()
    return str(target)


def presentation_to_pdf(path, method='Auto'):
    exe = shutil.which('soffice') or shutil.which('libreoffice')
    if not exe and os.name == 'nt':
        for root in (os.getenv('ProgramFiles', ''), os.getenv('ProgramFiles(x86)', '')):
            candidate = Path(root) / 'LibreOffice' / 'program' / 'soffice.exe'
            if root and candidate.exists(): exe = str(candidate); break
    folder = tempfile.mkdtemp(prefix='chableszshow_')
    output = Path(folder) / (path.stem + '.pdf')
    if method == 'LibreOffice' and not exe:
        raise RuntimeError('LibreOffice was selected but not found. Install it or choose Auto.')
    if method == 'Microsoft PowerPoint' and os.name != 'nt':
        raise RuntimeError('Microsoft PowerPoint conversion requires Windows.')
    if (method == 'Microsoft PowerPoint' or (method == 'Auto' and not exe)) and os.name == 'nt':
        source = str(path.resolve()).replace("'", "''")
        target = str(output.resolve()).replace("'", "''")
        script = ("$p=New-Object -ComObject PowerPoint.Application; $p.AutomationSecurity=3; "
                  f"$d=$p.Presentations.Open('{source}',-1,0,0); "
                  f"try {{$d.SaveAs('{target}',32)}} finally {{$d.Close();$p.Quit()}}")
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        result = subprocess.run(['powershell.exe','-NoProfile','-EncodedCommand',encoded],
                                capture_output=True, text=True, timeout=90)
    elif exe:
        result = subprocess.run([exe, '-env:UserInstallation=file:///' + folder.replace('\\', '/'),
            '--headless', '--convert-to', 'pdf', '--outdir', folder, str(path)],
            capture_output=True, text=True, timeout=90)
    else:
        raise RuntimeError('Install LibreOffice or Microsoft PowerPoint, or export the deck as PDF.')
    if result.returncode or not output.exists():
        raise RuntimeError(result.stderr or result.stdout or 'PowerPoint conversion failed')
    return output


class Stage(QWidget):
    preview_tapped = Signal()
    def __init__(self, preview=False):
        super().__init__()
        self.is_preview = preview
        self.setStyleSheet('background:#080d1a;color:white;')
        self.stack = QVBoxLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.setSpacing(0)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.content, 1)
        self.brand = QLabel('')
        self.brand.setAlignment(Qt.AlignCenter)
        self.brand.setWordWrap(True)
        self.brand.setStyleSheet('background:transparent;color:#eacb79;font-size:20px;font-weight:bold;padding:5px;')
        self.brand.hide()
        self.stack.addWidget(self.brand)
        self.alert_host = QWidget()
        self.alert_host.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.stack.addWidget(self.alert_host)
        self.alert_host.hide()
        self.alert = QLabel('', self.alert_host)
        self.alert.setTextFormat(Qt.PlainText)
        self.alert.setAlignment(Qt.AlignCenter)
        self.alert.setWordWrap(True)
        self.alert.setStyleSheet('background:#bd2037;color:white;font-size:27px;font-weight:bold;padding:12px;')
        self.alert.hide()
        self.alert_tick = QTimer(self)
        self.alert_tick.timeout.connect(self.animate_alert)
        self.alert_x = 0
        self.alert_y = 0
        self.alert_generation = 0
        self.clock = QLabel('')
        self.clock.setAlignment(Qt.AlignRight)
        self.clock.setStyleSheet('background:#101b2e;color:#f7d17c;font-size:25px;padding:6px 18px;')
        self.clock.hide()
        self.stack.addWidget(self.clock)
        self.player = None
        self.audio = None
        self.repeat_mode = 'Off'
        self.replayed_once = False
        self.black = False
        self.current = None
        self.settings = {'background':'#080d1a','text_color':'#ffffff','font_size':52,'font_family':'Arial','text_align':'Center','max_verses':1,'max_chars':0,'image_fit':'contain','volume':80,'alert_color':'#bd2037','alert_motion':'Static','alert_position':'Bottom','alert_speed':4,'alert_duration':0,'background_image':'','brand':'','theme':'Standard Dark','default_songbook':'RCCG Hymn','default_version':'KJV','song_lines':5,'auto_backup':True,'web_enabled':True,'confidence_enabled':True,'broadcast_enabled':True,'ppt_method':'Auto','repeat_mode':'Off','document_folder':str(Path.home()/'Documents'),'church_licence':''}
        self.active_settings = self.settings
        self.settings.update(PROFILE_DEFAULTS)
        self.settings['gradient_end']=''
        self.web_session=None
        self.camera_session=None
        self.web_placeholder=None
        self.setCursor(Qt.PointingHandCursor if preview else Qt.ArrowCursor)
        self.setMinimumSize(0, 0)
        self.content.setMinimumSize(0, 0)
        self.content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def minimumSizeHint(self): return QSize(0, 0)

    def set_alert(self, message):
        self.alert_generation += 1
        self.alert_tick.stop()
        self.alert.setText(message.strip())
        pixels = max(10, int(self.width()*.022))
        self.alert.setStyleSheet(f"background:{self.settings['alert_color']};color:white;font-size:{pixels}px;font-weight:bold;padding:6px;")
        if not message.strip():
            self.alert.hide()
            self.alert_host.hide()
            return
        self.alert_host.setFixedHeight(max(28, int(self.height()*.16)))
        self.alert_host.setStyleSheet(f"background:{self.settings['alert_color']};")
        self.stack.removeWidget(self.alert_host)
        top = self.settings.get('alert_position') == 'Top'
        self.stack.insertWidget(0 if top else 1, self.alert_host)
        self.alert_host.setVisible(not self.black)
        self.stack.activate()
        motion = self.settings['alert_motion']
        self.alert.setWordWrap(motion in ('Static','Bottom to top','Top to bottom'))
        self.position_alert(reset=True)
        self.alert.setVisible(not self.black)
        self.alert.raise_()
        if motion != 'Static': self.alert_tick.start(30)
        duration = int(self.settings.get('alert_duration', 0))
        if duration > 0:
            generation = self.alert_generation
            QTimer.singleShot(duration * 1000, lambda: self.clear_alert() if generation == self.alert_generation else None)

    def clear_alert(self):
        self.alert_generation += 1
        self.alert_tick.stop()
        self.alert.clear()
        self.alert.hide()
        self.alert_host.hide()

    def position_alert(self, reset=False):
        if not self.alert.text(): return
        motion = self.settings['alert_motion']
        host_width, host_height = self.alert_host.width(), self.alert_host.height()
        self.alert.setMaximumWidth(max(1, host_width-16) if motion in ('Static','Bottom to top','Top to bottom') else 16777215)
        self.alert.adjustSize()
        width = min(self.alert.sizeHint().width(), max(1, host_width-16)) if motion in ('Static','Bottom to top','Top to bottom') else self.alert.sizeHint().width()
        height = min(host_height, self.alert.height())
        self.alert.resize(width, height)
        target_y = max(0, (host_height-height)//2)
        if reset:
            self.alert_x = host_width if motion == 'Scroll left' else (-width if motion == 'Scroll right' else (host_width-width)//2)
            self.alert_y = host_height if motion in ('Rolling','Bottom to top') else (-height if motion == 'Top to bottom' else target_y)
        elif motion in ('Static','Scroll left','Scroll right'):
            if motion == 'Static': self.alert_x = (host_width-width)//2
            self.alert_y = target_y
        self.alert.move(int(self.alert_x), int(self.alert_y))

    def animate_alert(self):
        if not self.alert.text() or self.black: return
        motion = self.settings['alert_motion']
        self.alert_x, self.alert_y, finished = advance_alert(
            motion, self.alert_x, self.alert_y, self.alert.width(), self.alert.height(),
            self.alert_host.width(), self.alert_host.height(), self.settings['alert_speed'])
        if finished: self.alert_tick.stop()
        self.alert.move(int(self.alert_x), int(self.alert_y))

    def clear_content(self):
        if self.web_session:
            self.web_session.close();self.web_session=None
        if self.player:
            self.player.stop()
            self.player = None
            self.audio = None
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().hide()
                child.widget().deleteLater()
        self.web_placeholder=None

    def display(self, item, web_session=None):
        self.current = item
        if self.black:
            return
        self.content.show()
        self.clear_content()
        cfg = dict(self.settings)
        profile='bible' if item['kind']=='verse' else ('song' if item.get('role')=='song' or item.get('attribution') else '')
        if profile:
            chosen=cfg.get(profile+'_theme','Global')
            if chosen in THEMES:cfg.update(THEMES[chosen])
            if cfg.get(profile+'_background_image'):cfg['background_image']=cfg[profile+'_background_image']
        if item.get('theme') in THEMES:
            cfg.update(THEMES[item['theme']])
            if self.settings.get('brand'):
                cfg['brand'] = self.settings['brand']
        self.active_settings = cfg
        self.setStyleSheet(f"background:{cfg['background']};color:{cfg['text_color']};")
        kind = item['kind']
        footer = [cfg.get('brand',''), item.get('attribution','')]
        self.brand.setText(' • '.join(part for part in footer if part))
        self.brand.setVisible(kind in ('verse', 'text') and bool(self.brand.text()) and not item.get('hide_brand'))
        if kind=='verse':
            self.brand.setText(cfg.get('bible_footer_text') or cfg.get('brand',''))
            self.brand.setVisible(bool(cfg.get('bible_footer_enabled',True) and self.brand.text()))
            self.brand.setStyleSheet(f"background:transparent;color:{cfg['bible_footer_color']};font-size:{max(8,int(cfg['bible_footer_size']*4/3*self.width()/1280))}px;font-weight:bold;padding:3px;")
        if kind == 'video':
            video = QVideoWidget()
            audio = QAudioOutput(video)
            player = QMediaPlayer(video)
            player.setAudioOutput(audio)
            player.setVideoOutput(video)
            player.setSource(QUrl.fromLocalFile(item['path']))
            audio.setVolume(cfg['volume']/100)
            audio.setMuted(self.is_preview)
            self.content_layout.addWidget(video)
            if self.is_preview:video.installEventFilter(self)
            self.player = player
            self.audio = audio
            self.replayed_once = False
            self.start_position=0
            player.mediaStatusChanged.connect(self.video_status)
            player.play()
        elif kind == 'camera':
            if self.camera_session:
                view=CameraView(self.camera_session);self.content_layout.addWidget(view)
                if self.is_preview:view.installEventFilter(self)
            else:self.show_web_placeholder('Camera is not connected.')
        elif kind == 'url':
            self.show_web_placeholder('Starting the supported web player…')
            session=web_session or BrowserSession(item['url'],DATA/'web_profile',self)
            if web_session and session.stage and session.stage is not self:
                try:session.tapped.disconnect(session.stage.preview_tapped.emit)
                except (RuntimeError,TypeError):pass
                try:session.message.disconnect(session.stage.show_web_placeholder)
                except (RuntimeError,TypeError):pass
            session.tapped.connect(self.preview_tapped.emit)
            session.message.connect(self.show_web_placeholder)
            session.attach(self)
        else:
            if kind == 'url': item = dict(item, text=item['url'])
            self.content_layout.addWidget(SlideCanvas(item, cfg))

    def clear_placeholder(self):
        if self.web_placeholder:
            self.content_layout.removeWidget(self.web_placeholder)
            self.web_placeholder.hide();self.web_placeholder.deleteLater();self.web_placeholder=None

    def show_web_placeholder(self,message):
        self.clear_placeholder()
        label=QLabel(message);label.setWordWrap(True);label.setAlignment(Qt.AlignCenter)
        label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Ignored)
        self.web_placeholder=label;self.content_layout.addWidget(label)

    def take_web_session(self):
        session=self.web_session
        if session:
            if session.widget:self.content_layout.removeWidget(session.widget)
            self.web_session=None
        return session

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and self.is_preview and self.current and self.current['kind'] in ('video','url','camera'):
            self.preview_tapped.emit()
        super().mousePressEvent(event)

    def eventFilter(self,watched,event):
        if self.is_preview and event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
            self.preview_tapped.emit();return True
        return super().eventFilter(watched,event)

    def video_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia) and getattr(self,'start_position',0) and self.player:
            position=self.start_position;self.start_position=0;self.player.setPosition(position)
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.player:
            if self.repeat_mode == 'Loop' or (self.repeat_mode == 'Repeat once' and not self.replayed_once):
                self.replayed_once = True
                self.player.setPosition(0)
                self.player.play()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        pixels = max(8, int(self.width()*.015))
        self.brand.setStyleSheet(f'background:transparent;color:#eacb79;font-size:{pixels}px;font-weight:bold;padding:3px;')
        if self.current and self.current['kind']=='verse':
            cfg=self.active_settings
            self.brand.setStyleSheet(f"background:transparent;color:{cfg['bible_footer_color']};font-size:{max(8,int(cfg['bible_footer_size']*4/3*self.width()/1280))}px;font-weight:bold;padding:3px;")
        self.brand.setMaximumHeight(max(18, int(self.height()*.08)))
        self.clock.setStyleSheet(f'background:#101b2e;color:#f7d17c;font-size:{pixels}px;padding:3px;')
        if self.alert.text():
            self.alert_host.setFixedHeight(max(28, int(self.height()*.16)))
            self.alert.setStyleSheet(f"background:{self.settings['alert_color']};color:white;font-size:{max(10,int(self.width()*.022))}px;font-weight:bold;padding:6px;")
        self.position_alert(reset=self.settings.get('alert_motion') != 'Static')

    def blackout(self, enabled):
        if self.black == enabled:
            return
        self.black = enabled
        if enabled:
            self.content.hide()
            self.was_playing=bool(self.player and self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState)
            if self.player:self.player.pause()
            if self.web_session:self.web_session.media('black')
            self.setStyleSheet('background:#000000;color:white;')
        else:
            self.content.show()
            self.setStyleSheet(f"background:{self.active_settings['background']};color:{self.active_settings['text_color']};")
            if self.player and getattr(self,'was_playing',False):self.player.play()
            if self.web_session:self.web_session.media('restore')
        self.brand.setVisible(not enabled and bool(self.brand.text()) and not (self.current or {}).get('hide_brand') and (not self.current or self.current.get('kind')!='verse' or self.active_settings.get('bible_footer_enabled',True)))
        self.alert.setVisible(not enabled and bool(self.alert.text()))
        self.alert_host.setVisible(not enabled and bool(self.alert.text()))
        if not enabled and self.alert.text(): self.alert.raise_()
        self.clock.setVisible(not enabled and bool(self.clock.text()))
        if not enabled and self.current and not self.content_layout.count():
            self.display(self.current)


class ConfidenceWindow(QWidget):
    """High contrast notes and cues for people on stage, never projected to audience."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ChableszShow Confidence')
        self.resize(1000, 600)
        self.setStyleSheet('background:#05080c;color:white;')
        layout = QVBoxLayout(self)
        top = QHBoxLayout(); layout.addLayout(top)
        self.time_label = QLabel('')
        self.time_label.setStyleSheet('font-size:36px;color:#f5d274;')
        self.timer_label = QLabel('')
        self.timer_label.setAlignment(Qt.AlignRight)
        self.timer_label.setStyleSheet('font-size:36px;color:#f5d274;')
        top.addWidget(self.time_label); top.addWidget(self.timer_label)
        self.current_label = QLabel('CURRENT\nNothing Live')
        self.current_label.setWordWrap(True)
        self.current_label.setAlignment(Qt.AlignCenter)
        self.current_label.setStyleSheet('background:#10233b;font-weight:bold;padding:25px;')
        self.current_label.setFont(QFont('Arial', 40, QFont.Weight.Bold))
        layout.addWidget(self.current_label, 3)
        self.next_label = QLabel('NEXT\nNothing queued')
        self.next_label.setWordWrap(True)
        self.next_label.setStyleSheet('background:#172b20;color:#e0f5d3;padding:15px;')
        for label in (self.current_label,self.next_label):label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Ignored)
        layout.addWidget(self.next_label, 1)
        self.notes_label = QLabel('')
        self.notes_label.setWordWrap(True)
        self.notes_label.setStyleSheet('background:#2b2313;color:#ffe3a8;font-size:24px;padding:10px;')
        self.notes_label.hide()
        layout.addWidget(self.notes_label)

    def update_cues(self, current, upcoming, timer, notes):
        self.time_label.setText(datetime.now().strftime('%H:%M:%S'))
        self.timer_label.setText(timer)
        def summary(item):
            if not item: return 'Nothing queued'
            if item.get('kind') in ('video','image','url'): return item.get('title','Media')
            if item.get('kind')=='verse':
                body,reference=slide_parts(item)
                passage=item.get('passage_reference','')
                return (passage+'\n' if passage else '')+body+'\n'+reference
            return item.get('text','')
        self.current_label.setText('CURRENT\n' + summary(current))
        self.next_label.setText('NEXT\n' + summary(upcoming))
        self.notes_label.setText('STAGE NOTE: ' + notes if notes else '')
        self.notes_label.setVisible(bool(notes))
        self.fit_presenter_text()

    def fit_presenter_text(self):
        from PySide6.QtGui import QFontMetrics
        for label,maximum in ((self.current_label,40),(self.next_label,26)):
            for size in range(maximum,9,-1):
                font=QFont('Arial',size,QFont.Weight.Bold)
                rect=QFontMetrics(font).boundingRect(0,0,max(1,label.width()-60),100000,Qt.TextWordWrap,label.text())
                if rect.height()<=max(1,label.height()-60):break
            label.setFont(font)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fit_presenter_text()


class StandaloneVideo(QWidget):
    def __init__(self, path, volume=80):
        super().__init__()
        self.setWindowTitle('ChableszShow Video • ' + Path(path).name)
        self.resize(960, 600)
        layout = QVBoxLayout(self)
        self.screen = QVideoWidget(); layout.addWidget(self.screen, 1)
        self.audio = QAudioOutput(self); self.audio.setVolume(volume / 100)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.screen); self.player.setAudioOutput(self.audio)
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        controls = QHBoxLayout(); layout.addLayout(controls)
        for name, callback in [('Play / Pause', self.toggle), ('Restart', self.restart),
                               ('Back 10s', lambda: self.seek(-10000)),
                               ('Forward 10s', lambda: self.seek(10000)), ('Mute', self.mute)]:
            button = QPushButton(name); button.clicked.connect(callback); controls.addWidget(button)
        self.repeat = QComboBox(); self.repeat.addItems(['Off','Loop','Repeat once']); controls.addWidget(self.repeat)
        self.progress = QSlider(Qt.Horizontal); self.progress.setRange(0, 1000)
        self.progress.sliderMoved.connect(lambda value: self.player.setPosition(self.player.duration() * value // 1000))
        layout.addWidget(self.progress)
        self.player.positionChanged.connect(self.update_progress)
        self.player.durationChanged.connect(lambda _: self.update_progress(self.player.position()))
        self.player.mediaStatusChanged.connect(self.on_status)
        self.repeated = False
        self.player.play()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState: self.player.pause()
        else: self.player.play()

    def restart(self):
        self.repeated = False; self.player.setPosition(0); self.player.play()

    def seek(self, delta):
        self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + delta)))

    def mute(self): self.audio.setMuted(not self.audio.isMuted())

    def update_progress(self, position):
        if self.player.duration() and not self.progress.isSliderDown():
            self.progress.setValue(position * 1000 // self.player.duration())

    def on_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            mode = self.repeat.currentText()
            if mode == 'Loop' or (mode == 'Repeat once' and not self.repeated):
                self.repeated = True; self.player.setPosition(0); self.player.play()

    def closeEvent(self, event):
        self.player.stop(); super().closeEvent(event)


class OptionsDialog(QDialog):
    SECTIONS = ['General','Display','Songbooks','Bibles','Agenda','Lyrics editor','Video',
                'Presentation screen','Typography','PowerPoint','Notifications','Customization','Modules']

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle('ChableszShow Options')
        self.resize(900, 650)
        self.controls = {}
        self.setStyleSheet('QDialog,QWidget{background:#17263e;color:white;} QLineEdit,QComboBox,QSpinBox,QTextEdit,QListWidget{background:#0d1b2f;color:white;border:1px solid #506886;padding:5px;} QPushButton{background:#2d5fa6;color:white;padding:9px;border-radius:5px;}')
        root = QVBoxLayout(self)
        top = QHBoxLayout(); root.addLayout(top, 1)
        self.navigation = QListWidget(); self.navigation.setFixedWidth(195)
        self.navigation.addItems(self.SECTIONS)
        top.addWidget(self.navigation)
        self.pages = QStackedWidget(); top.addWidget(self.pages, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.build_pages()
        self.navigation.setCurrentRow(0)
        buttons = QHBoxLayout(); root.addLayout(buttons)
        buttons.addStretch()
        self.app.button('Help', self.show_help, buttons)
        self.app.button('Apply', self.apply_changes, buttons)
        self.app.button('Cancel', self.reject, buttons)
        self.app.button('OK', self.accept_changes, buttons)
        available = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(900,available.width()-40),min(650,available.height()-60))

    def page(self, title):
        widget = QWidget(); layout = QVBoxLayout(widget)
        heading = QLabel(title); heading.setStyleSheet('font-size:22px;font-weight:bold;color:#f8d573;')
        layout.addWidget(heading)
        form = QFormLayout(); layout.addLayout(form)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(widget)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.pages.addWidget(scroll)
        return layout, form

    def combo(self, form, key, label, choices):
        widget = QComboBox(); widget.addItems(choices)
        widget.setCurrentText(str(self.app.live.settings.get(key,choices[0])))
        form.addRow(label, widget); self.controls[key] = widget
        return widget

    def number(self, form, key, label, low, high):
        widget = QSpinBox(); widget.setRange(low, high)
        widget.setValue(int(self.app.live.settings.get(key, low)))
        form.addRow(label, widget); self.controls[key] = widget
        return widget

    def line(self, form, key, label):
        widget = QLineEdit(str(self.app.live.settings.get(key,'')))
        form.addRow(label, widget); self.controls[key] = widget
        return widget

    def check(self, form, key, label):
        widget = QCheckBox(label)
        widget.setChecked(bool(self.app.live.settings.get(key,False)))
        form.addRow('', widget); self.controls[key] = widget
        return widget

    def action(self, layout, label, callback):
        return self.app.button(label, callback, layout)

    def choose_color(self, key):
        color = QColorDialog.getColor(QColor(self.controls[key].text()), self, 'Choose colour')
        if color.isValid(): self.controls[key].setText(color.name())

    def color(self, form, key, label):
        row = QHBoxLayout()
        widget = QLineEdit(str(self.app.live.settings.get(key,'')))
        self.controls[key] = widget
        row.addWidget(widget)
        self.action(row, 'Choose…', lambda: self.choose_color(key))
        form.addRow(label, row)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose document folder', self.controls['document_folder'].text())
        if folder: self.controls['document_folder'].setText(folder)

    def explore_folder(self):
        folder = Path(self.controls['document_folder'].text()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def choose_background(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose slide background', self.app.document_folder(), 'Images (*.png *.jpg *.jpeg *.webp)')
        if path: self.controls['background_image'].setText(path)

    def profile_background(self,layout,form,profile):
        key=profile+'_background_image'
        self.line(form,key,'Background image')
        row=QHBoxLayout();layout.addLayout(row)
        def choose():
            path,_=QFileDialog.getOpenFileName(self,'Choose '+profile+' background',self.app.document_folder(),'Images (*.png *.jpg *.jpeg *.webp *.bmp)')
            if path:self.controls[key].setText(path)
        self.action(row,'Choose image',choose)
        self.action(row,'Clear image',lambda:self.controls[key].clear())
        self.combo(form,profile+'_theme','Default theme',['Global']+list(THEMES))

    def direct_action(self, callback):
        if self.apply_changes(): callback()

    def screen_action(self, kind):
        if not self.apply_changes(): return
        if kind == 'live':
            self.app.screens.setCurrentIndex(self.output_screen.currentIndex())
            self.app.test_output()
        elif kind == 'confidence':
            self.app.confidence_screens.setCurrentIndex(self.confidence_screen.currentIndex())
            self.app.open_confidence()
        else:
            self.app.open_broadcast()

    def build_pages(self):
        layout, form = self.page('General')
        form.addRow('Interface language', QLabel('English (current supported language)'))
        self.line(form, 'document_folder', 'Document folder')
        row = QHBoxLayout(); layout.addLayout(row)
        self.action(row, 'Select folder', self.choose_folder)
        self.action(row, 'Explore folder', self.explore_folder)
        licence = QTextEdit(self.app.live.settings.get('church_licence',''))
        licence.setMinimumHeight(90)
        form.addRow('Church licence / attribution', licence)
        self.controls['church_licence'] = licence
        self.check(form, 'auto_backup', 'Create a backup copy when the agenda changes')
        layout.addStretch()

        layout, form = self.page('Display')
        self.color(form,'background','Slide background colour')
        self.color(form,'text_color','Slide text colour')
        self.line(form,'background_image','Background image')
        row = QHBoxLayout(); layout.addLayout(row)
        self.action(row,'Choose image',self.choose_background)
        self.action(row,'Clear image',lambda: self.controls['background_image'].clear())
        self.combo(form,'image_fit','Image fitting',['contain','cover'])
        layout.addStretch()

        layout, form = self.page('Songbooks')
        self.line(form,'default_songbook','Default songbook')
        self.profile_background(layout,form,'song')
        self.action(layout,'Create a songbook',lambda: self.direct_action(self.app.new_songbook))
        self.action(layout,'Import songs CSV',lambda: self.direct_action(self.app.import_songs))
        self.action(layout,'Import songs / hymns from HTTPS CSV',lambda: self.direct_action(self.app.import_songs_online))
        layout.addStretch()

        layout, form = self.page('Bibles')
        self.combo(form,'default_version','Default translation',VERSIONS)
        self.number(form,'max_verses','Maximum verses per slide',1,10)
        self.number(form,'max_chars','Maximum characters (0 = no limit)',0,1000)
        self.profile_background(layout,form,'bible')
        self.check(form,'bible_header_enabled','Show Bible header')
        self.line(form,'bible_header_text','Header text')
        self.color(form,'bible_header_color','Header colour')
        self.number(form,'bible_header_size','Header size (points)',12,60)
        self.check(form,'bible_reference_enabled','Show scripture reference / translation')
        self.combo(form,'bible_reference_position','Reference position',['Top','Bottom'])
        self.color(form,'bible_reference_color','Reference colour')
        self.number(form,'bible_reference_size','Reference size (points)',12,60)
        self.check(form,'bible_footer_enabled','Show Bible footer')
        self.line(form,'bible_footer_text','Footer text (blank = church brand)')
        self.color(form,'bible_footer_color','Footer colour')
        self.number(form,'bible_footer_size','Footer size (points)',10,40)
        available=QLabel('Included offline: KJV, ASV, WEB, YLT. Other editions require permitted text imports.')
        available.setWordWrap(True);layout.addWidget(available)
        self.action(layout,'Import selected Bible CSV',lambda: self.direct_action(self.app.import_bible))
        self.action(layout,'Import selected Bible from HTTPS CSV',lambda: self.direct_action(self.app.import_bible_online))
        self.action(layout,'Use bundled selected edition (backs up imported text)',lambda: self.direct_action(self.app.restore_bundled_kjv))
        layout.addStretch()

        layout, form = self.page('Agenda')
        self.action(layout,'Save portable agenda',lambda: self.direct_action(self.app.save_agenda))
        self.action(layout,'Open portable agenda',lambda: self.direct_action(self.app.open_agenda))
        self.action(layout,'Backup library',lambda: self.direct_action(self.app.backup_library))
        self.action(layout,'Restore library',lambda: self.direct_action(self.app.restore_library))
        layout.addStretch()

        layout, form = self.page('Lyrics editor')
        self.number(form,'song_lines','Maximum lines per song slide',2,12)
        layout.addWidget(QLabel('Blank lines start a new slide. The line limit splits long verses.'))
        self.action(layout,'Edit selected song',lambda: self.direct_action(self.app.edit_song))
        layout.addStretch()

        layout, form = self.page('Video')
        self.number(form,'volume','Video volume (%)',0,100)
        self.combo(form,'repeat_mode','Default repeat',['Off','Repeat once','Loop'])
        layout.addWidget(QLabel('Live video transport controls are beside the operator Preview.'))
        self.action(layout,'Set up supported Edge web player',lambda: self.direct_action(self.app.setup_web_player))
        layout.addStretch()

        layout, form = self.page('Presentation screen')
        self.output_screen = QComboBox()
        for i in range(self.app.screens.count()): self.output_screen.addItem(self.app.screens.itemText(i), self.app.screens.itemData(i))
        self.output_screen.setCurrentIndex(self.app.screens.currentIndex())
        form.addRow('Audience Live screen', self.output_screen)
        self.confidence_screen = QComboBox()
        for i in range(self.app.confidence_screens.count()): self.confidence_screen.addItem(self.app.confidence_screens.itemText(i), self.app.confidence_screens.itemData(i))
        self.confidence_screen.setCurrentIndex(self.app.confidence_screens.currentIndex())
        form.addRow('Confidence screen', self.confidence_screen)
        self.action(layout,'Test audience output',lambda: self.screen_action('live'))
        self.action(layout,'Open confidence',lambda: self.screen_action('confidence'))
        self.action(layout,'Open OBS window',lambda: self.screen_action('broadcast'))
        layout.addStretch()

        layout, form = self.page('Typography')
        self.number(form,'font_size','Font size (points)',24,100)
        self.combo(form,'font_family','Font family',['Arial','Calibri','Candara','Segoe UI','Times New Roman'])
        self.combo(form,'text_align','Text alignment',['Left','Center','Right'])
        layout.addStretch()

        layout, form = self.page('PowerPoint')
        self.combo(form,'ppt_method','Conversion method',['Auto','LibreOffice','Microsoft PowerPoint'])
        layout.addWidget(QLabel('Slides are converted to still pages. Animations are not played.'))
        self.action(layout,'Import PowerPoint',lambda: self.direct_action(self.app.add_presentation))
        self.action(layout,'Get LibreOffice converter',lambda: QDesktopServices.openUrl(QUrl('https://www.libreoffice.org/download/download-libreoffice/')))
        layout.addStretch()

        layout, form = self.page('Notifications')
        self.combo(form,'alert_motion','Alert movement',['Static','Scroll left','Scroll right','Rolling','Bottom to top','Top to bottom'])
        self.combo(form,'alert_position','Message band position',['Top','Bottom'])
        self.number(form,'alert_speed','Motion speed (1–10)',1,10)
        self.number(form,'alert_duration','Hide after seconds (0 = stay)',0,300)
        self.color(form,'alert_color','Alert background')
        self.alert_preview = Stage(preview=True)
        self.alert_preview.setMinimumHeight(190)
        layout.addWidget(self.alert_preview)
        message = QLabel('Messages move inside their own band, leaving the scripture unobstructed.')
        message.setWordWrap(True); layout.addWidget(message)
        self.action(layout,'Apply and show test alert',self.test_alert)
        layout.addStretch()

        layout, form = self.page('Customization')
        self.line(form,'brand','Footer / church name')
        self.combo(form,'theme','Theme',list(THEMES))
        self.action(layout,'Apply theme to all slides',self.apply_theme)
        self.action(layout,'Apply theme to selected slide',self.apply_selected_theme)
        layout.addStretch()

        layout, form = self.page('Modules')
        self.check(form,'web_enabled','Enable web URLs (internet required)')
        self.check(form,'confidence_enabled','Enable confidence monitor')
        self.check(form,'broadcast_enabled','Enable OBS output')
        layout.addWidget(QLabel('Bible and song libraries work offline after their texts are imported.'))
        layout.addStretch()

    def values(self):
        result = {}
        for key, widget in self.controls.items():
            if isinstance(widget,QComboBox): result[key] = widget.currentText()
            elif isinstance(widget,QSpinBox): result[key] = widget.value()
            elif isinstance(widget,QCheckBox): result[key] = widget.isChecked()
            elif isinstance(widget,QTextEdit): result[key] = widget.toPlainText()
            else: result[key] = widget.text().strip()
        return result

    def apply_changes(self):
        values = self.values()
        if not values['default_songbook']:
            QMessageBox.warning(self,'Songbook','Enter a default songbook name.')
            return False
        for key in (k for k in values if k.endswith('_color') or k=='background'):
            if not QColor(values[key]).isValid():
                QMessageBox.warning(self,'Invalid colour',f'Choose a valid colour for {key}.')
                return False
        folder = Path(values['document_folder']).expanduser()
        try: folder.mkdir(parents=True,exist_ok=True)
        except OSError as error:
            QMessageBox.warning(self,'Document folder',str(error)); return False
        values['document_folder'] = str(folder)
        for key in ('background_image','bible_background_image','song_background_image'):
            background = values[key]
            if background and not Path(background).is_file():
                QMessageBox.warning(self,'Background image','Choose an existing image file or clear the '+key+' field.')
                return False
        for stage in (self.app.live,self.app.preview,self.app.broadcast):
            stage.settings.update(values)
            if stage.alert.text(): stage.set_alert(stage.alert.text())
        self.app.font_size.setValue(values['font_size'])
        self.app.font_family.setCurrentText(values['font_family'])
        self.app.text_align.setCurrentText(values['text_align'])
        self.app.max_verses.setValue(values['max_verses'])
        self.app.max_chars.setValue(values['max_chars'])
        self.app.volume.setValue(values['volume'])
        self.app.image_fit.setCurrentText(values['image_fit'])
        self.app.theme.setCurrentText(values['theme'])
        self.app.version.setCurrentText(values['default_version'])
        self.app.refresh_songbooks()
        self.app.songbook.setCurrentText(values['default_songbook'])
        self.app.repeat.setCurrentText(values['repeat_mode'])
        self.app.confidence_button.setEnabled(values['confidence_enabled'])
        self.app.broadcast_button.setEnabled(values['broadcast_enabled'])
        if not values['confidence_enabled']: self.app.confidence.close()
        if not values['broadcast_enabled']: self.app.broadcast.close()
        self.app.screens.setCurrentIndex(self.output_screen.currentIndex())
        self.app.confidence_screens.setCurrentIndex(self.confidence_screen.currentIndex())
        self.app.save_settings()
        return True

    def test_alert(self):
        if self.apply_changes():
            self.alert_preview.settings.update(self.values())
            self.alert_preview.set_alert('ChableszShow alert movement test')
            self.app.alert_input.setText('ChableszShow alert movement test')
            self.app.show_alert()

    def apply_theme(self):
        if self.apply_changes():
            self.app.apply_global_theme()
            theme = THEMES[self.controls['theme'].currentText()]
            for key in ('background','text_color','brand'):
                self.controls[key].setText(theme[key])

    def apply_selected_theme(self):
        if self.apply_changes(): self.app.apply_selected_theme()

    def accept_changes(self):
        if self.apply_changes(): self.accept()

    def show_help(self):
        QMessageBox.information(self,'Options help','Select a section on the left. Apply saves changes without closing; OK saves and closes; Cancel discards unsaved fields. Import, backup, test, and output buttons perform their actions immediately. New language packs and native PowerPoint animations are not available in this version.')


class App(CaptureToolsMixin,ServiceMixin,QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'ChableszShow v{VERSION} | Media + Bible projection')
        self.resize(1320, 800)
        self.items = []
        self.verses = []
        self.songs = []
        self.songbooks = []
        self.current_live_index = -1
        self.import_worker=None
        self.update_worker=None
        self.pending_update=None
        self.pending_update_close=False
        self.pending_close=False
        self.preview_popup=None
        self.active_screen_key = None
        self.network = QNetworkAccessManager(self)
        self.online_replies = []
        self.closing = False
        self.live = Stage()
        self.preview = Stage(preview=True)
        self.broadcast = Stage(preview=True)
        self.confidence = ConfidenceWindow()
        presenter_controls=QHBoxLayout();self.confidence.layout().addLayout(presenter_controls)
        for title,callback in [('Previous Live',self.previous_live),('Next Live',self.next_live),('Bible search',self.focus_bible)]:
            button=QPushButton(title);button.clicked.connect(callback);presenter_controls.addWidget(button)
        self.live.setWindowTitle('ChableszShow Live')
        self.broadcast.setWindowTitle('ChableszShow OBS Output')
        self.broadcast.setFixedSize(1280, 720)
        self.seconds = None
        self.elapsed = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.build_ui()
        self.preview.preview_tapped.connect(self.send_preview_live)
        self.load_settings()
        self.load_data()
        self.init_service_tools()
        if Path(__file__).with_name("BUNDLED_RUNTIME").exists():
            self.update_url.setEnabled(False);self.auto_update.setChecked(False);self.auto_update.setEnabled(False)
            self.update_url.setPlaceholderText("Use a checked full Windows installer for updates")
        self.init_capture_tools()
        self.refresh_screens()
        QGuiApplication.instance().screenAdded.connect(lambda _: self.refresh_screens())
        QGuiApplication.instance().screenRemoved.connect(lambda _: self.screen_removed())
        QShortcut(QKeySequence('F5'), self, activated=self.go_live)
        QShortcut(QKeySequence('F6'), self, activated=self.restore_outputs)
        QShortcut(QKeySequence('Escape'), self, activated=self.black_outputs)
        QShortcut(QKeySequence('Ctrl+Right'), self, activated=self.next_live)
        QShortcut(QKeySequence('Ctrl+Left'), self, activated=self.previous_live)
        QShortcut(QKeySequence('Ctrl+F'),self,activated=self.open_word_finder)
        QApplication.instance().installEventFilter(self)
        available = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1320,available.width()-40),min(800,available.height()-60))

    def open_word_finder(self):
        if not self.verses:
            self.status.setText('Select an installed Bible translation first.');return
        WordFinder(self).exec()

    def refresh_app(self):
        self.refresh_screens()
        if Path(__file__).with_name("BUNDLED_RUNTIME").exists():
            self.status.setText("Screens refreshed. This bundled edition updates through a checked Windows installer.");return
        if self.pending_update:
            self.apply_downloaded_update();return
        if self.update_worker:
            self.status.setText('An update check is already running.');return
        url=self.update_url.text().strip()
        if not url:
            self.status.setText('Screens refreshed. Automatic updates need a hosted release address in Service tools.');return
        worker=UpdateDownload(url,DATA/'updates',self);self.update_worker=worker
        worker.progress.connect(self.status.setText)
        worker.failed.connect(self.update_failed)
        worker.completed.connect(self.update_downloaded)
        worker.finished.connect(self.update_finished);worker.start()

    @Slot(str)
    def update_failed(self,message):
        self.status.setText('Update unavailable: '+message+'. Your installed app still works offline.')

    @Slot(object)
    def update_downloaded(self,release):
        self.pending_update=release
        if release:self.status.setText('Version '+str(release['version'])+' verified. Close outputs before installing.')
        else:self.status.setText('ChableszShow v'+str(VERSION)+' is up to date.')

    @Slot()
    def update_finished(self):
        worker=self.update_worker;self.update_worker=None
        if worker:worker.deleteLater()
        if self.pending_update_close:
            self.pending_update_close=False;self.close();return
        if self.pending_update and self.auto_update.isChecked():self.apply_downloaded_update()

    def apply_downloaded_update(self):
        if Path(__file__).with_name("BUNDLED_RUNTIME").exists():
            self.status.setText("Bundled editions update through a checked full Windows installer.");return False
        if not self.pending_update:
            self.status.setText('No downloaded update. Press Refresh to check.');return False
        if self.update_worker or self.import_worker or self.live.isVisible() or self.broadcast.isVisible() or self.confidence.isVisible():
            self.status.setText('Update ready. Close Live, OBS and confidence outputs, then press Refresh to install safely.');return False
        if os.name!='nt':
            self.status.setText('Automatic installation is supported on Windows.');return False
        release=self.pending_update;root=Path(__file__).parent
        process=QProcess();process.setProgram(sys.executable)
        process.setArguments([str(root/'update_apply.py'),'--pid',str(os.getpid()),'--root',str(root),
            '--package',release['path'],'--version',str(release['version'])]);process.setWorkingDirectory(str(root))
        ok,_=process.startDetached()
        if not ok:self.status.setText('Could not start the update helper.');return False
        self.close();return True

    def button(self, title, action, container):
        b = QPushButton(title)
        b.clicked.connect(action)
        container.addWidget(b)
        return b

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(12,12,12,10); outer.setSpacing(8)
        header = QHBoxLayout(); outer.addLayout(header)
        title = QLabel('ChableszShow'); title.setStyleSheet('font-size:22px;font-weight:700;color:#f4f7fc;')
        header.addWidget(title); header.addStretch()
        header.addWidget(QLabel('Projector'))
        self.screens = QComboBox(); self.screens.setMaximumWidth(290)
        self.screens.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.screens.setMinimumContentsLength(16)
        self.screens.currentIndexChanged.connect(self.remember_screen); header.addWidget(self.screens)
        self.button('Refresh', self.refresh_app, header)
        self.button('Voice / Camera', self.open_capture_tools, header)
        self.button('Test screen', self.test_output, header)
        self.button('Options', self.open_options, header)
        actions = QHBoxLayout(); outer.addLayout(actions)
        self.button('Open Live', self.open_live, actions)
        live_button = self.button('GO LIVE  F5', self.go_live, actions)
        live_button.setStyleSheet('background:#b92f45;color:white;font-weight:bold;padding:9px 14px;')
        self.button('Previous', self.previous_live, actions)
        self.button('Next', self.next_live, actions)
        self.button('Black  Esc', self.black_outputs, actions)
        self.button('Restore  F6', self.restore_outputs, actions)
        self.button('Bible  Ctrl+B', self.focus_bible, actions)
        self.button('Service tools',lambda:self.library_section.setCurrentIndex(5),actions)
        actions.addStretch()
        outputs = QHBoxLayout(); outer.addLayout(outputs)
        outputs.addWidget(QLabel('Confidence'))
        self.confidence_screens = QComboBox(); self.confidence_screens.setMaximumWidth(250)
        self.confidence_screens.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.confidence_screens.setMinimumContentsLength(12)
        self.confidence_screens.currentIndexChanged.connect(self.remember_confidence_screen)
        outputs.addWidget(self.confidence_screens)
        self.confidence_button = self.button('Open', self.open_confidence, outputs)
        self.broadcast_button = self.button('OBS output', self.open_broadcast, outputs)
        self.button('Close outputs', self.close_extra_outputs, outputs); outputs.addStretch()
        self.splitter = QSplitter(Qt.Horizontal); outer.addWidget(self.splitter, 1)
        library = QWidget(); left = QVBoxLayout(library); left.setContentsMargins(0,0,8,0)
        self.library_section = QComboBox()
        self.library_section.addItems(['Bible','Service agenda','Songs & hymns','Alerts & timer','Quick settings','Service tools'])
        self.library_section.setStyleSheet('font-size:15px;font-weight:bold;padding:8px;')
        left.addWidget(self.library_section)
        self.library_pages = QStackedWidget(); left.addWidget(self.library_pages, 1)
        self.library_section.currentIndexChanged.connect(self.library_pages.setCurrentIndex)
        library.setMinimumWidth(290); self.splitter.addWidget(library)

        def page():
            content = QWidget(); layout = QVBoxLayout(content); layout.setSpacing(8)
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(content)
            scroll.setFrameShape(QScrollArea.NoFrame)
            self.library_pages.addWidget(scroll)
            return layout

        def grid(layout, entries, columns=2):
            row = QGridLayout(); layout.addLayout(row)
            for i, (name, action) in enumerate(entries):
                button = QPushButton(name); button.clicked.connect(action)
                row.addWidget(button, i//columns, i%columns)
            return row

        bl = page()
        self.version = QComboBox(); self.version.addItems(VERSIONS)
        version_row = QHBoxLayout(); bl.addLayout(version_row)
        version_row.addWidget(QLabel('Translation')); version_row.addWidget(self.version,1)
        self.version_status = QLabel(''); self.version_status.setWordWrap(True)
        self.version_status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.version_status.setStyleSheet('color:#a9bbd3;font-size:12px;'); bl.addWidget(self.version_status)
        self.query = QLineEdit(); self.query.setPlaceholderText('John 3:16-18, Ps 23, or search words')
        self.bible_search_timer = QTimer(self); self.bible_search_timer.setSingleShot(True)
        self.bible_search_timer.timeout.connect(self.search_bible)
        self.query.textChanged.connect(lambda: self.bible_search_timer.start(180))
        self.query.returnPressed.connect(self.search_bible_live); bl.addWidget(self.query)
        navigator = QFormLayout(); bl.addLayout(navigator)
        self.book = QComboBox(); self.chapter = QComboBox(); self.verse_number = QComboBox()
        self.book.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        navigator.addRow('Book',self.book)
        numbers = QHBoxLayout()
        for name, widget in [('Chapter',self.chapter),('Verse',self.verse_number)]:
            numbers.addWidget(QLabel(name)); numbers.addWidget(widget,1)
        bl.addLayout(numbers)
        self.version.currentTextChanged.connect(self.load_version)
        self.book.currentTextChanged.connect(self.update_chapters)
        self.chapter.currentTextChanged.connect(self.update_verse_numbers)
        self.verse_number.currentTextChanged.connect(self.select_reference)
        grid(bl,[('Previous verse',lambda: self.step_bible(-1)),('Next verse',lambda: self.step_bible(1))])
        self.results = QListWidget(); self.results.setMinimumHeight(110)
        self.results.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Ignored)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setTextElideMode(Qt.ElideRight)
        self.results.currentRowChanged.connect(self.preview_verse)
        self.results.itemDoubleClicked.connect(lambda _: self.verse_live())
        bl.addWidget(self.results, 1)
        self.bible_count = QLabel(''); self.bible_count.setWordWrap(True); bl.addWidget(self.bible_count)
        help_keys=QLabel('Enter: passage Live • → / ↓ / Page Down: next • ← / ↑ / Page Up: back')
        help_keys.setWordWrap(True);bl.addWidget(help_keys)
        grid(bl,[('Show verse Live',self.verse_live),('Queue verse',self.add_verse),
                 ('Queue passage',self.add_passage),('Show chapter',self.show_chapter)])
        self.button('Find words / phrase  Ctrl+F',self.open_word_finder,bl)
        self.button('Continue to next verse Live',self.continue_bible,bl)
        grid(bl,[('Import CSV',self.import_bible),('Import online',self.import_bible_online)])

        pl = page()
        self.list = QListWidget(); self.list.setMinimumHeight(120)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self.preview_selection)
        self.list.itemDoubleClicked.connect(lambda _: self.go_live()); pl.addWidget(self.list,1)
        grid(pl,[('Add media',self.add_files),('Standalone video',self.open_standalone_video),
                 ('Add URL',self.add_url),('Add text',self.add_text)])
        grid(pl,[('PowerPoint / slides',self.add_presentation),('Cancel import',self.cancel_media_import)])
        self.import_status=QLabel('');self.import_status.setWordWrap(True)
        self.import_status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.import_status.hide();pl.addWidget(self.import_status)
        grid(pl,[('Move up',lambda: self.move_item(-1)),('Move down',lambda: self.move_item(1)),('Remove',self.remove_item)],3)
        grid(pl,[('Save agenda',self.save_agenda),('Open agenda',self.open_agenda),
                 ('Backup library',self.backup_library),('Restore library',self.restore_library)])

        sl = page(); self.song_search = QLineEdit(); self.song_search.setPlaceholderText('Search title, hymn number, or lyrics')
        self.song_search.textChanged.connect(self.search_songs); sl.addWidget(self.song_search)
        self.songbook = QComboBox(); self.songbook.addItem('All songbooks')
        self.songbook.currentTextChanged.connect(self.search_songs); sl.addWidget(self.songbook)
        self.song_list = QListWidget(); self.song_list.setMinimumHeight(120)
        self.song_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.song_list.itemDoubleClicked.connect(lambda _: self.queue_song()); sl.addWidget(self.song_list,1)
        grid(sl,[('Add song',self.add_song),('Edit song',self.edit_song),('Import CSV',self.import_songs),
                 ('Import online',self.import_songs_online),('New songbook',self.new_songbook),('Queue song slides',self.queue_song)])

        self.repeat_chorus=QCheckBox('Repeat chorus after each verse')
        sl.addWidget(self.repeat_chorus)
        self.repeat_chorus.toggled.connect(self.save_operator_preferences)
        self.button('Song section buttons',self.show_song_sections,sl)

        tl = page(); tl.addWidget(QLabel('Audience message'))
        self.alert_input = QLineEdit(); self.alert_input.setPlaceholderText('Please silence your phones'); tl.addWidget(self.alert_input)
        grid(tl,[('Show alert',self.show_alert),('Clear alert',self.clear_alert)])
        tl.addWidget(QLabel('Countdown minutes'))
        self.minutes = QSpinBox(); self.minutes.setRange(1,240); self.minutes.setValue(10); tl.addWidget(self.minutes)
        grid(tl,[('Start countdown',self.start_countdown),('Elapsed timer',self.start_elapsed),('Hide timer',self.hide_timer)])
        tl.addWidget(QLabel('Confidence monitor note'))
        self.stage_note = QLineEdit(); self.stage_note.setPlaceholderText('Pastor speaking next')
        self.stage_note.textChanged.connect(self.refresh_confidence); tl.addWidget(self.stage_note)
        note = QLabel('Alerts use a separate message band. Adjust motion, colour, and duration in Options > Notifications.')
        note.setWordWrap(True); note.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred); tl.addWidget(note); tl.addStretch()

        st = page(); self.button('Full Options',self.open_options,st)
        form = QFormLayout(); st.addLayout(form)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.font_size = QSpinBox(); self.font_size.setRange(24,100)
        self.font_family = QComboBox(); self.font_family.addItems(['Arial','Calibri','Candara','Segoe UI','Times New Roman'])
        self.text_align = QComboBox(); self.text_align.addItems(['Left','Center','Right'])
        self.max_verses = QSpinBox(); self.max_verses.setRange(1,10)
        self.max_chars = QSpinBox(); self.max_chars.setRange(0,1000)
        self.volume = QSpinBox(); self.volume.setRange(0,100)
        self.image_fit = QComboBox(); self.image_fit.addItems(['contain','cover'])
        self.theme = QComboBox(); self.theme.addItems(THEMES)
        for name,widget in [('Preferred font size',self.font_size),('Font family',self.font_family),
                            ('Alignment',self.text_align),('Verses per slide',self.max_verses),
                            ('Characters (0 = automatic)',self.max_chars),('Volume %',self.volume),
                            ('Image fit',self.image_fit),('Theme',self.theme)]: form.addRow(name,widget)
        grid(st,[('Background',lambda: self.choose_color('background')),('Text colour',lambda: self.choose_color('text_color')),
                 ('Alert colour',lambda: self.choose_color('alert_color')),('Choose image',self.choose_background_image),
                 ('Clear image',self.clear_background_image),('Save settings',self.save_settings),
                 ('Theme all',self.apply_global_theme),('Theme selected',self.apply_selected_theme)])
        st.addStretch()

        self.build_service_tools(page(),DATA)

        studio = QWidget(); rr = QVBoxLayout(studio); rr.setContentsMargins(8,0,0,0)
        studio.setMinimumWidth(380); self.splitter.addWidget(studio)
        monitors = QHBoxLayout(); rr.addLayout(monitors, 1)
        preview_panel = QVBoxLayout(); monitors.addLayout(preview_panel,1)
        preview_heading = QLabel('PREVIEW'); preview_heading.setStyleSheet('color:#7fb2ff;font-weight:bold;')
        preview_panel.addWidget(preview_heading)
        self.preview_frame = AspectFrame(self.preview); preview_panel.addWidget(self.preview_frame,1)
        self.preview_caption = QLabel('Select a verse or agenda item'); self.preview_caption.setWordWrap(True)
        self.preview_caption.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        preview_panel.addWidget(self.preview_caption)
        live_panel = QVBoxLayout(); monitors.addLayout(live_panel,1)
        self.live_heading = QLabel('LIVE • Not open'); self.live_heading.setStyleSheet('color:#ff8899;font-weight:bold;')
        live_panel.addWidget(self.live_heading)
        self.live_monitor = MonitorLabel('Live output'); self.live_monitor.setAlignment(Qt.AlignCenter)
        self.live_monitor.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Ignored)
        self.live_frame = AspectFrame(self.live_monitor); live_panel.addWidget(self.live_frame,1)
        self.live_caption = QLabel('Audience output'); self.live_caption.setWordWrap(True)
        self.live_caption.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred); live_panel.addWidget(self.live_caption)
        web_controls=QHBoxLayout();rr.addLayout(web_controls)
        self.preview_size=QComboBox();self.preview_size.addItems(['Small preview','Full-screen preview'])
        self.preview_size.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);self.preview_size.setMinimumContentsLength(10)
        self.preview_size.currentIndexChanged.connect(self.change_preview_size);web_controls.addWidget(self.preview_size)
        self.web_size=QComboBox();self.web_size.addItems(['Web page / small video','Fill screen with video'])
        self.web_size.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);self.web_size.setMinimumContentsLength(12)
        self.web_size.currentIndexChanged.connect(self.change_web_size);web_controls.addWidget(self.web_size)
        self.button('Preview → Live',self.send_preview_live,web_controls)
        cards = QHBoxLayout(); rr.addLayout(cards)
        self.current_card = QLabel('CURRENT LIVE\nNothing Live'); self.next_card = QLabel('NEXT\nNothing queued')
        self.current_thumb = MonitorLabel(''); self.next_thumb = MonitorLabel('')
        for card,thumb in ((self.current_card,self.current_thumb),(self.next_card,self.next_thumb)):
            box = QVBoxLayout(); cards.addLayout(box,1)
            thumb.setFixedHeight(44); thumb.setWordWrap(True); thumb.setAlignment(Qt.AlignCenter)
            thumb.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
            thumb.setStyleSheet('background:#080d1a;color:#b7c7de;font-size:12px;padding:4px;'); box.addWidget(thumb)
            card.setWordWrap(True); card.setFixedHeight(44); card.setAlignment(Qt.AlignCenter)
            card.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
            card.setStyleSheet('background:#1b2c44;color:#ffffff;padding:6px;font-size:12px;'); box.addWidget(card)
        rr.addWidget(QLabel('LIVE VIDEO TRANSPORT'))
        transport = QGridLayout(); rr.addLayout(transport)
        entries = [('Play / Pause',self.toggle_video),('Restart',self.restart_video),('Mute',self.toggle_mute),
                   ('Back 10s',lambda: self.seek_video(-10000)),('Forward 10s',lambda: self.seek_video(10000))]
        for i,(name,action) in enumerate(entries):
            button = QPushButton(name); button.clicked.connect(action); transport.addWidget(button,i//3,i%3)
        self.repeat = QComboBox(); self.repeat.addItems(['Off','Repeat once','Loop'])
        self.repeat.currentTextChanged.connect(self.set_repeat); transport.addWidget(self.repeat,1,2)
        self.position = QSlider(Qt.Horizontal); self.position.setRange(0,1000)
        self.position.sliderReleased.connect(self.seek_slider); rr.addWidget(self.position)
        self.video_time = QLabel('00:00 / 00:00'); rr.addWidget(self.video_time)
        self.status = QLabel('Ready. Select a Bible verse, then Show verse Live.')
        self.status.setWordWrap(True); self.status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        outer.addWidget(self.status)
        self.video_timer = QTimer(self); self.video_timer.timeout.connect(self.update_video_position); self.video_timer.start(500)
        self.monitor_timer = QTimer(self); self.monitor_timer.timeout.connect(self.update_live_monitor); self.monitor_timer.start(250)
        self.splitter.setChildrenCollapsible(False); self.splitter.setSizes([400,850])
        self.setMinimumSize(900,560)
        self.setStyleSheet('QMainWindow,QWidget{background:#111c2e;color:#f4f7fc;font-size:13px;} '
            'QPushButton{background:#2455a2;border:0;border-radius:5px;padding:8px 9px;} '
            'QPushButton:hover{background:#3976d3;} QPushButton:pressed{background:#143763;} '
            'QLineEdit,QListWidget,QComboBox,QSpinBox,QTextEdit{background:#1b2a42;color:white;padding:6px;border:1px solid #344663;} '
            'QListWidget::item{padding:6px;} QListWidget::item:selected{background:#3467ac;} '
            'QScrollArea{border:0;} QSplitter::handle{background:#253751;}')

    def update_live_monitor(self):
        item = self.live.current
        if not self.live.isVisible():
            self.live_heading.setText('LIVE • Not open'); self.live_monitor.clear(); self.live_monitor.setText('Live output closed')
        else:
            self.live_heading.setText('LIVE • BLACK' if self.live.black else 'LIVE • Audience')
            if self.live.width() and self.live.height():
                pixmap = self.live.grab()
                if self.live.web_session:
                    web_image=self.live.web_session.snapshot()
                    if not web_image.isNull():
                        painter=QPainter(pixmap);painter.drawPixmap(self.live.content.geometry(),web_image);painter.end()
                        if self.broadcast.isVisible() and not self.broadcast.black:
                            child=self.broadcast.content_layout.itemAt(0).widget() if self.broadcast.content_layout.count() else None
                            if not isinstance(child,MonitorLabel):
                                self.broadcast.clear_content();child=MonitorLabel();child.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Ignored)
                                self.broadcast.content_layout.addWidget(child)
                            child.setPixmap(web_image)
                self.live_monitor.setPixmap(pixmap.scaled(self.live_monitor.size(), Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.live_caption.setText(item['title'] if item else 'Nothing on air')
        if self.preview.current: self.preview_caption.setText(self.preview.current['title'])

    def change_web_size(self,index):
        if self.preview.web_session:self.preview.web_session.fill(index==1)
        elif self.live.web_session:self.live.web_session.fill(index==1)
        else:self.status.setText('Select a web URL first, then choose the video size.')

    def change_preview_size(self,index):
        if index==0:
            if self.preview_popup:self.preview_popup.close()
            return
        popup=QDialog(self);popup.setWindowTitle('ChableszShow full-screen Preview')
        layout=QVBoxLayout(popup);buttons=QHBoxLayout();layout.addLayout(buttons)
        self.button('Return to small Preview',popup.close,buttons)
        self.button('Fill web video',lambda:self.change_web_size(1),buttons)
        self.button('Web page',lambda:self.change_web_size(0),buttons)
        self.button('Send Preview Live',self.send_preview_live,buttons)
        self.preview.setParent(popup);layout.addWidget(self.preview,1)
        self.preview_popup=popup
        if self.preview.web_session:
            self.preview.web_session.settings['tap_enabled']=False
            self.preview.web_session.apply_settings()
        def restore(_):
            self.preview.setParent(self.preview_frame);self.preview.show();self.preview_frame.layout_view()
            self.preview_popup=None
            self.preview_size.blockSignals(True);self.preview_size.setCurrentIndex(0);self.preview_size.blockSignals(False)
            if self.preview.web_session:
                self.preview.web_session.settings['tap_enabled']=True;self.preview.web_session.apply_settings()
            popup.deleteLater()
        popup.finished.connect(restore);popup.showFullScreen()

    def send_preview_live(self):
        item=self.preview.current
        if not item:return
        if self.preview_popup:self.preview_popup.close()
        row=next((i for i,entry in enumerate(self.items) if entry is item),-1)
        if row<0:
            self.items.append(item);row=len(self.items)-1;self.save();self.refresh_list(preview=False)
        self.list.blockSignals(True);self.list.setCurrentRow(row);self.list.blockSignals(False)
        self.go_live()

    def load_settings(self):
        try:
            config = json.loads((DATA / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            config = {}
        self.saved_screen_key = config.get('screen_key')
        self.saved_confidence_screen_key = config.get('confidence_screen_key')
        for stage in (self.preview, self.live, self.broadcast):
            stage.settings.update({k: v for k, v in config.items() if k in stage.settings})
        self.font_size.setValue(int(self.live.settings['font_size']))
        self.font_family.setCurrentText(self.live.settings['font_family'])
        self.text_align.setCurrentText(self.live.settings['text_align'])
        self.max_verses.setValue(int(self.live.settings['max_verses']))
        self.max_chars.setValue(int(self.live.settings['max_chars']))
        self.volume.setValue(int(self.live.settings['volume']))
        self.image_fit.setCurrentText(self.live.settings['image_fit'])
        self.theme.setCurrentText(self.live.settings.get('theme', 'Standard Dark'))
        self.version.setCurrentText(self.live.settings.get('default_version','KJV'))
        self.repeat.setCurrentText(self.live.settings.get('repeat_mode','Off'))
        self.confidence_button.setEnabled(bool(self.live.settings.get('confidence_enabled',True)))
        self.broadcast_button.setEnabled(bool(self.live.settings.get('broadcast_enabled',True)))

    def open_options(self):
        OptionsDialog(self).exec()

    def document_folder(self):
        folder = Path(self.live.settings.get('document_folder', str(Path.home()/'Documents'))).expanduser()
        return str(folder if folder.is_dir() else Path.home())

    def setup_web_player(self):
        if Path(__file__).with_name("BUNDLED_RUNTIME").exists():
            QMessageBox.information(self,"Web player","The web player is included. If it cannot start, repair this installation with its Windows installer.");return
        if os.name=='nt':os.startfile(str(Path(__file__).parent/'Install_Web_Player.bat'))
        else:QMessageBox.information(self,'Web player','Edge WebView2 setup is for Windows. Generic HTML uses Qt on this development platform.')

    def choose_color(self, key):
        color = QColorDialog.getColor(QColor(self.live.settings[key]), self, 'Choose colour')
        if color.isValid():
            for stage in (self.preview, self.live, self.broadcast): stage.settings[key] = color.name()
            if key == 'alert_color':
                for stage in (self.live, self.broadcast):
                    if stage.alert.text(): stage.set_alert(stage.alert.text())
            self.save_settings()

    def save_settings(self):
        for stage in (self.preview, self.live, self.broadcast):
            stage.settings.update(font_size=self.font_size.value(), font_family=self.font_family.currentText(),
                text_align=self.text_align.currentText(), max_verses=self.max_verses.value(),
                max_chars=self.max_chars.value(), volume=self.volume.value(), image_fit=self.image_fit.currentText(),
                theme=self.theme.currentText())
        payload = dict(self.live.settings)
        payload['screen_key'] = getattr(self, 'saved_screen_key', None)
        payload['confidence_screen_key'] = getattr(self, 'saved_confidence_screen_key', None)
        (DATA / 'settings.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        if self.preview.web_session:
            self.preview.web_session.settings['volume']=self.volume.value()/100;self.preview.web_session.apply_settings()
        elif self.preview.current and self.preview.current['kind'] != 'video':
            self.preview.display(self.preview.current)
        if self.live.current and not self.live.black:
            if self.live.web_session:
                self.live.web_session.settings['volume']=self.volume.value()/100;self.live.web_session.apply_settings()
            elif self.live.current['kind'] == 'video' and self.live.audio:
                self.live.audio.setVolume(self.volume.value() / 100)
            elif self.live.current['kind'] != 'video':
                self.live.display(self.live.current)
        if self.broadcast.isVisible() and self.broadcast.current and not self.broadcast.black and self.broadcast.current['kind'] not in ('video','url'):
            self.broadcast.display(self.broadcast.current)
        self.status.setText('Settings saved.')

    def apply_global_theme(self):
        selected = self.theme.currentText()
        for stage in (self.preview, self.live, self.broadcast):
            stage.settings.update(THEMES[selected])
            stage.settings['theme'] = selected
        self.save_settings()

    def apply_selected_theme(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.items):
            QMessageBox.information(self, 'Choose a slide', 'Select an agenda slide first.')
            return
        self.items[row]['theme'] = self.theme.currentText()
        self.save(); self.preview_selection(row)
        self.status.setText('Theme applied to the selected slide.')

    def save_agenda(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save portable service agenda', str(Path(self.document_folder())/'Sunday Service.chablesz'), 'ChableszShow agenda (*.chablesz)')
        if not path: return
        if not path.lower().endswith('.chablesz'): path += '.chablesz'
        try:
            styles = {k:v for k,v in self.live.settings.items() if k in VISUAL_SETTINGS}
            count, media = save_package(path, self.items, styles)
            self.status.setText(f'Saved {count} items and {media} media assets to {Path(path).name}.')
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            QMessageBox.warning(self, 'Agenda not saved', str(error))

    def open_agenda(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open portable service agenda', self.document_folder(), 'ChableszShow agenda (*.chablesz)')
        if not path: return
        if self.items and QMessageBox.question(self, 'Open agenda', 'Replace the current agenda? Save it first if needed.') != QMessageBox.Yes:
            return
        try:
            incoming, styles = load_package_full(path, ASSETS)
            self.items = incoming
            for stage in (self.preview, self.live, self.broadcast):
                stage.settings.update({k:v for k,v in styles.items() if k in VISUAL_SETTINGS and k in stage.settings})
            self.font_size.setValue(int(self.live.settings['font_size']))
            self.font_family.setCurrentText(self.live.settings['font_family'])
            self.text_align.setCurrentText(self.live.settings['text_align'])
            self.max_verses.setValue(int(self.live.settings['max_verses']))
            self.max_chars.setValue(int(self.live.settings['max_chars']))
            self.volume.setValue(int(self.live.settings['volume']))
            self.image_fit.setCurrentText(self.live.settings['image_fit'])
            self.theme.setCurrentText(self.live.settings['theme'])
            payload = dict(self.live.settings)
            payload['screen_key'] = getattr(self, 'saved_screen_key', None)
            payload['confidence_screen_key'] = getattr(self, 'saved_confidence_screen_key', None)
            (DATA / 'settings.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
            self.save()
            self.current_live_index = -1
            self.refresh_list()
            self.update_current_next()
            self.status.setText(f'Opened {Path(path).name}: {len(incoming)} items.')
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            QMessageBox.warning(self, 'Agenda not opened', str(error))

    def choose_background_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose slide background', '', 'Images (*.png *.jpg *.jpeg *.webp)')
        if path:
            for stage in (self.preview, self.live, self.broadcast): stage.settings['background_image'] = path
            self.save_settings()

    def clear_background_image(self):
        for stage in (self.preview, self.live, self.broadcast): stage.settings['background_image'] = ''
        self.save_settings()

    def video_player(self):
        return self.live.player if self.live.current and self.live.current.get('kind') == 'video' else None

    def toggle_video(self):
        if self.live.web_session:self.live.web_session.media('toggle');return
        player = self.video_player()
        if player:
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState: player.pause()
            else: player.play()

    def restart_video(self):
        if self.live.web_session:self.live.web_session.media('restart');return
        player = self.video_player()
        if player:
            self.live.replayed_once = False
            player.setPosition(0); player.play()

    def seek_video(self, delta):
        if self.live.web_session:self.live.web_session.media('seek',delta/1000);return
        player = self.video_player()
        if player: player.setPosition(max(0, min(player.duration(), player.position() + delta)))

    def seek_slider(self):
        player = self.video_player()
        if player and player.duration() > 0:
            player.setPosition(player.duration() * self.position.value() // 1000)

    def toggle_mute(self):
        if self.live.web_session:
            session=self.live.web_session;session.settings['muted']=not session.settings.get('muted',False)
            session.media('mute',session.settings['muted']);return
        if self.video_player() and self.live.audio:
            self.live.audio.setMuted(not self.live.audio.isMuted())
            self.status.setText('Video muted.' if self.live.audio.isMuted() else 'Video sound restored.')

    def set_repeat(self, mode):
        if self.live.web_session:
            self.live.web_session.settings['repeat_mode']=mode;self.live.web_session.media('repeat',mode)
        self.live.repeat_mode = mode
        self.live.replayed_once = False
        self.live.settings['repeat_mode'] = mode

    def update_video_position(self):
        player = self.video_player()
        if not player:
            self.position.setValue(0)
            self.video_time.setText('00:00 / 00:00')
            return
        if not self.position.isSliderDown() and player.duration():
            self.position.setValue(player.position() * 1000 // player.duration())
        def stamp(ms):
            return f'{ms // 60000:02d}:{ms // 1000 % 60:02d}'
        self.video_time.setText(f'{stamp(player.position())} / {stamp(player.duration())}')
        if self.broadcast.isVisible() and self.broadcast.player:
            follower = self.broadcast.player
            if abs(follower.position() - player.position()) > 400:
                follower.setPosition(player.position())
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                if follower.playbackState() != QMediaPlayer.PlaybackState.PlayingState: follower.play()
            elif follower.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                follower.pause()

    def backup_library(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save ChableszShow backup', str(Path(self.document_folder())/'ChableszShow-backup.zip'), 'ZIP (*.zip)')
        if not path: return
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as backup:
            for name in ('playlist.json', 'songs.json', 'songbooks.json', 'settings.json'):
                if (DATA / name).exists(): backup.write(DATA / name, name)
            for bible in BIBLES.glob('*.csv'): backup.write(bible, 'bibles/' + bible.name)
            for slide in MEDIA.glob('*.png'): backup.write(slide, 'slides/' + slide.name)
        self.status.setText('Backup saved: ' + path)

    def restore_library(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Restore ChableszShow backup', self.document_folder(), 'ZIP (*.zip)')
        if not path: return
        answer = QMessageBox.question(self, 'Restore backup', 'Replace the current playlist, songs, settings and Bible libraries with this backup?')
        if answer != QMessageBox.Yes: return
        try:
            with zipfile.ZipFile(path) as backup:
                for member in backup.infolist():
                    name = member.filename
                    if name in ('playlist.json','songs.json','songbooks.json','settings.json'):
                        target = DATA / name
                    elif re.fullmatch(r'bibles/[A-Za-z0-9_-]+\.csv', name):
                        target = BIBLES / Path(name).name
                    elif re.fullmatch(r'slides/[A-Za-z0-9_-]+\.png', name):
                        target = MEDIA / Path(name).name
                    else:
                        continue
                    target.write_bytes(backup.read(member))
            self.load_settings(); self.load_data()
            self.status.setText('Backup restored.')
        except (OSError, zipfile.BadZipFile) as error:
            QMessageBox.warning(self, 'Restore failed', str(error))

    def update_books(self):
        previous = self.book.currentText()
        self.book.blockSignals(True)
        self.book.clear()
        self.book.addItems(list(dict.fromkeys(row['book'] for row in self.verses)))
        if previous: self.book.setCurrentText(previous)
        self.book.blockSignals(False)
        self.update_chapters()

    def update_chapters(self):
        previous = self.chapter.currentText()
        self.chapter.blockSignals(True)
        self.chapter.clear()
        chapters = sorted({int(r['chapter']) for r in self.verses if r['book'] == self.book.currentText() and r['chapter'].isdigit()})
        self.chapter.addItems([str(n) for n in chapters])
        if previous: self.chapter.setCurrentText(previous)
        self.chapter.blockSignals(False)
        self.update_verse_numbers()

    def update_verse_numbers(self):
        self.verse_number.blockSignals(True)
        self.verse_number.clear()
        numbers = sorted({int(r['verse']) for r in self.verses if r['book'] == self.book.currentText() and r['chapter'] == self.chapter.currentText() and r['verse'].isdigit()})
        self.verse_number.addItems([str(n) for n in numbers])
        self.verse_number.blockSignals(False)
        self.select_reference()

    def select_reference(self):
        if self.book.currentText() and self.chapter.currentText() and self.verse_number.currentText():
            self.query.setText(f'{self.book.currentText()} {self.chapter.currentText()}:{self.verse_number.currentText()}')
            self.search_bible()

    def show_chapter(self):
        if self.book.currentText() and self.chapter.currentText():
            self.query.setText(f'{self.book.currentText()} {self.chapter.currentText()}')
            self.search_bible()

    def step_bible(self, delta):
        row = self.results.currentRow()
        if not self.verses or not 0 <= row < len(self.matches): return
        selected = self.matches[row]
        index = next((i for i,r in enumerate(self.verses) if r == selected), -1)
        following = index+delta
        if index < 0 or not 0 <= following < len(self.verses): return
        row = self.verses[following]
        self.sync_reference_widgets(row)
        self.select_reference()

    def sync_reference_widgets(self, row):
        for widget in (self.book,self.chapter,self.verse_number): widget.blockSignals(True)
        self.book.setCurrentText(row['book'])
        self.chapter.clear()
        self.chapter.addItems([str(n) for n in sorted({int(r['chapter']) for r in self.verses if r['book']==row['book']})])
        self.chapter.setCurrentText(row['chapter'])
        self.verse_number.clear()
        self.verse_number.addItems([str(n) for n in sorted({int(r['verse']) for r in self.verses if r['book']==row['book'] and r['chapter']==row['chapter']})])
        self.verse_number.setCurrentText(row['verse'])
        for widget in (self.book,self.chapter,self.verse_number): widget.blockSignals(False)

    def new_songbook(self):
        name, ok = QInputDialog.getText(self, 'New songbook', 'Songbook name')
        if ok and name.strip() and self.songbook.findText(name.strip()) < 0:
            self.songbooks.append(name.strip())
            (DATA / 'songbooks.json').write_text(json.dumps(self.songbooks, ensure_ascii=False), encoding='utf-8')
            self.songbook.addItem(name.strip())
            self.songbook.setCurrentText(name.strip())

    def refresh_songbooks(self):
        previous = self.songbook.currentText()
        self.songbook.blockSignals(True)
        self.songbook.clear()
        self.songbook.addItem('All songbooks')
        default = self.live.settings.get('default_songbook','RCCG Hymn')
        self.songbook.addItems(sorted(set(self.songbooks) | {default} | {s.get('category', 'Other') or 'Other' for s in self.songs}))
        if self.songbook.findText(previous) >= 0: self.songbook.setCurrentText(previous)
        self.songbook.blockSignals(False)

    def refresh_screens(self):
        selected = self.screens.currentData() or getattr(self, 'saved_screen_key', None)
        confidence_selected = self.confidence_screens.currentData() or getattr(self, 'saved_confidence_screen_key', None)
        self.screens.blockSignals(True)
        self.confidence_screens.blockSignals(True)
        self.screens.clear()
        self.confidence_screens.clear()
        self.confidence_screens.addItem('Window on laptop', '')
        for i, screen in enumerate(QGuiApplication.screens()):
            g = screen.geometry()
            key = self.screen_key(screen)
            self.screens.addItem(f'{i + 1}: {screen.name()} ({g.width()}×{g.height()})', key)
            self.confidence_screens.addItem(f'{i + 1}: {screen.name()} ({g.width()}×{g.height()})', key)
        found = self.screens.findData(selected)
        self.screens.setCurrentIndex(found if found >= 0 else min(1, self.screens.count()-1))
        confidence_index = self.confidence_screens.findData(confidence_selected)
        self.confidence_screens.setCurrentIndex(max(0, confidence_index))
        self.screens.blockSignals(False)
        self.confidence_screens.blockSignals(False)
        if self.live.isVisible() and self.active_screen_key and self.active_screen_key not in [self.screen_key(s) for s in QGuiApplication.screens()]:
            self.live.close()
            self.active_screen_key = None
            if self.broadcast.isVisible(): self.broadcast.blackout(True)
            self.status.setText('Projector disconnected. Live output stopped; reconnect and test output.')
            self.refresh_confidence()
            QMessageBox.warning(self, 'Live screen disconnected', 'The selected projector/TV is no longer connected. Reconnect it, refresh screens, and run Test output.')
        if self.confidence.isVisible() and getattr(self, 'active_confidence_screen_key', None) and self.active_confidence_screen_key not in [self.screen_key(s) for s in QGuiApplication.screens()]:
            self.confidence.close()
            self.active_confidence_screen_key = None
            self.status.setText('Confidence monitor disconnected.')

    def screen_key(self, screen):
        return screen.name() + '|' + str(screen.geometry().width()) + 'x' + str(screen.geometry().height())

    def remember_screen(self):
        key = self.screens.currentData()
        if not key: return
        self.saved_screen_key = key
        try:
            config = json.loads((DATA / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            config = dict(self.live.settings)
        config['screen_key'] = key
        (DATA / 'settings.json').write_text(json.dumps(config, indent=2), encoding='utf-8')

    def remember_confidence_screen(self):
        key = self.confidence_screens.currentData() or ''
        self.saved_confidence_screen_key = key
        try:
            config = json.loads((DATA / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            config = dict(self.live.settings)
        config['confidence_screen_key'] = key
        (DATA / 'settings.json').write_text(json.dumps(config, indent=2), encoding='utf-8')

    def open_confidence(self):
        if not self.live.settings.get('confidence_enabled',True):
            QMessageBox.information(self,'Confidence disabled','Enable the confidence module in Options > Modules.')
            return
        key = self.confidence_screens.currentData()
        if key:
            screen = next((s for s in QGuiApplication.screens() if self.screen_key(s) == key), None)
            if not screen:
                QMessageBox.warning(self, 'Screen missing', 'Select an available confidence screen.')
                return
            if key == self.active_screen_key and self.live.isVisible():
                QMessageBox.warning(self, 'Screen in use', 'The audience Live output uses that screen. Choose another screen or Window on laptop.')
                return
            self.confidence.setGeometry(screen.geometry())
            self.confidence.showFullScreen()
            if self.confidence.windowHandle(): self.confidence.windowHandle().setScreen(screen)
            self.confidence.setGeometry(screen.geometry())
            self.active_confidence_screen_key = key
        else:
            self.confidence.showNormal()
            self.confidence.resize(1000, 600)
            self.confidence.move(self.x()+60, self.y()+60)
            self.active_confidence_screen_key = None
        self.refresh_confidence()
        self.activateWindow()

    def open_broadcast(self):
        if not self.live.settings.get('broadcast_enabled',True):
            QMessageBox.information(self,'OBS output disabled','Enable the OBS output module in Options > Modules.')
            return
        if self.broadcast.isVisible():
            self.broadcast.raise_()
            self.activateWindow()
            return
        self.broadcast.showNormal()
        self.broadcast.move(self.x()+30, self.y()+30)
        if self.live.current:
            self.broadcast.display(self.live.current)
            self.broadcast.blackout(self.live.black)
        self.broadcast.set_alert(self.live.alert.text())
        self.broadcast.clock.setText(self.live.clock.text())
        self.broadcast.clock.setVisible(self.live.clock.isVisible() and not self.broadcast.black)
        self.activateWindow()

    def close_extra_outputs(self):
        self.confidence.close()
        self.broadcast.close()
        self.active_confidence_screen_key = None

    def black_outputs(self):
        self.live.blackout(True)
        if self.broadcast.isVisible(): self.broadcast.blackout(True)
        self.refresh_confidence()

    def restore_outputs(self):
        self.live.blackout(False)
        if self.broadcast.isVisible(): self.broadcast.blackout(False)
        self.refresh_confidence()

    def refresh_confidence(self):
        if not self.confidence.isVisible(): return
        current = self.live.current if self.live.isVisible() and not self.live.black else None
        upcoming = self.next_cue()
        if self.seconds is None:
            timer = ''
        else:
            timer = f"{'Elapsed' if self.elapsed else 'Countdown'} {max(0,self.seconds)//60:02d}:{max(0,self.seconds)%60:02d}"
        self.confidence.update_cues(current, upcoming, timer, self.stage_note.text().strip())

    def screen_removed(self):
        QTimer.singleShot(0, self.refresh_screens)

    def test_output(self):
        screens = QGuiApplication.screens()
        if len(screens) < 2:
            QMessageBox.information(self, 'No external display', 'Connect the TV/projector and select Extend these displays in Windows settings, then refresh screens.')
            return
        if self.open_live():
            screen = screens[self.screens.currentIndex()]
            g = screen.geometry()
            self.live.blackout(False)
            self.live.display({'kind':'text','title':'Output test','text':f'CHABLESZSHOW\nOUTPUT TEST\n{screen.name()} • {g.width()} × {g.height()}\n\nPress Go Live to begin'})
            self.current_live_index = -1
            self.update_current_next()
            self.status.setText('Output test sent to the selected display.')

    def open_live(self):
        screens = QGuiApplication.screens()
        idx = self.screens.currentIndex()
        if idx < 0 or idx >= len(screens):
            return False
        if self.confidence.isVisible() and getattr(self, 'active_confidence_screen_key', None) == self.screen_key(screens[idx]):
            QMessageBox.warning(self, 'Screen in use', 'The confidence monitor uses this screen. Choose a different Live screen or close the confidence monitor.')
            return False
        if len(screens) == 1:
            QMessageBox.information(self, 'One display detected', 'Only one display is detected. Set Windows to Extend displays after connecting the projector. Live will open on this screen for testing.')
        self.live.setGeometry(screens[idx].geometry())
        self.live.showFullScreen()
        if self.live.windowHandle(): self.live.windowHandle().setScreen(screens[idx])
        self.live.setGeometry(screens[idx].geometry())
        self.active_screen_key = self.screen_key(screens[idx])
        self.remember_screen()
        self.activateWindow()
        self.status.setText('Live window open. Select an item and press Go Live.')
        return True

    def load_data(self):
        try:
            self.items = json.loads((DATA / 'playlist.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.items = []
        self.refresh_list()
        self.load_version(self.version.currentText())
        try:
            self.songs = json.loads((DATA / 'songs.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.songs = []
        try:
            self.songbooks = json.loads((DATA / 'songbooks.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.songbooks = []
        self.refresh_songbooks()
        if self.songbook.findText(self.live.settings.get('default_songbook','RCCG Hymn')) >= 0:
            self.songbook.setCurrentText(self.live.settings['default_songbook'])
        self.search_songs()

    def load_version(self, version):
        query_before = self.query.text()
        path = BIBLES / (version + '.csv')
        bundled = Path(__file__).parent / 'bundled_bibles' / (version + '.csv')
        if not path.is_file() and bundled.is_file(): path = bundled
        try:
            with path.open(encoding='utf-8-sig', newline='') as f:
                rows = normalize_rows(list(csv.DictReader(f)))
            self.verses = rows
            books = len({r['book'] for r in rows})
            self.version_status.setText(f'{version} • {books} books • {len(rows):,} verses • Offline ready')
        except (OSError, ValueError) as error:
            self.verses = []
            self.version_status.setText(f'{version}: import a permitted Bible CSV to use this translation.'
                if not path.exists() else f'{version}: unable to read the library: {error}')
        self.update_books()
        if query_before: self.query.setText(query_before)
        self.search_bible()

    def save(self):
        target = DATA / 'playlist.json'
        if self.live.settings.get('auto_backup', True) and target.exists():
            shutil.copy2(target, DATA / 'playlist.backup.json')
        fd, temporary = tempfile.mkstemp(prefix='.playlist_', dir=DATA)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(self.items, output, ensure_ascii=False, indent=2)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        self.save_recovery()

    def refresh_list(self,preview=True):
        self.refresh_section_picker()
        row = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for i, item in enumerate(self.items):
            self.list.addItem(item['title'])
            if i == self.current_live_index:
                self.list.item(i).setBackground(QColor('#ae263f'))
                self.list.item(i).setForeground(QColor('#ffffff'))
        if self.items:
            self.list.setCurrentRow(max(0, min(row, len(self.items) - 1)))
        self.list.blockSignals(False)
        if preview and self.items:self.preview_selection(self.list.currentRow())
        self.update_current_next()

    def update_current_next(self):
        if 0 <= self.current_live_index < len(self.items):
            current = self.items[self.current_live_index]
            following = self.next_cue()
        else:
            current = self.live.current if self.live.isVisible() else None
            following = self.items[0] if self.items else None
        def summary(item):
            if not item: return 'Nothing queued'
            line = item.get('text', '').replace('\n', ' ')
            return item['title'] + ('\n' + line[:65] + ('…' if len(line)>65 else '') if line else '')
        def thumbnail(item, widget):
            widget.clear()
            if not item:
                widget.setText('—')
            elif item['kind'] == 'image' and Path(item.get('path','')).is_file():
                pixmap = QPixmap(item['path'])
                widget.setPixmap(pixmap.scaled(260, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            elif item['kind'] == 'video': widget.setText('▶ VIDEO')
            elif item['kind'] == 'url': widget.setText('WEB PAGE')
            else: widget.setText(item.get('text','')[:120])
        thumbnail(current, self.current_thumb)
        thumbnail(following, self.next_thumb)
        self.current_card.setText('CURRENT LIVE\n' + (current.get('title','')[:95] if current else 'Nothing Live'))
        self.next_card.setText('NEXT\n' + (following.get('title','')[:95] if following else 'Nothing queued'))
        self.current_card.setToolTip(summary(current));self.next_card.setToolTip(summary(following))
        self.refresh_confidence()

    def bible_flow_indices(self):
        current=self.live.current or {}
        flow=current.get('bible_flow')
        if not flow:return []
        return [i for i,item in enumerate(self.items) if item.get('bible_flow')==flow]

    def step_live(self,delta):
        indices=self.bible_flow_indices()
        if indices:
            if self.current_live_index not in indices:return
            position=indices.index(self.current_live_index)+delta
            if not 0 <= position < len(indices):
                self.status.setText('End of passage.' if delta>0 else 'Beginning of passage.')
                return
            index=indices[position]
        else:index=self.current_live_index+delta
        if 0 <= index < len(self.items):
            self.list.setCurrentRow(index)
            self.go_live()
            if indices:
                self.status.setText(f'LIVE Bible {indices.index(index)+1}/{len(indices)}: '+self.items[index]['title'])

    def next_live(self):self.step_live(1)

    def previous_live(self):self.step_live(-1)

    def eventFilter(self,watched,event):
        navigation={Qt.Key_Right:1,Qt.Key_Down:1,Qt.Key_PageDown:1,
                    Qt.Key_Left:-1,Qt.Key_Up:-1,Qt.Key_PageUp:-1}
        if event.type() in (QEvent.ShortcutOverride,QEvent.KeyPress) and event.key() in navigation:
            if not event.modifiers() and self.bible_flow_indices() and isinstance(watched,QWidget):
                # Do not steal cursor keys while typing, or keys in settings/browser dialogs.
                owner=watched.window()
                editing=isinstance(watched,(QLineEdit,QTextEdit,QSpinBox,QComboBox))
                if owner in (self,self.live,self.confidence) and not editing:
                    if event.type()==QEvent.KeyPress:self.step_live(navigation[event.key()])
                    event.accept();return True
        return super().eventFilter(watched,event)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add media', self.document_folder(), 'Media (*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.webm *.avi *.pdf *.pptx *.ppt);;All files (*)')
        self.import_paths(paths)

    def open_standalone_video(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Play standalone video', self.document_folder(),
            'Video (*.mp4 *.mov *.mkv *.webm *.avi)')
        if not path: return
        self.standalone_video = StandaloneVideo(path, self.live.settings.get('volume',80))
        self.standalone_video.show()

    def add_presentation(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add PowerPoint', self.document_folder(), 'PowerPoint (*.pptx *.ppt);;PDF (*.pdf)')
        self.import_paths(paths)

    def import_paths(self, paths):
        if not paths:return
        if self.import_worker:
            self.status.setText('A media import is already running. Wait or cancel it first.');return
        worker=MediaImportWorker(paths,MEDIA,presentation_to_pdf,self.live.settings.get('ppt_method','Auto'),self)
        self.import_worker=worker
        worker.progress.connect(self.media_import_progress)
        worker.completed.connect(self.media_import_completed)
        worker.finished.connect(self.media_import_stopped)
        worker.start();self.import_status.show()
        self.status.setText('Import started. Preview and Live remain available.')

    @Slot(int,int,str)
    def media_import_progress(self,index,total,title):
        self.import_status.setText(f'{title} • {index}/{total} slides' if total else title)

    @Slot(list,list)
    def media_import_completed(self,items,errors):
        first=len(self.items);self.items.extend(items);self.save();self.refresh_list(preview=False)
        if items:self.list.setCurrentRow(first)
        message=f'Imported {len(items)} media / slide items.'
        if errors:message+=' '+ '\n'.join(errors)
        self.import_status.setText(message);self.status.setText(message)

    @Slot()
    def media_import_stopped(self):
        worker=self.import_worker;self.import_worker=None
        if worker:worker.deleteLater()
        if self.pending_close:QTimer.singleShot(0,self.close)

    def cancel_media_import(self):
        if self.import_worker:
            self.import_worker.requestInterruption()
            self.status.setText('Cancelling import. Any running PowerPoint conversion will finish or time out first.')

    def add_url(self):
        if not self.live.settings.get('web_enabled', True):
            QMessageBox.information(self,'Web disabled','Enable web URLs in Options > Modules.')
            return
        url, ok = QInputDialog.getText(self, 'Add web page', 'HTTPS URL')
        if ok and url.strip():
            try:url=validated_url(url)
            except ValueError as error:QMessageBox.warning(self,'Invalid URL',str(error));return
            self.items.append({'kind':'url','url':url,'title':'Web: ' + url})
            self.save(); self.refresh_list()

    def add_text(self):
        value, ok = QInputDialog.getMultiLineText(self, 'Display text', 'Message or lyric')
        if ok and value.strip():
            self.items.append({'kind':'text','text':value.strip(),'title':'Text: '+value.splitlines()[0][:50]})
            self.save(); self.refresh_list()

    def move_item(self, delta):
        row = self.list.currentRow()
        dest = row + delta
        if 0 <= row < len(self.items) and 0 <= dest < len(self.items):
            self.items[row], self.items[dest] = self.items[dest], self.items[row]
            if self.current_live_index == row: self.current_live_index = dest
            elif self.current_live_index == dest: self.current_live_index = row
            self.save(); self.refresh_list(); self.list.setCurrentRow(dest)

    def remove_item(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            self.items.pop(row)
            if row == self.current_live_index: self.current_live_index = -1
            elif row < self.current_live_index: self.current_live_index -= 1
            self.save(); self.refresh_list()
            if not self.items: self.preview.clear_content()

    def preview_selection(self, row):
        if 0 <= row < len(self.items):
            self.preview.display(self.items[row])
            self.preview_caption.setText(self.items[row]['title'])

    def go_live(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            item = self.items[row]
            if item['kind']=='camera' and (not self.camera_session.connected or not self.camera_session.frame_received):
                self.status.setText('Camera is disconnected. Connect Camera Preview before sending Live.');return
            if item['kind'] == 'url' and not self.live.settings.get('web_enabled',True):
                QMessageBox.information(self,'Web disabled','Enable web URLs in Options > Modules to show this item.')
                return
            if item['kind'] in ('image','video') and not Path(item['path']).exists():
                QMessageBox.warning(self, 'File missing', 'This media file has moved. Add it again.')
                return
            if not self.live.isVisible() and not self.open_live(): return
            if item['kind']=='url' and self.live.current==item and self.live.web_session and not self.preview.web_session:
                self.live.blackout(False)
                self.status.setText('LIVE: '+item['title'])
                return
            web=None;position=0
            if self.preview.current==item:
                if item['kind']=='url':web=self.preview.take_web_session()
                if item['kind']=='video' and self.preview.player:
                    position=self.preview.player.position();self.preview.player.pause()
            self.live.black=False
            self.live.clock.setVisible(bool(self.live.clock.text()))
            self.live.alert_host.setVisible(bool(self.live.alert.text()))
            self.live.alert.setVisible(bool(self.live.alert.text()))
            self.live.display(item,web_session=web)
            if self.live.player and position:
                self.live.start_position=position;self.live.player.setPosition(position)
            if self.broadcast.isVisible():
                self.broadcast.blackout(False)
                if item['kind']=='url':
                    self.broadcast.display({'kind':'text','text':'Web player mirror…','title':item['title']})
                    self.broadcast.current=item
                else:self.broadcast.display(item)
            self.current_live_index = row
            self.remember_bible_live()
            self.refresh_list(preview=False)
            self.status.setText('LIVE: ' + item['title'])
            self.save_recovery()

    def import_bible(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Import Bible CSV', '', 'CSV files (*.csv)')
        if not name: return
        try:
            with open(name, encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.install_bible(rows)
        except Exception as e:
            QMessageBox.warning(self, 'Bible import failed', str(e))

    def restore_bundled_kjv(self):
        version=self.version.currentText()
        source = Path(__file__).parent/'bundled_bibles'/(version+'.csv')
        target = BIBLES/(version+'.csv')
        if not source.exists():
            self.status.setText('Bundled editions are KJV, ASV, WEB, and YLT. Select one of these first.');return
        try:
            if target.exists(): shutil.copy2(target,BIBLES/(version+'.backup.csv'))
            shutil.copy2(source,target)
            self.load_version(version)
            self.status.setText('Bundled '+version+' selected. Previous imported text is backed up if present.')
        except OSError as error: QMessageBox.warning(self,'Bible restore failed',str(error))

    def install_bible(self, rows, version=None):
        fields = ('book','chapter','verse','text')
        rows = normalize_rows(rows)
        version = version or self.version.currentText()
        target = BIBLES / (version + '.csv')
        temporary = target.with_suffix('.csv.tmp')
        with temporary.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        os.replace(temporary, target)
        if self.version.currentText() == version: self.load_version(version)
        self.status.setText(f'Imported {len(rows)} verses as {version} for offline use.')

    def import_bible_online(self):
        url, ok = QInputDialog.getText(self, 'Import Bible online',
            'Paste an authorized HTTPS CSV URL (book,chapter,verse,text):')
        if not ok or not url.strip(): return
        version = self.version.currentText()
        self.download_csv(url,lambda rows: self.install_bible(rows,version),'Bible '+version)

    def download_csv(self, url, install, description):
        address = QUrl(url.strip())
        if not address.isValid() or address.scheme() != 'https' or not address.host() or address.userName() or address.password():
            QMessageBox.warning(self,'Invalid import URL','Enter a direct public HTTPS link to a UTF-8 CSV file.'); return
        request = QNetworkRequest(address)
        request.setTransferTimeout(20000)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self.network.get(request); self.online_replies.append(reply)
        buffer = bytearray(); oversized = [False]
        def read_data():
            buffer.extend(bytes(reply.readAll()))
            if len(buffer)>16*1024*1024:
                oversized[0]=True; reply.abort()
        def finished():
            try:
                if self.closing: return
                read_data()
                if oversized[0]: raise ValueError('The CSV exceeds the 16 MB import limit.')
                if reply.error() != QNetworkReply.NoError: raise ValueError(reply.errorString())
                rows = list(csv.DictReader(StringIO(buffer.decode('utf-8-sig'))))
                install(rows)
            except Exception as error:
                QMessageBox.warning(self,description+' import failed',str(error))
            finally:
                if reply in self.online_replies: self.online_replies.remove(reply)
                reply.deleteLater()
        reply.readyRead.connect(read_data); reply.finished.connect(finished)
        self.status.setText('Downloading '+description+'… Preview and Live remain available.')

    def search_bible(self):
        self.bible_search_timer.stop()
        q = self.query.text().strip()
        found = search_rows(self.verses,q) if q else []
        self.matches = found[:500]
        self.results.clear()
        for row in self.matches:
            self.results.addItem(f"{row['book']} {row['chapter']}:{row['verse']} — {row['text'][:90]}")
            self.results.item(self.results.count()-1).setToolTip(row['text'])
        if self.matches: self.results.setCurrentRow(0)
        self.bible_count.setText(f'{len(found):,} verses found' + (' • showing first 500' if len(found)>500 else '')
            if found else ('No matching verse. Check the reference or installed translation.' if q else 'Enter a reference or choose a book, chapter, and verse.'))
        if not found and self.preview.current and self.preview.current.get('kind') == 'verse':
            self.preview.display({'kind':'text','text':'No verse selected','title':'No verse selected'})

    def preview_verse(self, row):
        if 0 <= row < len(self.matches):
            self.sync_reference_widgets(self.matches[row])
            item = self.selected_verse()
            if item:
                self.preview.display(item)
                self.preview_caption.setText(item['title'])

    def selected_verse(self):
        i = self.results.currentRow()
        if not 0 <= i < len(self.matches): return None
        row = self.matches[i]
        item=verse_slide(row,self.version.currentText())
        item['passage_reference']=passage_reference(self.matches) or item['passage_reference']
        return item

    def add_verse(self):
        row = self.results.currentRow()
        if not 0 <= row < len(self.matches):
            self.status.setText('Select an installed Bible verse first.'); return False
        slides = paginate([self.matches[row]],self.version.currentText(),1,self.max_chars.value())
        for slide in slides:slide['passage_reference']=passage_reference(self.matches) or slide['passage_reference']
        first = len(self.items); self.items.extend(slides)
        self.save(); self.refresh_list(); self.list.setCurrentRow(first)
        self.status.setText(f'Queued {len(slides)} scripture slide(s).')
        return True

    def add_passage(self):
        if not self.matches: return
        slides = paginate(self.matches,self.version.currentText(),self.max_verses.value(),self.max_chars.value())
        for slide in slides:slide['passage_reference']=passage_reference(self.matches) or slide['passage_reference']
        first = len(self.items)
        self.items.extend(slides)
        self.save(); self.refresh_list(); self.list.setCurrentRow(first)
        self.status.setText(f'Added {len(slides)} Bible slides to the agenda.')

    def search_bible_live(self):
        self.search_bible()
        self.verse_live()

    def verse_live(self):
        row=self.results.currentRow()
        if not 0 <= row < len(self.matches):
            self.status.setText('No matching Bible verse. Live has not changed.');return
        if not self.live.isVisible() and not self.open_live():return
        # Keep every verse in the searched passage, with long verses split safely.
        import uuid
        flow=uuid.uuid4().hex
        slides=[];selected_offset=0
        for index,verse in enumerate(self.matches):
            if index==row:selected_offset=len(slides)
            pages=paginate([verse],self.version.currentText(),1,self.max_chars.value())
            for page in pages:
                page['bible_flow']=flow
                page['passage_reference']=passage_reference(self.matches) or page['passage_reference']
            slides.extend(pages)
        first=len(self.items)
        self.items.extend(slides);self.save();self.refresh_list(preview=False)
        self.list.setCurrentRow(first+selected_offset);self.go_live()
        self.results.setFocus()
        self.status.setText(f'LIVE Bible {selected_offset+1}/{len(slides)}: '+slides[selected_offset]['title']+' • Arrow / Page keys: next or back')


    def save_songs(self):
        (DATA / 'songs.json').write_text(json.dumps(self.songs, ensure_ascii=False, indent=2), encoding='utf-8')

    def search_songs(self):
        q = self.song_search.text().casefold().strip()
        book = self.songbook.currentText()
        self.song_matches = [i for i, song in enumerate(self.songs)
            if (book == 'All songbooks' or song.get('category', 'Other') == book)
            and q in ' '.join(str(song.get(k, '')) for k in ('title','number','category','lyrics')).casefold()][:300]
        self.song_list.clear()
        for i in self.song_matches:
            song = self.songs[i]
            prefix = f"{song.get('number')}  •  " if song.get('number') else ''
            self.song_list.addItem(f"{prefix}{song['title']}  [{song.get('category','Song')}]")

    def song_form(self, original=None):
        song = dict(original or {})
        for key, prompt in [('title','Title'),('category','Category (RCCG Hymn, Nigerian Praise, Nigerian Worship, Other)'),('number','Hymn number (optional)'),('author','Author / source (optional)')]:
            value, ok = QInputDialog.getText(self, 'Song details', prompt, text=str(song.get(key,'')))
            if not ok: return None
            song[key] = value.strip()
        value, ok = QInputDialog.getMultiLineText(self, 'Song lyrics',
            'Separate each projector slide with a blank line. Include repeated chorus where it should appear.', song.get('lyrics',''))
        if not ok: return None
        song['lyrics'] = value.strip()
        if not song['title'] or not song['lyrics']:
            QMessageBox.warning(self, 'Missing details', 'Title and lyrics are required.')
            return None
        return song

    def add_song(self):
        category = self.songbook.currentText()
        song = self.song_form({'category': category if category != 'All songbooks' else self.live.settings.get('default_songbook','RCCG Hymn')})
        if song:
            self.songs.append(song); self.save_songs(); self.refresh_songbooks(); self.search_songs()

    def edit_song(self):
        row = self.song_list.currentRow()
        if not 0 <= row < len(self.song_matches): return
        index = self.song_matches[row]
        song = self.song_form(self.songs[index])
        if song:
            self.songs[index] = song; self.save_songs(); self.refresh_songbooks(); self.search_songs()

    def import_songs(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Import authorized hymn/song CSV', '', 'CSV files (*.csv)')
        if not name: return
        try:
            with open(name, encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.install_songs(rows)
        except Exception as e:
            QMessageBox.warning(self, 'Song import failed', str(e))

    def install_songs(self, rows):
        if not rows or not {'title','lyrics'}.issubset(rows[0]):
            raise ValueError('CSV needs title and lyrics columns; optional category, number, author.')
        valid = []
        category = self.songbook.currentText()
        if category == 'All songbooks': category = self.live.settings.get('default_songbook','RCCG Hymn')
        for row in rows:
            if not str(row.get('title') or '').strip() or not str(row.get('lyrics') or '').strip():
                raise ValueError('Every song needs a title and lyrics.')
            song = {k: str(row.get(k) or '').strip() for k in ('title','lyrics','category','number','author')}
            song['category'] = song['category'] or category
            valid.append(song)
        self.songs.extend(valid)
        self.save_songs(); self.refresh_songbooks(); self.search_songs()
        self.status.setText(f'Imported {len(valid)} songs / hymns for offline use.')

    def import_songs_online(self):
        url, ok = QInputDialog.getText(self, 'Import songs / hymns online',
            'Paste an authorized HTTPS CSV URL (title,lyrics; optional category,number,author):')
        if not ok or not url.strip(): return
        self.download_csv(url,self.install_songs,'Songs / hymns')

    def queue_song(self):
        row = self.song_list.currentRow()
        if not 0 <= row < len(self.song_matches): return
        song = self.songs[self.song_matches[row]]
        slides=song_sections(song,int(self.live.settings.get('song_lines',5)),self.repeat_chorus.isChecked())
        if not slides:self.status.setText('Song has no lyric content.');return
        licence=self.live.settings.get('church_licence','').strip()
        if licence:
            for item in slides:item['attribution']+=' | '+licence
        first=len(self.items);self.items.extend(slides)
        self.save();self.refresh_list();self.list.setCurrentRow(first)
        self.status.setText(f'Added {len(slides)} labelled song slides from {song["title"]}.')

    def show_alert(self):
        self.live.set_alert(self.alert_input.text())
        if self.broadcast.isVisible():
            self.broadcast.set_alert(self.live.alert.text())

    def clear_alert(self):
        self.live.clear_alert()
        self.broadcast.clear_alert()

    def start_countdown(self):
        self.seconds = self.minutes.value() * 60
        self.elapsed = False
        self.tick(display_only=True)

    def start_elapsed(self):
        self.seconds = 0
        self.elapsed = True
        self.tick(display_only=True)

    def hide_timer(self):
        self.seconds = None
        self.live.clock.hide()
        self.live.clock.clear()
        self.broadcast.clock.hide()
        self.broadcast.clock.clear()
        self.refresh_confidence()

    def tick(self, display_only=False):
        if self.seconds is None:
            self.refresh_confidence()
            return
        if not display_only:
            self.seconds += 1 if self.elapsed else -1
        self.live.clock.setText(f"{'Elapsed' if self.elapsed else 'Countdown'}  {max(0,self.seconds)//60:02d}:{max(0,self.seconds)%60:02d}")
        self.live.clock.setVisible(not self.live.black)
        if self.broadcast.isVisible():
            self.broadcast.clock.setText(self.live.clock.text())
            self.broadcast.clock.setVisible(not self.broadcast.black)
        if self.seconds <= 0 and not self.elapsed:
            self.seconds = None
        self.refresh_confidence()

    def closeEvent(self, event):
        if self.update_worker and self.update_worker.isRunning():
            self.pending_update_close=True;self.update_worker.requestInterruption();event.ignore();return
        if self.import_worker:
            self.pending_close=True;self.cancel_media_import();event.ignore();return
        if not self.prepare_feature_close():event.ignore();return
        QApplication.instance().removeEventFilter(self)
        self.save_recovery()
        self.closing = True
        for reply in list(self.online_replies): reply.abort()
        self.live.close()
        self.live.clear_content()
        self.broadcast.close()
        self.broadcast.clear_content()
        self.confidence.close()
        self.preview.clear_content()
        super().closeEvent(event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())
