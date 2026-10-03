"""Windows Edge WebView2 host, isolated from the Qt UI process."""
import base64
import json
import os
import sys
import threading
from web_media import media_script,preview_script,delayed_media_script

_lock=threading.Lock()
def emit(**value):
    with _lock:
        print(json.dumps(value),flush=True)

class PreviewAPI:
    enabled=True
    def preview_tap(self):
        if self.enabled: emit(event='tap')


def main():
    if os.name!='nt': raise RuntimeError('The Edge WebView2 host requires Windows.')
    import webview
    api=PreviewAPI()
    window=webview.create_window('ChableszShow Web Player',url=sys.argv[1],js_api=api,
        width=800,height=450,min_size=(100,60),frameless=True,easy_drag=False,
        focus=True,background_color='#080d1a')
    loaded=threading.Event(); settings={'preview':True,'fill':False,'volume':0.8,'loop':False}
    def shown():
        emit(event='ready',hwnd=int(window.native.Handle.ToInt64()))
    def apply_settings():
        from System import Action
        def mute_browser():window.native.webview.CoreWebView2.IsMuted=bool(settings['preview'] or settings.get('muted',False))
        window.native.Invoke(Action(mute_browser))
        window.evaluate_js(preview_script(settings['preview'] and settings.get('tap_enabled',True)))
        window.evaluate_js(media_script('mute',settings['preview'] or settings.get('muted',False)))
        window.evaluate_js(media_script('volume',settings['volume']))
        window.evaluate_js(media_script('repeat',settings.get('repeat_mode','Off')))
        window.evaluate_js(media_script('fill' if settings['fill'] else 'page'))
        window.evaluate_js(delayed_media_script(settings))
    def page_loaded():
        loaded.set()
        try: apply_settings()
        except Exception as error: emit(event='warning',message=str(error))
        emit(event='loaded')
    def capture():
        # Microsoft's supported WebView2 screenshot API; no screen scraping.
        from System import Action
        from System.IO import MemoryStream
        from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
        stream=MemoryStream(); task=[]
        def begin():
            task.append(window.native.webview.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream))
        window.native.Invoke(Action(begin))
        task[0].GetAwaiter().GetResult()
        emit(event='snapshot',image=base64.b64encode(bytes(stream.ToArray())).decode('ascii'))
        stream.Dispose()
    def commands():
        for line in sys.stdin:
            try:
                command=json.loads(line); name=command.get('command')
                if name=='close': window.destroy();return
                if name=='snapshot':
                    if loaded.is_set(): capture()
                    else: emit(event='snapshot_pending')
                elif name=='settings':
                    settings.update(command.get('values',{}));api.enabled=bool(settings['preview'] and settings.get('tap_enabled',True))
                    if loaded.is_set():apply_settings()
                elif name=='media' and loaded.is_set():
                    if command['action']=='mute':
                        from System import Action
                        window.native.Invoke(Action(lambda:setattr(window.native.webview.CoreWebView2,'IsMuted',bool(command.get('value')))))
                    window.evaluate_js(media_script(command['action'],command.get('value')))
            except Exception as error:emit(event='warning',message=str(error))
        window.destroy()  # Parent process closed; do not leave the browser running.
    window.events.shown+=shown;window.events.loaded+=page_loaded
    window.events.closed+=lambda:emit(event='closed')
    webview.start(commands,gui='edgechromium',private_mode=False,storage_path=sys.argv[2])

if __name__=='__main__':
    try:main()
    except Exception as error:
        emit(event='error',message='Edge WebView2 could not start: '+str(error)+'. Run Install_Web_Player.bat.');sys.exit(1)
