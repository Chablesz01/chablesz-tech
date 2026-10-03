# ChableszShow v17 — Voice Bible & Camera (Windows build kit)

These features are implemented in a SEPARATE copy. This ZIP contains source,
the offline English speech model, tests and a Windows installer recipe.
It is NOT a finished Windows installer. No installer has been released after
the antivirus incident. Windows execution and Defender checks remain pending.
Nothing in an already installed app changes merely by downloading this ZIP.

## Voice Bible

Open Voice / Camera. Choose a microphone and press Listen, or Ctrl+Shift+V.
Repeat a reference such as "John chapter two verses three to eight". Pause for
the configured silence interval, or press Finish reference. The English Vosk
engine recognizes locally; microphone audio is not uploaded. The bundled
model needs no internet or GPU. It loads only when Listen is used.

Complete verse references can go Live automatically when that option is on,
recognition meets the confidence threshold, and every verse in the requested
range exists in the selected installed translation. Incomplete, invalid,
low-confidence references remain for confirmation. This reduces mistakes but
cannot guarantee correctness: confident speech recognition can still be wrong.
For confirmation every time, turn automatic Live off. Edit the recognized
words, Search / Preview, then Send selected Live. English is supported first;
this is not an offline Yoruba speech model. Accent and background noise
accuracy require a microphone test on the operator's laptop.

Remembered words (for example "valley shadow death") search installed Bible
texts offline and show matching passages. Choose the correct result before
sending Live. Bible text is retrieved, not generated. This is offline speech
recognition and word matching, not a general language-model assistant.

The existing passage header, per-verse numbers, Enter, Next/Previous and
Page keys continue working after a voice-selected passage is sent Live.

## Camera

Camera Preview is independent of Bible Preview. Sources:
- USB / webcam, including a phone webcam driver visible to Windows;
- MJPEG camera URLs over HTTP/HTTPS;
- RTSP/video stream URLs through Qt media playback, where codecs are supported.

A phone needs a compatible camera-stream app or webcam driver. For a network
stream use the same trusted local Wi-Fi or phone hotspot and paste the actual
video stream URL, not the phone app's browser page. A local network does not
need internet. Some apps require separate installation or fees; none is bundled.
Camera audio is off to avoid feedback. This build does not record the service.

Connect Preview first. Only Send Camera Live (Ctrl+Shift+C) changes audience
output. Return to Bible (Ctrl+Shift+B) restores the last projected Bible slide.
Camera stays ready in its own Preview while scripture is on the projector.
If a Live camera disconnects, that camera output goes black; Return to Bible
still works. Connecting/disconnecting while Bible is Live leaves Bible intact.
Black and Restore work with a connected camera. Old agendas remain readable;
new saved camera cues need a connected camera when used again.

## Protecting earlier work

The earlier desktop ZIPs and v16 build kit are preserved. Proposed v17 install
folder: LOCALAPPDATA/ChableszShowVoiceCamera/v17, with its own shortcut and
uninstaller. Data goes to APPDATA/ChableszShowVoiceCamera. First launch copies
libraries from ChableszShowStandalone, or from ChableszShow if that is the
previous edition. Copying does not replace original data. Existing media
references still point to original files; keep those files available.
Saved service JSON files and imported Bibles are copied too. Existing imported
translations remain usable. Four public-domain Bible editions stay bundled.

Full installer updates are required for this bundled edition; the older
source-ZIP auto-updater remains disabled. Office/LibreOffice is still needed
for native PowerPoint conversion; an office suite is not bundled.

## Build and validation

The workflow includes pinned Windows libraries and the offline speech model.
It checks prerequisite signatures, scans the payload and installer using
up-to-date Microsoft Defender, then checks startup, install, shortcut and
uninstall behavior. Unavailable or failed scanning blocks release.
These Windows checks have NOT run yet, and no signed publisher certificate
is available. No antivirus protection bypass or exclusion is included.

Completed here: 50 app/feature tests, 3 packaging tests, recorded offline
recognition and worker shutdown, real local HTTP MJPEG streaming, 23 pinned
dependency archive hashes, offline payload assembly (without WebView2 for the
local check), and installer-recipe syntax. The model is included in this ZIP.
It is excluded from source control; the builder can fetch the same model and
verify its recorded checksum when building on Windows.

See qa/verification.json. Physical Windows microphone, phone camera, sound,
HDMI, RTSP, and antivirus checks still need the target environment.
