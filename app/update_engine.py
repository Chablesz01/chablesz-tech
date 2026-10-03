"""HTTPS release downloads, integrity checks and a constrained package format."""
import hashlib
import json
import re
import ssl
import urllib.request
import zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit
from PySide6.QtCore import QThread,Signal
from release_info import VERSION

from release_package import HTTPSOnly,checked_manifest,https_url,validate_package

class UpdateDownload(QThread):
    completed=Signal(object);failed=Signal(str);progress=Signal(str)
    def __init__(self,url,folder,parent=None):super().__init__(parent);self.url=url;self.folder=Path(folder)
    def run(self):
        temporary=None
        try:
            opener=urllib.request.build_opener(HTTPSOnly(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            def get(url):return opener.open(urllib.request.Request(https_url(url),headers={'User-Agent':'ChableszShow/'+str(VERSION),'Cache-Control':'no-cache'}),timeout=15)
            self.progress.emit('Checking for app updates…')
            with get(self.url) as response:
                data=response.read(65537)
                if len(data)>65536:raise ValueError('Update manifest is too large.')
            m=checked_manifest(data)
            if m['version']<=VERSION:self.completed.emit(None);return
            self.folder.mkdir(parents=True,exist_ok=True);temporary=self.folder/'download.part';digest=hashlib.sha256();size=0
            self.progress.emit('Downloading version '+str(m['version'])+'…')
            with get(m['url']) as response,temporary.open('wb') as output:
                while True:
                    if self.isInterruptionRequested():raise ValueError('Update download cancelled.')
                    chunk=response.read(65536)
                    if not chunk:break
                    size+=len(chunk)
                    if size>m['size']:raise ValueError('Download exceeds declared release size.')
                    digest.update(chunk);output.write(chunk)
            if size!=m['size'] or digest.hexdigest().lower()!=m['sha256'].lower():raise ValueError('Downloaded release failed its integrity check.')
            validate_package(temporary,m['version']);target=self.folder/f"release-{m['version']}.zip";temporary.replace(target)
            self.completed.emit({'path':str(target),'version':m['version']})
        except Exception as e:
            if temporary:temporary.unlink(missing_ok=True)
            self.failed.emit(str(e))
