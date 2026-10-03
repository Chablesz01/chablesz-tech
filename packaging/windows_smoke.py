import os, sys, tempfile
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineSettings
# Use isolated user data for validation, never the operator's existing library.
scratch=tempfile.TemporaryDirectory();os.environ['APPDATA']=scratch.name
import app
application=QApplication(sys.argv)
window=app.App();window.show()
assert app.VERSION==17
assert len(window.verses)>30000
assert not window.auto_update.isEnabled()
window.build_capture_dialog()
assert window.camera_session is not None
assert window.voice_capture is not None
from offline_voice import model_path
assert model_path() is not None
from vosk import Model,KaldiRecognizer
model=Model(str(model_path()))
recognizer=KaldiRecognizer(model,16000)
assert recognizer is not None
QTimer.singleShot(1500,window.close)
QTimer.singleShot(2500,application.quit)
application.exec()
print('Windows startup and bundled Bible checks passed')
scratch.cleanup()
