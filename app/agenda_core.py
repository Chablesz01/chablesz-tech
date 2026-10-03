"""Portable service agendas and built-in slide themes; no Qt dependency."""

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


THEMES = {
    'Standard Dark': {'background': '#080d1a', 'text_color': '#ffffff', 'brand': ''},
    'RCCG Scripture': {'background': '#18102c', 'text_color': '#ffffff', 'brand': 'RCCG JESUS THE CHAMPION'},
    'RCCG Worship': {'background': '#371018', 'text_color': '#fff7e6', 'brand': 'RCCG JESUS THE CHAMPION'},
    'RCCG Announcement': {'background': '#102b46', 'text_color': '#ffffff', 'brand': 'RCCG JESUS THE CHAMPION'},
    'Ocean Blue': {'background':'#061b38','gradient_end':'#135a85','text_color':'#ffffff','brand':''},
    'Emerald Worship': {'background':'#04291e','gradient_end':'#12664f','text_color':'#ffffff','brand':''},
    'Royal Purple': {'background':'#1c103e','gradient_end':'#613777','text_color':'#ffffff','brand':''},
    'Crimson Praise': {'background':'#310917','gradient_end':'#8c2541','text_color':'#fff7ec','brand':''},
    'Golden Evening': {'background':'#30220c','gradient_end':'#755420','text_color':'#fff9e9','brand':''},
    'Bright Paper': {'background':'#faf6ed','gradient_end':'#e8e0ce','text_color':'#172c43','brand':''},
    'Deep Black': {'background':'#000000','gradient_end':'#000000','text_color':'#ffffff','brand':''},
    'Calm Sky': {'background':'#dcefff','gradient_end':'#9ec8df','text_color':'#102740','brand':''},
}
for preset in THEMES.values():preset.setdefault('gradient_end','')
MEDIA_KINDS = {'image', 'video'}
ALLOWED_KINDS = MEDIA_KINDS | {'text', 'verse', 'url', 'camera'}
FORMAT = 'ChableszShow Agenda'
VERSION = 1
ALERT_MOTIONS = ('Static','Scroll left','Scroll right','Rolling','Bottom to top','Top to bottom')


