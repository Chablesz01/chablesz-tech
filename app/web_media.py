"""Browser-neutral media controls. Never spoof a browser or bypass site security."""
import json
from urllib.parse import urlsplit


def validated_url(value):
    value=value.strip()
    if not value.startswith(('https://','http://')): value='https://'+value
    parts=urlsplit(value)
    if parts.scheme not in ('https','http') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Enter a public HTTP or HTTPS web address without embedded credentials.')
    return value


def media_script(command, value=None):
    # Native full-screen requests require a website user gesture. This reversible
    # CSS mode fills the application's player viewport while retaining the page.
    if command=='fill':
        return """(() => {
          const target=document.querySelector('.html5-video-player')||document.querySelector('video');
          if(!target)return false;
          target.setAttribute('data-chablesz-player','1');
          let style=document.getElementById('chablesz-player-style');
          if(!style){style=document.createElement('style');style.id='chablesz-player-style';document.head.appendChild(style);}
          style.textContent='[data-chablesz-player="1"]{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;z-index:2147483646!important;background:black!important;transform:none!important;} [data-chablesz-player="1"] video{width:100%!important;height:100%!important;object-fit:contain!important;} [data-chablesz-player="1"] .html5-video-container{width:100%!important;height:100%!important;inset:0!important;}';
          return true;
        })()"""
    if command=='page':
        return "document.getElementById('chablesz-player-style')?.remove();document.querySelectorAll('[data-chablesz-player]').forEach(e=>e.removeAttribute('data-chablesz-player'));true"
    value=json.dumps(value)
    if command=='repeat':
        return 'document.querySelectorAll("video,audio").forEach(v=>{if(v.chableszEnded)v.removeEventListener("ended",v.chableszEnded);v.loop='+('true' if value=='"Loop"' else 'false')+';v.chableszRepeated=false;v.chableszEnded=()=>{if('+value+'==="Repeat once"&&!v.chableszRepeated){v.chableszRepeated=true;v.currentTime=0;v.play().catch(()=>{});}};v.addEventListener("ended",v.chableszEnded);});true'
    action={
        'mute':f'v.muted={value}',
        'volume':f'v.volume=Math.max(0,Math.min(1,{value}))',
        'toggle':'v.paused?v.play().catch(()=>{}):v.pause()',
        'pause':'v.pause()', 'play':'v.play().catch(()=>{})',
        'restart':'v.currentTime=0;v.play().catch(()=>{})',
        'seek':f'v.currentTime=Math.max(0,Math.min(isFinite(v.duration)?v.duration:Infinity,v.currentTime+{value}))',
        'loop':f'v.loop={value}'
        ,'black':'v.chableszWasPlaying=!v.paused;v.pause()'
        ,'restore':'if(v.chableszWasPlaying)v.play().catch(()=>{})'
    }.get(command)
    if not action: raise ValueError('Unknown web media command')
    return 'document.querySelectorAll("video,audio").forEach(v=>{'+action+'});true'


def preview_script(enabled):
    return """window.chableszPreview=%s;
      if(!window.chableszTapInstalled){
        window.chableszTapInstalled=true;
        document.addEventListener('click',e=>{
          if(window.chableszPreview && e.isTrusted && e.target.closest('video,.html5-video-player')){
            e.preventDefault();e.stopImmediatePropagation();
            window.pywebview?.api?.preview_tap();
          }
        },true);
      }
    """ % ('true' if enabled else 'false')


def delayed_media_script(settings):
    """Handle players created after load, as on single-page video websites."""
    config=json.dumps(settings)
    fill=media_script('fill')
    return """window.chableszMediaSettings=%s;
      window.chableszPrepareNewMedia=()=>{
        const s=window.chableszMediaSettings;
        document.querySelectorAll('video,audio').forEach(v=>{
          if(v.chableszPrepared)return;
          v.chableszPrepared=true;v.muted=!!(s.preview||s.muted);v.volume=s.volume;
          v.loop=s.repeat_mode==='Loop';
          v.addEventListener('ended',()=>{if(window.chableszMediaSettings.repeat_mode==='Repeat once'&&!v.chableszRepeated){v.chableszRepeated=true;v.currentTime=0;v.play().catch(()=>{});}});
        });
        if(s.fill&&!document.querySelector('[data-chablesz-player]')){%s;}
      };
      if(!window.chableszMediaObserver){
        window.chableszMediaObserver=new MutationObserver(()=>{
          clearTimeout(window.chableszMediaTimer);window.chableszMediaTimer=setTimeout(()=>window.chableszPrepareNewMedia(),120);
        });
        window.chableszMediaObserver.observe(document.documentElement,{childList:true,subtree:true});
      }
      window.chableszPrepareNewMedia();
    """ % (config,fill)
