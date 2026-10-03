"""Sunday service planning, operator shortcuts, readiness and recovery."""
import copy
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QShortcut,QKeySequence,QImageReader
from PySide6.QtWidgets import (QLabel,QLineEdit,QComboBox,QPushButton,QCheckBox,
    QGridLayout,QHBoxLayout,QListWidget,QDialog,QVBoxLayout,QTextEdit,QInputDialog,QFileDialog,QScrollArea)
from bible_core import paginate,passage_reference
from projection import slide_parts

SECTIONS=['Opening prayer','Praise and worship','Hymns','Sunday School','Announcements',
          'Sermon','Offering','Closing prayer']

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.saving_',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def song_sections(song,line_limit=5,repeat=False):
    """Explicit section headings are optional; unlabelled stanzas become verses."""
    chunks=[b.strip() for b in re.split(r'\n\s*\n',song['lyrics']) if b.strip()]
    sections=[];verse=0
    for chunk in chunks:
        lines=chunk.splitlines();heading=lines[0].strip().strip('[](): ').strip()
        if re.fullmatch(r'(verse\s*\d*|chorus|bridge|refrain|intro|outro|pre[- ]chorus)',heading,re.I):
            label=heading.title();lines=lines[1:]
            if not lines:continue
        else:
            verse+=1;label=f'Verse {verse}'
        sections.append((label,lines))
    chorus=next((s for s in sections if s[0] in ('Chorus','Refrain')),None)
    if repeat and chorus:
        arranged=[]
        for i,section in enumerate(sections):
            arranged.append(section)
            if section[0].startswith('Verse') and (i+1==len(sections) or sections[i+1][0] not in ('Chorus','Refrain')):
                arranged.append(chorus)
        sections=arranged
    slides=[];run=uuid.uuid4().hex;limit=max(2,line_limit)
    for label,lines in sections:
        for offset in range(0,len(lines),limit):
            part=offset//limit+1;pages=(len(lines)+limit-1)//limit
            slides.append({'kind':'text','role':'song','text':'\n'.join(lines[offset:offset+limit]),
                'title':song['title']+' • '+label+(f' {part}/{pages}' if pages>1 else ''),
                'song_run':run,'song_section':label,'section_page':part,
                'attribution':' — '.join(x for x in (song['title'],song.get('author','')) if x)})
    return slides

