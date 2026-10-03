"""Assemble a private CPython runtime and pinned Windows wheels. No pip at install time."""
import argparse, hashlib, json, shutil, tarfile, urllib.request, zipfile
from pathlib import Path, PurePosixPath
ROOT = Path(__file__).resolve().parent.parent
PY_URL = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip'
PY_HASH = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'
WV_URL = 'https://go.microsoft.com/fwlink/?LinkId=2124701'

def fetch(url, dest, expected=None):
    if dest.exists() and expected and hashlib.sha256(dest.read_bytes()).hexdigest() == expected:
        return
    temporary = dest.with_suffix(dest.suffix + '.part')
    with urllib.request.urlopen(url, timeout=60) as source, temporary.open('wb') as target:
        shutil.copyfileobj(source, target)
    if expected and hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
        temporary.unlink(); raise ValueError('Checksum mismatch: ' + dest.name)
    temporary.replace(dest)

def safe_path(root, name):
    parts = PurePosixPath(name).parts
    if name.startswith('/') or '\\' in name or any(p in ('..', '.') or ':' in p for p in parts):
        raise ValueError('Unsafe archive member: ' + name)
    p = root.joinpath(*parts)
    if not p.resolve().is_relative_to(root.resolve()): raise ValueError(name)
    return p

def unzip(archive, root, wheel=False):
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if member.is_dir(): continue
            name = member.filename
            if wheel and '.data/' in name:
                _, category, name = name.split('/', 2)
                if category not in ('purelib', 'platlib'): continue
            p = safe_path(root, name); p.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as source, p.open('wb') as target: shutil.copyfileobj(source, target)

def build(cache, output, include_webview=True):
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'; runtime.mkdir(exist_ok=True)
    pyzip = cache / 'python-3.13.16-embed-amd64.zip'; fetch(PY_URL, pyzip, PY_HASH)
    unzip(pyzip, runtime)
    site = runtime / 'Lib' / 'site-packages'; site.mkdir(parents=True, exist_ok=True)
    for entry in json.loads((ROOT / 'packaging/dependencies.lock.json').read_text()):
        p = cache / entry['filename']; fetch(entry['url'], p, entry['sha256'])
        if p.suffix == '.whl': unzip(p, site, wheel=True)
        elif entry['name'] == 'proxy-tools':
            with tarfile.open(p) as z:
                for member in z.getmembers():
                    if member.isfile() and '/proxy_tools/' in member.name:
                        name = member.name.split('/', 1)[1]; dest = safe_path(site, name)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with z.extractfile(member) as source, dest.open('wb') as target: shutil.copyfileobj(source, target)
                    elif member.isfile() and member.name.rsplit('/', 1)[-1].lower().startswith('license'):
                        dest = site / 'proxy-tools-LICENSE.txt'; dest.write_bytes(z.extractfile(member).read())
        elif entry['name'] == 'srt':
            with tarfile.open(p) as z:
                for member in z.getmembers():
                    if member.isfile() and member.name.endswith('/srt.py'):
                        (site/'srt.py').write_bytes(z.extractfile(member).read())
                    elif member.isfile() and member.name.rsplit('/',1)[-1].upper().startswith('LICENSE'):
                        (site/'srt-LICENSE.txt').write_bytes(z.extractfile(member).read())
        else: raise ValueError('Unsupported dependency archive: ' + p.name)
    (runtime / 'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../app\nimport site\n')
    voice_lock=json.loads((ROOT/'packaging/voice-model.lock.json').read_text())
    voice_root=ROOT/'app/voice_models'
    if not (voice_root/voice_lock['model_folder']/'am/final.mdl').is_file():
        model_archive=cache/voice_lock['filename'];fetch(voice_lock['url'],model_archive,voice_lock['sha256']);voice_root.mkdir(parents=True,exist_ok=True);unzip(model_archive,voice_root)
    model_files=json.loads((ROOT/'packaging/voice-model-files.json').read_text())
    for name,digest in model_files.items():
        if hashlib.sha256(safe_path(ROOT/'app',name).read_bytes()).hexdigest()!=digest:raise ValueError('Voice model integrity failed: '+name)
    shutil.copytree(ROOT / 'app', output / 'app', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'packaging/launcher.py', output / 'app/launcher.py')
    if include_webview:
        prerequisite = output / 'prerequisites'; prerequisite.mkdir(exist_ok=True)
        fetch(WV_URL, prerequisite / 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe')
    inventory = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file() and p.name != 'payload-sha256.json'}
    (output / 'payload-sha256.json').write_text(json.dumps(inventory, indent=2))
    print(f'Assembled {len(inventory)} files. Windows signature checks and antivirus scan are still required.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--cache', type=Path, default=ROOT/'cache'); parser.add_argument('--output', type=Path, default=ROOT/'payload'); parser.add_argument('--without-webview', action='store_true')
    args = parser.parse_args(); build(args.cache.resolve(), args.output.resolve(), not args.without_webview)
