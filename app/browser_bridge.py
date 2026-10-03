"""Own a supported Windows browser and move its existing viewport between stages."""
import base64
import json
import os
from pathlib import Path
import sys
import time
from PySide6.QtCore import QObject,QProcess,QTimer,Signal,QUrl
from PySide6.QtGui import QWindow,QPixmap
from PySide6.QtWidgets import QWidget
from web_media import media_script,delayed_media_script

class BrowserSession(QObject):
    tapped=Signal()
    message=Signal(str)
    def __init__(self,url,profile,parent=None):
        super().__init__(parent)
        self.url=url;self.stage=None;self.widget=None;self.image=QPixmap()
        self.settings={'preview':True,'fill':False,'volume':.8,'loop':False}
        self.buffer=bytearray();self.pending_snapshot=False;self.last_snapshot=0;self.closing=False
        self.process=None
        if os.name=='nt':
            self.process=QProcess(self)
            self.process.readyReadStandardOutput.connect(self.read_output)
            self.process.readyReadStandardError.connect(lambda:self.process.readAllStandardError())
            self.process.errorOccurred.connect(lambda _:self.message.emit('Web player could not start. Run Install_Web_Player.bat.'))
            self.process.setProgram(sys.executable)
            self.process.setArguments([str(Path(__file__).with_name('browser_host.py')),url,str(profile)])
            self.process.start()
        else:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebEngineCore import QWebEngineSettings
            self.widget=QWebEngineView()
            self.widget.settings().setAttribute(QWebEngineSettings.FullScreenSupportEnabled,True)
            self.widget.loadFinished.connect(lambda _:self.apply_settings())
            self.widget.load(QUrl(url))

    def attach(self,stage):
        previous=self.stage
        if previous and previous is not stage:
            if self.widget:previous.content_layout.removeWidget(self.widget)
            previous.web_session=None
            previous.show_web_placeholder('This prepared web player is now on Live.')
        self.stage=stage;stage.web_session=self
        self.setParent(stage)
        if self.widget:
            self.widget.setParent(stage.content)
            stage.clear_placeholder()
            stage.content_layout.addWidget(self.widget);self.widget.show()
        self.settings['preview']=stage.is_preview
        self.settings['volume']=stage.settings.get('volume',80)/100
        self.settings['repeat_mode']=stage.repeat_mode
        self.apply_settings()

    def read_output(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        while b'\n' in self.buffer:
            line,_,remaining=self.buffer.partition(b'\n');self.buffer=bytearray(remaining)
            try:event=json.loads(line)
            except (ValueError,UnicodeDecodeError):continue
            kind=event.get('event')
            if kind=='ready' and not self.closing:
                self.foreign=QWindow.fromWinId(int(event['hwnd']))
                self.widget=QWidget.createWindowContainer(self.foreign)
                if self.stage:self.attach(self.stage)
            elif kind=='tap':self.tapped.emit()
            elif kind=='snapshot':
                self.pending_snapshot=False
                self.image.loadFromData(base64.b64decode(event['image']))
            elif kind in ('snapshot_pending','warning'):
                self.pending_snapshot=False
            elif kind=='error':self.message.emit(event.get('message','Web player failed'))

    def send(self,command):
        if self.process and self.process.state()!=QProcess.NotRunning:
            self.process.write((json.dumps(command)+'\n').encode())

    def apply_settings(self):
        if self.process:self.send({'command':'settings','values':self.settings})
        elif self.widget:
            page=self.widget.page()
            page.setAudioMuted(self.settings['preview'])
            page.runJavaScript(media_script('fill' if self.settings['fill'] else 'page'))
            page.runJavaScript(media_script('volume',self.settings['volume']))
            page.runJavaScript(media_script('repeat',self.settings.get('repeat_mode','Off')))
            page.runJavaScript(delayed_media_script(self.settings))

    def media(self,action,value=None):
        if self.process:self.send({'command':'media','action':action,'value':value})
        elif self.widget:self.widget.page().runJavaScript(media_script(action,value))

    def fill(self,enabled):
        self.settings['fill']=enabled;self.apply_settings()

    def snapshot(self):
        if self.process:
            if not self.pending_snapshot and time.monotonic()-self.last_snapshot>=1:
                self.pending_snapshot=True;self.last_snapshot=time.monotonic();self.send({'command':'snapshot'})
        elif self.widget:self.image=self.widget.grab()
        return self.image

    def close(self):
        self.closing=True
        if self.process:
            self.send({'command':'close'})
            process=self.process
            def stop_if_running():
                try:
                    if process.state()!=QProcess.NotRunning:process.kill()
                except RuntimeError:pass
            QTimer.singleShot(1500,stop_if_running)
        if self.widget:self.widget.hide();self.widget.deleteLater();self.widget=None
        self.stage=None