class ServiceMixin:
    def build_service_tools(self,layout,data):
        self.service_data=Path(data);self.recovery_ready=False;self.recovery_pending=None
        self.service_name=QLineEdit('Sunday Service');layout.addWidget(QLabel('Saved service order'));layout.addWidget(self.service_name)
        self.service_picker=QComboBox();layout.addWidget(self.service_picker)
        row=QGridLayout();layout.addLayout(row)
        for i,(title,action) in enumerate([('Sunday outline',self.add_sunday_outline),('Save service',self.save_service),
            ('Load service',self.load_service),('Add section',self.add_service_section),
            ('Readiness check',self.show_readiness),('Resume last output',self.resume_last_output)]):
            b=QPushButton(title);b.clicked.connect(action);row.addWidget(b,i,0)
        self.service_section=QComboBox();self.service_section.addItems(SECTIONS);layout.addWidget(self.service_section)
        b=QPushButton('Tag selected agenda item');b.clicked.connect(self.tag_service_item);layout.addWidget(b)
        b=QPushButton('Jump to section (Preview)');b.clicked.connect(self.jump_service_section);layout.addWidget(b)
        layout.addWidget(QLabel('Quick audience screens'))
        self.quick_welcome=QLineEdit('Welcome to RCCG JESUS THE CHAMPION');layout.addWidget(self.quick_welcome)
        self.quick_theme=QLineEdit('Sunday Service');layout.addWidget(self.quick_theme)
        self.quick_logo=QLineEdit();self.quick_logo.setPlaceholderText('Choose your church logo image');layout.addWidget(self.quick_logo)
        b=QPushButton('Choose logo');b.clicked.connect(self.choose_quick_logo);layout.addWidget(b)
        row=QGridLayout();layout.addLayout(row)
        for i,(title,kind) in enumerate([('Welcome','welcome'),('Service theme','theme'),('Logo only','logo'),('Clear text','clear')]):
            b=QPushButton(title);b.clicked.connect(lambda _,k=kind:self.quick_output(k));row.addWidget(b,i//2,i%2)
        for field in (self.quick_welcome,self.quick_theme,self.quick_logo):field.editingFinished.connect(self.save_operator_preferences)
        self.recovery_label=QLabel('No previous output to resume.');self.recovery_label.setWordWrap(True);layout.addWidget(self.recovery_label)
        layout.addWidget(QLabel('App updates'))
        self.update_url=QLineEdit();self.update_url.setPlaceholderText('Hosted HTTPS release address (not connected yet)');layout.addWidget(self.update_url)
        self.auto_update=QCheckBox('Auto-install verified updates');self.auto_update.setChecked(True);layout.addWidget(self.auto_update)
        self.update_url.editingFinished.connect(self.save_update_preferences)
        self.auto_update.toggled.connect(self.save_update_preferences)
        button=QPushButton('Apply downloaded update');button.clicked.connect(self.apply_downloaded_update);layout.addWidget(button)
        close_button=QPushButton('Close outputs for update');close_button.clicked.connect(self.close_outputs_for_update);layout.addWidget(close_button)
        layout.addStretch();self.refresh_services()

    def init_service_tools(self):
        path=self.service_data/'operator.json'
        try:
            prefs=json.loads(path.read_text(encoding='utf-8'))
            for field,key in [(self.quick_welcome,'welcome'),(self.quick_theme,'theme'),(self.quick_logo,'logo')]:field.setText(prefs.get(key,field.text()))
            self.repeat_chorus.setChecked(prefs.get('repeat_chorus',False))
        except (OSError,ValueError,TypeError):pass
        try:
            candidate=json.loads((self.service_data/'recovery.json').read_text(encoding='utf-8'))
            if isinstance(candidate.get('items'),list) and isinstance(candidate.get('live_item'),dict):
                self.recovery_pending=candidate
                self.recovery_label.setText('Resume available: '+candidate['live_item'].get('title','Last output')+'. Check your projector, then Resume last output.')
        except (OSError,ValueError,TypeError):pass
        from release_info import DEFAULT_UPDATE_URL
        try:updates=json.loads((self.service_data/'updates.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):updates={}
        self.update_url.setText(updates.get('url',DEFAULT_UPDATE_URL))
        self.auto_update.blockSignals(True);self.auto_update.setChecked(updates.get('automatic',True));self.auto_update.blockSignals(False)
        self.recovery_ready=True
        self.recovery_timer=QTimer(self);self.recovery_timer.timeout.connect(self.save_recovery);self.recovery_timer.start(10000)
        self.service_shortcuts=[]
        for key,callback in [('Ctrl+B',self.focus_bible),('Ctrl+Shift+R',self.show_readiness),
            ('Ctrl+Shift+W',lambda:self.quick_output('welcome')),('Ctrl+Shift+T',lambda:self.quick_output('theme')),
            ('Ctrl+Shift+L',lambda:self.quick_output('logo')),('Ctrl+Shift+C',lambda:self.quick_output('clear'))]:
            for owner in (self,self.live,self.confidence):
                self.service_shortcuts.append(QShortcut(QKeySequence(key),owner,activated=callback))

    def close_outputs_for_update(self):
        self.live.blackout(True);self.live.close();self.broadcast.blackout(True);self.close_extra_outputs()
        self.status.setText('Outputs closed. Press Refresh to apply a ready update.')

    def save_update_preferences(self):
        from release_package import https_url
        url=self.update_url.text().strip()
        try:
            if url:https_url(url)
            atomic_json(self.service_data/'updates.json',{'url':url,'automatic':self.auto_update.isChecked()})
        except (ValueError,OSError) as e:self.status.setText(str(e))

    def focus_bible(self):
        self.showNormal();self.raise_();self.activateWindow();self.library_section.setCurrentIndex(0)
        self.query.setFocus();self.query.selectAll()

    def save_operator_preferences(self):
        atomic_json(self.service_data/'operator.json',{'welcome':self.quick_welcome.text(),
            'theme':self.quick_theme.text(),'logo':self.quick_logo.text(),'repeat_chorus':self.repeat_chorus.isChecked()})

    def refresh_services(self):
        previous=self.service_picker.currentData()
        self.service_picker.clear()
        directory=self.service_data/'services';directory.mkdir(exist_ok=True)
        for path in sorted(directory.glob('*.json')):
            try:
                payload=json.loads(path.read_text(encoding='utf-8'))
                self.service_picker.addItem(payload.get('name',path.stem),str(path))
            except (OSError,ValueError,TypeError):continue
        if previous:self.service_picker.setCurrentIndex(max(0,self.service_picker.findData(previous)))

    def refresh_section_picker(self):
        if not hasattr(self,'service_section'):return
        selected=self.service_section.currentText()
        sections=list(dict.fromkeys(SECTIONS+[i['service_section'] for i in self.items if i.get('service_section')]))
        self.service_section.clear();self.service_section.addItems(sections)
        self.service_section.setCurrentText(selected)

    def save_service(self):
        name=self.service_name.text().strip() or 'Sunday Service'
        import hashlib
        path=self.service_data/'services'/(hashlib.sha256(name.casefold().encode()).hexdigest()[:20]+'.json')
        atomic_json(path,{'name':name,'items':self.items})
        self.refresh_services();self.service_picker.setCurrentIndex(self.service_picker.findData(str(path)))
        self.status.setText('Saved service: '+name+'. Local media must remain in place; Save agenda makes a portable copy.')

    def load_service(self):
        name=self.service_picker.currentData()
        if not name:return
        try:
            payload=json.loads(Path(name).read_text(encoding='utf-8'))
            if not isinstance(payload['items'],list) or any(not isinstance(i,dict) or not {'kind','title'}.issubset(i) for i in payload['items']):raise ValueError('Invalid service data')
            if self.items:atomic_json(self.service_data/'services'/'previous_agenda.json',{'name':'Previous agenda (autosaved)','items':self.items})
            self.items=copy.deepcopy(payload['items']);self.current_live_index=-1
            self.service_name.setText(payload['name']);self.save();self.refresh_list();self.refresh_services()
            self.status.setText('Loaded service in Preview; existing Live output is unchanged.')
        except (OSError,ValueError,KeyError,TypeError) as e:self.status.setText('Could not load service: '+str(e))

    def add_sunday_outline(self):
        self.items.extend({'kind':'text','text':section,'title':section,'service_section':section} for section in SECTIONS)
        self.save();self.refresh_list();self.status.setText('Added Sunday outline. Replace each section slide with your prepared content; no output changed.')

    def add_service_section(self):
        text,ok=QInputDialog.getText(self,'Service section','Section name:')
        if ok and text.strip():
            text=text.strip();self.items.append({'kind':'text','text':text,'title':text,'service_section':text})
            self.service_section.addItem(text);self.save();self.refresh_list()

    def tag_service_item(self):
        row=self.list.currentRow()
        if 0<=row<len(self.items):
            self.items[row]['service_section']=self.service_section.currentText();self.save();self.refresh_list()

    def jump_service_section(self):
        section=self.service_section.currentText()
        row=next((i for i,item in enumerate(self.items) if item.get('service_section')==section),None)
        if row is not None:self.list.setCurrentRow(row);self.library_section.setCurrentIndex(1)
        else:self.status.setText('No agenda item tagged '+section+'.')

    def choose_quick_logo(self):
        path,_=QFileDialog.getOpenFileName(self,'Church logo','','Images (*.png *.jpg *.jpeg *.webp)')
        if path:self.quick_logo.setText(path);self.save_operator_preferences()

    def quick_output(self,kind):
        if kind=='logo':
            path=Path(self.quick_logo.text())
            if not path.is_file() or not QImageReader(str(path)).canRead():
                self.status.setText('Choose a readable church logo in Service tools first.');return
            item={'kind':'image','path':str(path),'title':'Church logo'}
        else:
            text={'welcome':self.quick_welcome.text(),'theme':self.quick_theme.text(),'clear':''}[kind]
            item={'kind':'text','text':text,'title':{'welcome':'Church welcome','theme':'Service theme','clear':'Clear text'}[kind]}
            if kind=='clear':item['hide_brand']=True
        self.items.append(item);self.save();self.refresh_list(preview=False);self.list.setCurrentRow(len(self.items)-1);self.go_live()

    def continue_bible(self):
        current=self.live.current or {}
        if current.get('kind')!='verse':self.status.setText('Send a Bible passage Live first.');return
        import re
        m=re.match(r'(.+?) (\d+):(\d+)',current.get('reference',''))
        if not m:return
        version=re.search(r'\(([^)]+)\)',current.get('reference',''))
        if version:self.version.setCurrentText(version[1])
        key=(m[1],m[2],m[3]);index=next((i for i,r in enumerate(self.verses) if (r['book'],str(r['chapter']),str(r['verse']))==key),None)
        if index is None or index+1>=len(self.verses):self.status.setText('No further verse in this installed Bible.');return
        verse=self.verses[index+1];self.query.setText(passage_reference([verse]));self.search_bible_live()

    def show_song_sections(self):
        row=self.song_list.currentRow()
        if not 0<=row<len(self.song_matches):self.status.setText('Select a song first.');return
        count=len(self.items);self.queue_song()
        if len(self.items)==count:return
        run=self.items[self.list.currentRow()].get('song_run')
        indices=[i for i,item in enumerate(self.items) if item.get('song_run')==run]
        dialog=QDialog(self);dialog.setWindowTitle('Song sections — tap to send Live');layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Choose a section/page to send it directly to the audience.'))
        scroll=QScrollArea();scroll.setWidgetResizable(True);layout.addWidget(scroll)
        from PySide6.QtWidgets import QWidget
        content=QWidget();buttons=QVBoxLayout(content);scroll.setWidget(content)
        for index in indices:
            button=QPushButton(self.items[index]['title']);buttons.addWidget(button)
            button.clicked.connect(lambda _,i=index:self.send_song_section(i))
        buttons.addStretch()
        dialog.resize(480,500);dialog.exec()

    def send_song_section(self,index):
        self.list.setCurrentRow(index);self.go_live()

    def readiness_report(self):
        results=[]
        from PySide6.QtGui import QGuiApplication
        all_screens=QGuiApplication.screens()
        results.append(('OK' if len(all_screens)>1 and 0<=self.screens.currentIndex()<len(all_screens) and all_screens[self.screens.currentIndex()] is not QGuiApplication.primaryScreen() else 'CHECK','Projector',
            f'{len(all_screens)} display(s) detected. Selected: {self.screens.currentText()}. Use Test screen; detection cannot confirm the HDMI picture.'))
        results.append(('OK' if self.verses else 'MISSING','Bible',self.version_status.text()))
        for item in self.items:
            if item.get('kind') in ('image','video'):
                path=Path(item.get('path',''))
                readable=path.is_file() and (item['kind']=='video' or QImageReader(str(path)).canRead())
                results.append(('OK' if readable else 'MISSING',item['title'],str(path)))
            elif item.get('kind')=='url':results.append(('CHECK',item['title'],'Online item: test internet, sign-in and playback before service.'))
        for key in ('background_image','bible_background_image','song_background_image'):
            path=self.live.settings.get(key,'')
            if path:results.append(('OK' if QImageReader(path).canRead() else 'MISSING',key,path))
        if any(item.get('kind')=='url' for item in self.items) and os.name=='nt':
            import importlib.util,winreg
            installed=False
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                for prefix in ('Software\\Microsoft\\EdgeUpdate\\Clients\\','Software\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\'):
                    try:
                        with winreg.OpenKey(hive,prefix+'{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') as key:
                            version=winreg.QueryValueEx(key,'pv')[0];installed=installed or bool(version and version!='0.0.0.0')
                    except OSError:pass
            results.append(('OK' if installed and importlib.util.find_spec('webview') else 'MISSING','Web player','Use Install_Web_Player.bat if the browser/runtime is missing. Actual website playback still needs a test.'))
        results.append(('CHECK','Audio',f'Volume {self.live.settings.get("volume",80)}%. Play a local video and check sound on the actual audience device. Preview is muted.'))
        if not self.items:results.append(('CHECK','Agenda','No prepared service items. Load or prepare a service.'))
        return results

    def show_readiness(self):
        dialog=QDialog(self);dialog.setWindowTitle('Pre-service readiness');layout=QVBoxLayout(dialog)
        report=QTextEdit();report.setReadOnly(True);report.setPlainText('\n\n'.join(f'{state} — {name}\n{message}' for state,name,message in self.readiness_report()));layout.addWidget(report)
        row=QHBoxLayout();layout.addLayout(row)
        for name,action in [('Test screen',self.test_output),('Audio settings',self.open_options),('Close',dialog.accept)]:
            b=QPushButton(name);b.clicked.connect(action);row.addWidget(b)
        dialog.resize(640,500);dialog.exec()

    def save_recovery(self):
        if not getattr(self,'recovery_ready',False):return
        current=self.live.current
        if not current:return
        payload={'items':self.items,'live_item':current,'live_index':self.current_live_index,
            'black':self.live.black,'seconds':self.seconds,'elapsed':self.elapsed,'note':self.stage_note.text(),
            'position':self.live.player.position() if self.live.player else 0}
        try:
            atomic_json(self.service_data/'recovery.json',payload)
            self.recovery_pending=copy.deepcopy(payload)
            self.recovery_label.setText('Resume available: '+current.get('title','Last output'))
        except OSError as e:self.status.setText('Recovery save failed: '+str(e))

    def resume_last_output(self):
        saved=self.recovery_pending
        if not saved:self.status.setText('No previous output to resume.');return
        item=copy.deepcopy(saved['live_item'])
        if item.get('kind') in ('image','video') and not Path(item.get('path','')).is_file():
            self.status.setText('Recovery media is missing. Restore its original file first.');return
        if not self.live.isVisible() and not self.open_live():return
        self.items=copy.deepcopy(saved['items']);index=saved.get('live_index',-1)
        if not 0<=index<len(self.items) or self.items[index]!=item:self.items.append(item);index=len(self.items)-1
        self.seconds=saved.get('seconds');self.elapsed=saved.get('elapsed',False);self.stage_note.setText(saved.get('note',''))
        self.save();self.refresh_list(preview=False);self.list.setCurrentRow(index);self.go_live()
        if self.live.player:
            self.live.start_position=saved.get('position',0);self.live.player.setPosition(self.live.start_position)
        if saved.get('black'):self.black_outputs()
        self.status.setText('Resumed last output. Web pages reload; local videos resume near their saved position.')

    def next_cue(self):
        current=self.live.current or {};flow=current.get('bible_flow')
        start=self.current_live_index+1
        for item in self.items[max(0,start):]:
            if not flow or item.get('bible_flow')==flow:return item
        return None
