"""Pure standard-library release validation, shared with the apply helper."""
import json,re,zipfile,urllib.request
from pathlib import PurePosixPath
from urllib.parse import urlsplit

FILES={'capture_tools.py','camera_capture.py','offline_voice.py','voice_reference.py','app.py','agenda_core.py','bible_core.py','projection.py','browser_bridge.py','browser_host.py',
       'web_media.py','media_import.py','service_tools.py','word_finder.py','update_engine.py',
       'update_apply.py','release_package.py','release_info.py','requirements.txt','README.md','UPDATE_README.txt',
       'THIRD_PARTY_NOTICES.txt','Install_WebView2.ps1','Install_Web_Player.bat','Setup_Environment.ps1',
       'build_installer.nsi','start_windows.bat','Install_ChableszShow.bat','Update_ChableszShow.ps1','Update_Installed_App.bat','bible_csv_example.csv'}

def https_url(value):
    p=urlsplit(value)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment:raise ValueError('Updates need a trusted HTTPS address without credentials or fragments.')
    return value

class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        https_url(newurl);return super().redirect_request(req,fp,code,msg,headers,newurl)


def checked_manifest(data):
    m=json.loads(data)
    if m.get('app')!='ChableszShow' or type(m.get('version')) is not int or m['version']<1:raise ValueError('Not a valid ChableszShow release.')
    https_url(m.get('url',''))
    if not re.fullmatch('[0-9a-fA-F]{64}',m.get('sha256','')):raise ValueError('Release checksum is missing.')
    if type(m.get('size')) is not int or not 0<m['size']<=50*1024*1024:raise ValueError('Release size is invalid.')
    return m


def validate_package(path,expected_version):
    with zipfile.ZipFile(path) as z:
        total=0;seen=set();files=[]
        for info in z.infolist():
            if info.is_dir():continue
            name=PurePosixPath(info.filename)
            if '\\' in info.filename or name.is_absolute() or '..' in name.parts:raise ValueError('Unsafe update path.')
            if not name.parts or name.parts[0]!='ChableszShow':raise ValueError('Unexpected release root.')
            relative=PurePosixPath(*name.parts[1:]);value=str(relative)
            if value in seen:raise ValueError('Duplicate update entry.')
            seen.add(value);total+=info.file_size
            if total>100*1024*1024:raise ValueError('Unpacked update is too large.')
            if value in FILES or (len(relative.parts)==2 and relative.parts[0]=='bundled_bibles' and relative.suffix=='.csv'):
                files.append(value)
            elif relative.parts and relative.parts[0] in ('tests','previews'):continue
            else:raise ValueError('Unexpected update file: '+value)
        if not ({name for name in FILES if name.endswith('.py')}|{'requirements.txt'}).issubset(files):raise ValueError('Incomplete release.')
        content=z.read('ChableszShow/release_info.py').decode('utf-8')
        if not re.search(r'^VERSION\s*=\s*'+str(expected_version)+r'\s*$',content,re.M):raise ValueError('Release version does not match manifest.')
        return files

