"""Windowed entry point; preserve the earlier desktop edition and its data."""
import ctypes, os, runpy, shutil, sys, traceback, tempfile
from pathlib import Path

def migrate(old, new):
    if new.exists(): return
    new.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='ChableszShow-migrate-',dir=new.parent))
    try:
        for name in ('songs.json','songbooks.json','playlist.json','settings.json'):
            source=old/name
            if source.is_file(): shutil.copy2(source,staging/name)
        for name in ('bibles','services'):
            if (old/name).is_dir():shutil.copytree(old/name,staging/name)
        staging.rename(new)
    finally:
        if staging.exists(): shutil.rmtree(staging)

def main():
    folder=Path(os.environ.get('APPDATA',Path.home()))
    data=folder/'ChableszShowVoiceCamera'
    previous=folder/'ChableszShowStandalone'
    if not previous.exists():previous=folder/'ChableszShow'
    migrate(previous,data)
    log=(data/'launch.log').open('a',encoding='utf-8',buffering=1)
    if sys.stdout is None: sys.stdout=log
    if sys.stderr is None: sys.stderr=log
    try: runpy.run_path(str(Path(__file__).with_name('app.py')),run_name='__main__')
    except Exception:
        traceback.print_exc(file=log)
        if os.name=='nt': ctypes.windll.user32.MessageBoxW(None,'ChableszShow could not start. Details are in '+str(data/'launch.log'),'ChableszShow',0x10)
        raise

if __name__=='__main__': main()
