"""Operator-controlled voice lookup and a separate camera preview panel."""
import copy,json
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QShortcut,QKeySequence,QGuiApplication
from PySide6.QtWidgets import QDialog,QTabWidget,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QComboBox,QLineEdit,QLabel,QPushButton,QCheckBox,QSpinBox,QListWidget,QScrollArea,QFrame
from PySide6.QtMultimedia import QMediaDevices
from camera_capture import CameraSession,CameraView
from offline_voice import VoiceCapture,model_path
from voice_reference import spoken_reference,validated_spoken_rows,remembered_query
from word_finder import find_words
from bible_core import passage_reference
from service_tools import atomic_json

class CaptureToolsMixin:
    def init_capture_tools(self):
        self.capture_dialog=None;self.voice_rows=[];self.voice_version='';self.last_bible_live_item=None;self.feature_shutting=False
        self.camera_session=CameraSession(self);self.voice_capture=VoiceCapture(self)
        for stage in (self.preview,self.live,self.broadcast):stage.camera_session=self.camera_session
        self.camera_session.failed.connect(self.camera_failed)
        self.voice_capture.recognized.connect(self.voice_result)
        self.voice_capture.started.connect(self.voice_started)
        self.voice_capture.state.connect(self.voice_message);self.voice_capture.failed.connect(self.voice_message)
        self.voice_capture.partial.connect(self.voice_partial)
        self.voice_capture.stopped.connect(self.voice_stopped)
        try:self.capture_settings=json.loads((self.capture_data_path()/'capture-settings.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):self.capture_settings={}
        QShortcut(QKeySequence('Ctrl+Shift+V'),self,activated=self.toggle_voice)
        QShortcut(QKeySequence('Ctrl+Shift+C'),self,activated=self.camera_live)
        QShortcut(QKeySequence('Ctrl+Shift+B'),self,activated=self.return_bible_live)
        self.feature_close_timer=QTimer(self);self.feature_close_timer.setInterval(100);self.feature_close_timer.timeout.connect(self.finish_feature_close)
    def capture_data_path(self):
        import app
        return app.DATA
    def open_capture_tools(self):
        if self.capture_dialog is None:self.build_capture_dialog()
        self.capture_dialog.show();self.capture_dialog.raise_();self.capture_dialog.activateWindow()
    def build_capture_dialog(self):
        dialog=QDialog(self);dialog.setWindowTitle('ChableszShow — Voice Bible & Camera')
        available=QGuiApplication.primaryScreen().availableGeometry();dialog.resize(min(790,available.width()-40),min(660,available.height()-60))
        outer=QVBoxLayout(dialog);tabs=QTabWidget();outer.addWidget(tabs);self.capture_dialog=dialog
        voice=QWidget();layout=QVBoxLayout(voice);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame);scroll.setWidget(voice);tabs.addTab(scroll,'Voice Bible · offline')
        hint=QLabel('Press Listen and repeat a reference, for example “John chapter two verses three to eight”. Pause to finish, or press Listen again. Remembered words show passages to choose.');hint.setWordWrap(True);layout.addWidget(hint)
        form=QFormLayout();layout.addLayout(form);self.voice_input=QComboBox();form.addRow('Microphone',self.voice_input)
        self.auto_voice_live=QCheckBox('Send a complete, confident verse reference Live automatically');self.auto_voice_live.setChecked(bool(self.capture_settings.get('auto_live',True)));form.addRow(self.auto_voice_live)
        self.voice_silence=QSpinBox();self.voice_silence.setRange(0,5000);self.voice_silence.setSingleStep(100);self.voice_silence.setValue(int(self.capture_settings.get('silence_ms',1200)));self.voice_silence.setSuffix(' ms');form.addRow('Pause to finish (0 = manual)',self.voice_silence)
        row=QHBoxLayout();layout.addLayout(row)
        self.listen_button=QPushButton('Listen · Ctrl+Shift+V');row.addWidget(self.listen_button);self.listen_button.clicked.connect(self.toggle_voice)
        cancel=QPushButton('Cancel voice');row.addWidget(cancel);cancel.clicked.connect(self.cancel_voice)
        refresh=QPushButton('Refresh devices');row.addWidget(refresh);refresh.clicked.connect(self.refresh_capture_devices)
        self.voice_state=QLabel('Offline English model ready.' if model_path() else 'Offline voice model missing; typing still works.');self.voice_state.setWordWrap(True);layout.addWidget(self.voice_state)
        self.voice_text=QLineEdit();self.voice_text.setPlaceholderText('Recognized words appear here; edit them if needed');layout.addWidget(self.voice_text)
        self.voice_text.returnPressed.connect(lambda:self.voice_lookup(self.voice_text.text(),0,False))
        self.voice_results=QListWidget();self.voice_results.setWordWrap(True);self.voice_results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);layout.addWidget(self.voice_results,1)
        self.voice_results.currentRowChanged.connect(self.voice_preview_selected)
        self.voice_results.itemActivated.connect(lambda _:self.send_voice_selected())
        row=QHBoxLayout();layout.addLayout(row)
        search=QPushButton('Search / Preview');search.clicked.connect(lambda:self.voice_lookup(self.voice_text.text(),0,False));row.addWidget(search)
        self.voice_send=QPushButton('Send selected Live');self.voice_send.clicked.connect(self.send_voice_selected);self.voice_send.setEnabled(False);row.addWidget(self.voice_send)
        camera=QWidget();layout=QVBoxLayout(camera);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame);scroll.setWidget(camera);tabs.addTab(scroll,'Camera Preview')
        hint=QLabel('Camera starts in this separate Preview. Phone: use a camera app that provides an MJPEG/RTSP stream, or a phone webcam driver. Use the same local Wi-Fi/hotspot; internet is not required for a local stream. Camera audio is off.');hint.setWordWrap(True);layout.addWidget(hint)
        form=QFormLayout();layout.addLayout(form)
        self.camera_mode=QComboBox();self.camera_mode.addItems(['USB / webcam','Phone · MJPEG','Phone · RTSP / video stream']);form.addRow('Source',self.camera_mode)
        self.camera_device=QComboBox();form.addRow('Connected camera',self.camera_device)
        self.camera_stream=QLineEdit();self.camera_stream.setPlaceholderText('Example: http://192.168.1.5:8080/video');self.camera_stream.setText(str(self.capture_settings.get('camera_url','')));form.addRow('Phone stream URL',self.camera_stream)
        row=QHBoxLayout();layout.addLayout(row)
        connect=QPushButton('Connect Preview');connect.clicked.connect(self.connect_camera);row.addWidget(connect)
        disconnect=QPushButton('Disconnect');disconnect.clicked.connect(self.disconnect_camera);row.addWidget(disconnect)
        devices=QPushButton('Refresh devices');devices.clicked.connect(self.refresh_capture_devices);row.addWidget(devices)
        self.camera_state=QLabel('Camera disconnected. Audience output is unchanged.');self.camera_state.setWordWrap(True);layout.addWidget(self.camera_state)
        self.camera_view=CameraView(self.camera_session);self.camera_view.setMinimumHeight(220);layout.addWidget(self.camera_view,1)
        self.camera_session.status.connect(self.camera_message)
        row=QHBoxLayout();layout.addLayout(row)
        self.camera_send=QPushButton('Send Camera Live · Ctrl+Shift+C');self.camera_send.clicked.connect(self.camera_live);row.addWidget(self.camera_send)
        self.camera_bible=QPushButton('Return to Bible · Ctrl+Shift+B');self.camera_bible.clicked.connect(self.return_bible_live);row.addWidget(self.camera_bible)
        blackout=QPushButton('Black');blackout.clicked.connect(self.black_outputs);row.addWidget(blackout)
        self.camera_session.frame.connect(lambda _:self.update_capture_actions())
        row=QHBoxLayout();outer.addLayout(row)
        save=QPushButton('Save voice / camera settings');save.clicked.connect(self.save_capture_settings);row.addWidget(save)
        close=QPushButton('Close panel');close.clicked.connect(dialog.close);row.addWidget(close)
        for b in dialog.findChildren(QPushButton):b.setAutoDefault(False)
        dialog.finished.connect(lambda _:self.cancel_voice() if self.voice_capture.listening or self.voice_capture.loading else None)
        self.refresh_capture_devices();self.update_capture_actions()
    def refresh_capture_devices(self):
        if self.capture_dialog is None:return
        old=self.voice_input.currentData();self.voice_input.clear();self.voice_input.addItem('System default microphone','')
        for device in QMediaDevices.audioInputs():self.voice_input.addItem(device.description(),bytes(device.id()).hex())
        chosen=old if old is not None else self.capture_settings.get('microphone','');i=self.voice_input.findData(chosen)
        if i>=0:self.voice_input.setCurrentIndex(i)
        old=self.camera_device.currentData();self.camera_device.clear()
        for device in QMediaDevices.videoInputs():self.camera_device.addItem(device.description(),bytes(device.id()).hex())
        chosen=old or self.capture_settings.get('camera_device','');i=self.camera_device.findData(chosen)
        if i>=0:self.camera_device.setCurrentIndex(i)
    def save_capture_settings(self):
        self.capture_settings={'auto_live':self.auto_voice_live.isChecked(),'silence_ms':self.voice_silence.value(),'microphone':self.voice_input.currentData(),'camera_device':self.camera_device.currentData(),'camera_url':self.camera_stream.text().strip()}
        atomic_json(self.capture_data_path()/'capture-settings.json',self.capture_settings);self.status.setText('Voice and camera settings saved on this laptop.')
    def voice_message(self,message):
        self.status.setText(message)
        if self.capture_dialog:self.voice_state.setText(message)
    def voice_partial(self,text):
        if self.capture_dialog:self.voice_text.setText(text)
    def voice_started(self):
        self.voice_version=self.version.currentText()
        if self.capture_dialog:self.listen_button.setText('Finish reference');self.voice_text.clear()
    def voice_stopped(self):
        if self.capture_dialog:self.listen_button.setText('Listen · Ctrl+Shift+V')
    def cancel_voice(self):self.voice_capture.cancel();self.voice_message('Voice cancelled. Live is unchanged.')
    def toggle_voice(self):
        if self.feature_shutting:return
        self.open_capture_tools()
        if self.voice_capture.listening or self.voice_capture.loading:self.voice_capture.finish()
        else:self.voice_capture.begin(self.voice_input.currentData() or '',self.voice_silence.value())
    def voice_result(self,text,confidence):
        if self.feature_shutting:return
        if self.capture_dialog:self.voice_text.setText(text)
        if self.voice_version!=self.version.currentText():
            self.voice_message('Bible translation changed while listening. Search again before sending Live.');return
        self.voice_lookup(text,confidence,True)
    def voice_lookup(self,text,confidence=0,allow_auto=False):
        if self.capture_dialog is None:self.build_capture_dialog()
        self.voice_rows=[];self.voice_results.clear();self.voice_send.setEnabled(False)
        if not self.verses:self.voice_message('Select an installed Bible translation first. Live is unchanged.');return
        if not text.strip():self.voice_message('No words were recognized. Move closer to the microphone and try again.');return
        reference=spoken_reference(text)
        if reference:
            found=validated_spoken_rows(self.verses,reference)
            if not found:self.voice_message('That complete reference is not present in this translation. Check the recognized words; Live is unchanged.');return
        else:
            found=find_words(self.verses,remembered_query(text),'All words')
        self.voice_rows=found[:500];self.voice_version=self.version.currentText()
        for row in self.voice_rows:self.voice_results.addItem(passage_reference([row])+' — '+row['text'])
        if self.voice_rows:self.voice_results.setCurrentRow(0)
        if not found:self.voice_message('No passage matched. Edit the words or try fewer remembered words. Live is unchanged.');return
        if reference:
            self.query.setText(reference.query);self.search_bible()
            if allow_auto and reference.first is not None and confidence>=.72 and self.auto_voice_live.isChecked():
                self.verse_live();self.voice_message('Voice Live: '+reference.query+' · '+self.version.currentText());return
            self.voice_message('Prepared '+reference.query+'. Check it, then Send selected Live.'+(' Recognition needs confirmation.' if allow_auto and confidence<.72 else ''))
        else:self.voice_message(str(len(found))+' passages match those words. Choose the correct passage, then Send selected Live.')
    def voice_preview_selected(self,index):
        self.voice_send.setEnabled(0<=index<len(self.voice_rows))
        if not 0<=index<len(self.voice_rows):return
        if self.voice_version!=self.version.currentText():return
        row=self.voice_rows[index];reference=spoken_reference(self.voice_text.text())
        query=reference.query if reference and validated_spoken_rows(self.verses,reference) else passage_reference([row])
        self.query.setText(query);self.search_bible()
        selected=next((i for i,r in enumerate(self.matches) if r['book']==row['book'] and r['chapter']==row['chapter'] and r['verse']==row['verse']),0)
        self.results.setCurrentRow(selected)
    def send_voice_selected(self):
        index=self.voice_results.currentRow()
        if not 0<=index<len(self.voice_rows):return
        if self.voice_version!=self.version.currentText():self.voice_message('Translation changed. Search again before sending Live.');return
        self.voice_preview_selected(index);self.verse_live()
    def camera_message(self,message):
        self.status.setText(message)
        if self.capture_dialog:self.camera_state.setText(message)
        self.update_capture_actions()
    def camera_failed(self,message):
        if self.live.current and self.live.current.get('kind')=='camera':self.black_outputs()
        self.camera_message(message+' Audience camera output is black; you can return to Bible.')
    def connect_camera(self):
        if self.capture_dialog is None:self.build_capture_dialog()
        if self.live.current and self.live.current.get('kind')=='camera':self.black_outputs()
        try:
            if self.camera_mode.currentIndex()==0:self.camera_session.start_usb(self.camera_device.currentData())
            else:self.camera_session.start_url(self.camera_stream.text(),'MJPEG' if self.camera_mode.currentIndex()==1 else 'RTSP')
        except ValueError as e:self.camera_message(str(e))
    def disconnect_camera(self):
        if self.live.current and self.live.current.get('kind')=='camera':self.black_outputs()
        self.camera_session.stop();self.camera_message('Camera disconnected. Reconnect Preview before sending it Live.')
    def update_capture_actions(self):
        if self.capture_dialog:
            self.camera_send.setEnabled(self.camera_session.connected and self.camera_session.frame_received)
            self.camera_bible.setEnabled(self.last_bible_live_item is not None)
    def remember_bible_live(self):
        if self.live.current and self.live.current.get('kind')=='verse':self.last_bible_live_item=copy.deepcopy(self.live.current)
        self.update_capture_actions()
    def camera_live(self):
        if not self.camera_session.connected or not self.camera_session.frame_received:
            self.status.setText('Connect a camera and wait for its Preview before sending Live.');return
        self.remember_bible_live()
        index=next((i for i,item in enumerate(self.items) if item.get('kind')=='camera'),-1)
        if index<0:self.items.append({'kind':'camera','title':'Live camera'});index=len(self.items)-1
        self.save();self.refresh_list(preview=False);self.list.setCurrentRow(index);self.go_live()
    def return_bible_live(self):
        if not self.last_bible_live_item:self.status.setText('No earlier Bible passage is available. Search and send a passage first.');return
        item=self.last_bible_live_item
        index=next((i for i,entry in enumerate(self.items) if entry==item),-1)
        if index<0:self.items.append(copy.deepcopy(item));index=len(self.items)-1;self.save();self.refresh_list(preview=False)
        self.list.setCurrentRow(index);self.go_live()
    def prepare_feature_close(self):
        self.feature_shutting=True;self.camera_session.stop()
        if self.capture_dialog:self.capture_dialog.hide()
        if not self.voice_capture.shutdown():self.feature_close_timer.start();return False
        self.feature_close_timer.stop();return True
    def finish_feature_close(self):
        if not self.voice_capture.worker or not self.voice_capture.worker.isRunning():self.feature_close_timer.stop();self.close()