def advance_alert(motion, x, y, label_width, label_height, screen_width, screen_height, speed):
    """Return (x, y, finished) for one 30 ms alert animation frame."""
    step = max(1, int(speed)) * 2
    finished = False
    if motion == 'Scroll left':
        x -= step
        if x < -label_width: x = screen_width
    elif motion == 'Scroll right':
        x += step
        if x > screen_width: x = -label_width
    elif motion == 'Rolling':
        y -= step
        if y < -label_height: y = screen_height
    elif motion == 'Bottom to top':
        target = max(0, (screen_height-label_height)//2)
        y = max(target, y-step)
        finished = y == target
    elif motion == 'Top to bottom':
        target = max(0, (screen_height-label_height)//2)
        y = min(target, y+step)
        finished = y == target
    return x, y, finished


def _digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _validate_items(items):
    if not isinstance(items, list) or len(items) > 10000:
        raise ValueError('Invalid agenda item list.')
    for item in items:
        if not isinstance(item, dict) or item.get('kind') not in ALLOWED_KINDS or not isinstance(item.get('title'), str):
            raise ValueError('The agenda contains an invalid item.')
        if item['kind'] in MEDIA_KINDS and not isinstance(item.get('path'), str):
            raise ValueError('A media item has no file path.')
        if item['kind'] in ('text', 'verse') and not isinstance(item.get('text'), str):
            raise ValueError('A text item has no content.')
        if item['kind'] == 'url' and not isinstance(item.get('url'), str):
            raise ValueError('A web item has no URL.')


def save_package(destination, items, theme_settings=None):
    """Save all referenced media with the agenda into a portable .chablesz file."""
    _validate_items(items)
    packed, assets = [], {}
    for item in items:
        entry = dict(item)
        if entry['kind'] in MEDIA_KINDS:
            source = Path(entry.pop('path')).expanduser()
            if not source.is_file():
                raise FileNotFoundError(f'Missing media: {source}')
            digest = _digest(source)
            asset = 'media/' + digest + source.suffix.lower()
            assets.setdefault(asset, source)
            entry['asset'] = asset
            entry['sha256'] = digest
        packed.append(entry)
    styles = dict(theme_settings or {})
    for setting in ('background_image','bible_background_image','song_background_image'):
        background = styles.get(setting)
        if background:
            source = Path(background)
            if not source.is_file():
                raise FileNotFoundError(f'Missing slide background: {source}')
            digest = _digest(source)
            asset = 'media/' + digest + source.suffix.lower()
            assets.setdefault(asset, source)
            styles[setting] = {'asset':asset,'sha256':digest}
    manifest = {'format': FORMAT, 'version': VERSION, 'items': packed, 'theme_settings': styles}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.chablesz_', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('agenda.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, source in assets.items():
                archive.write(source, name)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return len(packed), len(assets)


def load_package(source, asset_dir):
    return load_package_full(source, asset_dir)[0]


def load_package_full(source, asset_dir):
    """Verify package media and import it into an app-owned directory."""
    with zipfile.ZipFile(source) as archive:
        if 'agenda.json' not in archive.namelist():
            raise ValueError('This is not a ChableszShow agenda.')
        manifest = json.loads(archive.read('agenda.json'))
        if manifest.get('format') != FORMAT or manifest.get('version') != VERSION:
            raise ValueError('Unsupported ChableszShow agenda version.')
        items = manifest.get('items')
        if not isinstance(items, list) or len(items) > 10000:
            raise ValueError('Invalid agenda item list.')
        imported = []
        styles = manifest.get('theme_settings', {})
        if not isinstance(styles, dict): raise ValueError('Invalid agenda theme.')
        for setting in ('background_image','bible_background_image','song_background_image'):
            if styles.get(setting) and not isinstance(styles[setting], dict):
                raise ValueError('An agenda background must be packaged with its image.')
        staged = []
        asset_dir = Path(asset_dir)
        asset_dir.mkdir(parents=True, exist_ok=True)
        try:
            def extract_asset(name, digest):
                if not isinstance(name, str) or not isinstance(digest, str):
                    raise ValueError('Invalid media reference in agenda.')
                suffix = Path(name).suffix.lower()
                if not name.startswith('media/') or '/' in name[6:] or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest) or Path(name).stem != digest or suffix not in ('.png','.jpg','.jpeg','.webp','.bmp','.mp4','.mov','.mkv','.webm','.avi'):
                    raise ValueError('Invalid media reference in agenda.')
                info = archive.getinfo(name)
                if info.file_size > 2 * 1024 * 1024 * 1024:
                    raise ValueError('A media item exceeds the 2 GB limit.')
                target = asset_dir / Path(name).name
                if not target.exists() or _digest(target) != digest:
                    fd, temp = tempfile.mkstemp(prefix='.media_', dir=asset_dir)
                    os.close(fd)
                    staged.append(temp)
                    with archive.open(info) as original, open(temp, 'wb') as output:
                        shutil.copyfileobj(original, output, 1024 * 1024)
                    if _digest(temp) != digest:
                        raise ValueError('A media file failed its integrity check.')
                    os.replace(temp, target)
                    staged.remove(temp)
                return str(target)
            for item in items:
                if not isinstance(item, dict) or item.get('kind') not in ALLOWED_KINDS or not isinstance(item.get('title'), str):
                    raise ValueError('The agenda contains an invalid item.')
                entry = dict(item)
                if entry['kind'] in MEDIA_KINDS:
                    name, digest = entry.pop('asset', ''), entry.pop('sha256', '')
                    entry['path'] = extract_asset(name, digest)
                imported.append(entry)
            for setting in ('background_image','bible_background_image','song_background_image'):
                if isinstance(styles.get(setting), dict):
                    reference = styles[setting]
                    styles[setting] = extract_asset(reference.get('asset',''), reference.get('sha256',''))
            _validate_items(imported)
            return imported, styles
        finally:
            for path in staged:
                if os.path.exists(path): os.unlink(path)
