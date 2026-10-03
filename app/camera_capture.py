"""Shared camera preview/output with USB, MJPEG and RTSP inputs. Camera audio stays off."""
import time
from urllib.parse import urlsplit
from PySide6.QtCore import QObject,Signal,Slot,QUrl,Qt,QSize,QTimer
from PySide6.QtGui import QImage,QPainter,QColor
from PySide6.QtWidgets import QWidget,QSizePolicy
from PySide6.QtMultimedia import QCamera,QMediaCaptureSession,QMediaDevices,QMediaPlayer,QVideoSink
from PySide6.QtNetwork import QNetworkAccessManager,QNetworkRequest,QNetworkReply

class JpegStream:
    def __init__(self,limit=8*1024*1024):self.buffer=bytearray();self.limit=limit
    def feed(self,data):
        self.buffer.extend(data);images=[]
        while True:
            start=self.buffer.find(b'\xff\xd8')
            if start<0:
                self.buffer=self.buffer[-1:] if self.buffer[-1:]==b'\xff' else bytearray();break
            if start:del self.buffer[:start]
            end=self.buffer.find(b'\xff\xd9',2)
            if end<0:break
            images.append(bytes(self.buffer[:end+2]));del self.buffer[:end+2]
        if len(self.buffer)>self.limit:
            self.buffer.clear();raise ValueError('Camera frame exceeds the size limit.')
        return images

def camera_url(value,mode):
    value=value.strip();p=urlsplit(value)
    allowed=('http','https') if mode=='MJPEG' else ('rtsp','rtsps','http','https')
    if p.scheme.lower() not in allowed or not p.hostname:raise ValueError('Enter a complete '+('HTTP/HTTPS camera stream URL.' if mode=='MJPEG' else 'RTSP/HTTP camera stream URL.'))
    if p.username or p.password:raise ValueError('Use a camera stream without embedded login credentials on your trusted local network.')
    try:p.port
    except ValueError:raise ValueError('Camera URL has an invalid port.')
    if p.fragment:raise ValueError('A camera stream URL cannot include a page fragment.')
    return value

class CameraSession(QObject):
    frame=Signal(QImage);status=Signal(str);failed=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.image=QImage();self.camera=None;self.capture=None;self.player=None;self.reply=None;self.sink=None
        self.network=QNetworkAccessManager(self);self.parser=JpegStream();self.last_frame=0;self.connected=False;self.generation=0
        self.watchdog=QTimer(self);self.watchdog.setInterval(500);self.watchdog.timeout.connect(self.check_frames)
        self.started_at=0;self.last_received=0;self.frame_received=False
    def check_frames(self):
        if self.connected and time.monotonic()-max(self.started_at,self.last_received)>8:
            self.fail('Camera frames stopped. Check the phone stream or camera connection.')
    def fail(self,message):
        self.stop();self.failed.emit(message)
    def start_usb(self,device_id):
        self.stop();devices=QMediaDevices.videoInputs();device=next((d for d in devices if bytes(d.id()).hex()==device_id),None)
        if device is None:raise ValueError('The selected webcam is no longer connected.')
        self.camera=QCamera(device,self);self.capture=QMediaCaptureSession(self);self.capture.setCamera(self.camera)
        self.sink=QVideoSink(self);self.capture.setVideoSink(self.sink);self.sink.videoFrameChanged.connect(self.video_frame)
        generation=self.generation
        self.camera.errorOccurred.connect(lambda *_:self.fail('Webcam failed. Check Windows camera permissions and close other camera apps.') if self.connected and self.generation==generation else None)
        self.begin();self.camera.start();self.status.emit('Starting webcam preview…')
    def begin(self):
        self.connected=True;self.frame_received=False;self.started_at=time.monotonic();self.last_received=0;self.last_frame=0;self.watchdog.start()
    def start_url(self,value,mode):
        url=camera_url(value,mode);self.stop();self.begin()
        if mode=='MJPEG':
            request=QNetworkRequest(QUrl(url));request.setTransferTimeout(6000)
            request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,QNetworkRequest.NoLessSafeRedirectPolicy)
            self.reply=self.network.get(request);reply=self.reply
            reply.readyRead.connect(lambda:self.read_mjpeg(reply))
            reply.errorOccurred.connect(lambda _:self.fail('Phone stream could not be read. Check the URL and local Wi-Fi/hotspot.') if self.reply is reply else None)
            reply.finished.connect(lambda:self.fail('Phone stream ended. Reconnect to continue.') if self.reply is reply else None)
        else:
            self.player=QMediaPlayer(self);self.sink=QVideoSink(self);self.player.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self.video_frame)
            generation=self.generation
            self.player.errorOccurred.connect(lambda *_:self.fail('This stream could not be played. Check its format or try MJPEG.') if self.connected and self.generation==generation else None)
            self.player.setSource(QUrl(url));self.player.play()
        self.status.emit('Connecting camera stream…')
    def read_mjpeg(self,reply):
        if reply is not self.reply:return
        try:
            frames=self.parser.feed(bytes(reply.readAll()))
            if frames:
                image=QImage.fromData(frames[-1],'JPEG')
                if not image.isNull():self.accept_frame(image)
        except ValueError as e:self.fail(str(e))
    def video_frame(self,frame):
        if not self.connected or time.monotonic()-self.last_frame<.05:return
        image=frame.toImage()
        if not image.isNull():self.accept_frame(image)
    def accept_frame(self,image):
        if not self.connected or image.isNull():return
        now=time.monotonic();self.last_received=now
        if now-self.last_frame<.05:return
        self.last_frame=now
        if image.width()>1920 or image.height()>1080:image=image.scaled(1920,1080,Qt.KeepAspectRatio,Qt.FastTransformation)
        self.image=image;self.frame.emit(image)
        if not self.frame_received:self.frame_received=True;self.status.emit('Camera ready in Preview. Send Camera Live when ready.')
    def stop(self):
        self.generation+=1;self.connected=False;self.frame_received=False;self.watchdog.stop()
        reply=self.reply;self.reply=None
        if reply:reply.abort();reply.deleteLater()
        if self.camera:self.camera.stop();self.camera.deleteLater();self.camera=None
        if self.capture:self.capture.setCamera(None);self.capture.setVideoSink(None);self.capture.deleteLater();self.capture=None
        if self.player:self.player.stop();self.player.setVideoSink(None);self.player.deleteLater();self.player=None
        if self.sink:self.sink.deleteLater();self.sink=None
        self.parser=JpegStream();self.image=QImage();self.frame.emit(self.image)

class CameraView(QWidget):
    def __init__(self,session,parent=None):
        super().__init__(parent);self.image=session.image;self.session=session
        self.setMinimumSize(0,0);self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Ignored)
        session.frame.connect(self.set_image)
    def minimumSizeHint(self):return QSize(0,0)
    @Slot(QImage)
    def set_image(self,image):self.image=image;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#000000'))
        if self.image.isNull():
            p.setPen(QColor('#c4d4e7'));p.drawText(self.rect(),Qt.AlignCenter,'Camera Preview — connect a source');return
        size=self.image.size();size.scale(self.size(),Qt.KeepAspectRatio)
        from PySide6.QtCore import QRect
        rect=QRect((self.width()-size.width())//2,(self.height()-size.height())//2,size.width(),size.height())
        p.drawImage(rect,self.image)
