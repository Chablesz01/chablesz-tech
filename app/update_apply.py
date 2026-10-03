"""Apply an already verified release only after the GUI process exits."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import uuid
from pathlib import Path
from release_package import validate_package


def wait_for_exit(pid):
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        handle=kernel.OpenProcess(0x00100000,False,pid)
        if handle:
            kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
            kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            result=kernel.WaitForSingleObject(handle,120000);kernel.CloseHandle(handle)
            if result!=0:raise RuntimeError('App did not exit; update was not applied.')
    else:
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:os.kill(pid,0)
            except ProcessLookupError:return
            time.sleep(.2)
        raise RuntimeError('App did not exit.')


def apply_release(package,root,version):
    root=Path(root).resolve();files=validate_package(package,version)
    backup=root/('Previous_app_'+str(int(time.time()))+'_'+uuid.uuid4().hex[:8]);backup.mkdir()
    old=[];created=[]
    try:
        with zipfile.ZipFile(package) as z:
            for name in files:
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved);old.append(name)
                else:created.append(name)
                temporary=target.with_name(target.name+'.update-new');temporary.write_bytes(z.read('ChableszShow/'+name));os.replace(temporary,target)
        # New dependency changes are installed in a separate process; this helper loads no Qt DLLs.
        before=(backup/'requirements.txt').read_bytes() if (backup/'requirements.txt').exists() else b''
        if before!=(root/'requirements.txt').read_bytes():
            subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','-r',str(root/'requirements.txt')],check=True,timeout=600)
        return backup
    except Exception:
        for name in old:shutil.copy2(backup/name,root/name)
        for name in created:(root/name).unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pid',type=int,required=True);parser.add_argument('--root',required=True);parser.add_argument('--package',required=True);parser.add_argument('--version',type=int,required=True)
    args=parser.parse_args();root=Path(args.root).resolve()
    log=root/'update-result.txt'
    try:
        wait_for_exit(args.pid);backup=apply_release(args.package,root,args.version)
        log.write_text(f'Updated to v{args.version}. Previous program files: {backup}',encoding='utf-8')
    except Exception as e:log.write_text('Update failed; previous program files restored where applicable: '+str(e),encoding='utf-8')
    python=Path(sys.executable)
    if os.name=='nt' and python.with_name('pythonw.exe').exists():python=python.with_name('pythonw.exe')
    subprocess.Popen([str(python),str(root/'app.py')],cwd=root)

if __name__=='__main__':main()
