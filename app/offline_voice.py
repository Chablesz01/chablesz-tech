"""Local Vosk speech recognition with Qt microphone capture; no cloud/audio upload."""
import array,json,queue,struct,sys,time
from pathlib import Path
from PySide6.QtCore import QObject,QThread,Signal,QTimer,Slot
from PySide6.QtMultimedia import QAudioFormat,QAudioSource,QMediaDevices,QAudio

class PcmConverter:
    def __init__(self,rate,channels,kind):
        self.rate=rate;self.channels=channels;self.kind=kind;self.pending=b'';self.samples=[];self.phase=0.
    def feed(self,data):
        sizes={'int16':2,'int32':4,'float':4,'uint8':1};size=sizes[self.kind]*self.channels
        data=self.pending+data;count=len(data)//size*size;self.pending=data[count:];data=data[:count]
        if not data:return b''
        code={'int16':'h','int32':'i','float':'f','uint8':'B'}[self.kind]
        values=struct.unpack('<'+str(len(data)//sizes[self.kind])+code,data)
        scale={'int16':1,'int32':1/65536,'float':32767,'uint8':256}[self.kind]
        for i in range(0,len(values),self.channels):
            value=sum(values[i:i+self.channels])/self.channels
            if self.kind=='uint8':value-=128
            self.samples.append(max(-32768,min(32767,value*scale)))
        out=array.array('h');step=self.rate/16000
        while self.phase+1<len(self.samples):
            index=int(self.phase);fraction=self.phase-index
            value=self.samples[index]*(1-fraction)+self.samples[index+1]*fraction
            out.append(round(value));self.phase+=step
        consumed=min(int(self.phase),len(self.samples));self.samples=self.samples[consumed:];self.phase-=consumed
        if sys.byteorder!='little':out.byteswap()
        return out.tobytes()

def model_path():
    path=Path(__file__).with_name('voice_models')/'vosk-model-small-en-us-0.15'
    return path if (path/'am/final.mdl').is_file() and (path/'conf/model.conf').is_file() else None

class RecognitionWorker(QThread):
    ready=Signal();partial=Signal(str,int);recognized=Signal(str,float,int);failed=Signal(str)
    def __init__(self,path,parent=None):
        super().__init__(parent);self.path=path;self.commands=queue.Queue(maxsize=128)
    def submit(self,command):
        if command[0] in ('cancel','begin'):
            while True:
                try:self.commands.get_nowait()
                except queue.Empty:break
        try:self.commands.put_nowait(command);return True
        except queue.Full:return False
    def run(self):
        try:
            from vosk import Model,KaldiRecognizer,SetLogLevel
            SetLogLevel(-1);model=Model(str(self.path));recognizer=None;parts=[];words=[];tag=0
            self.ready.emit()
            while not self.isInterruptionRequested():
                try:kind,payload,token=self.commands.get(timeout=.1)
                except queue.Empty:continue
                if kind=='begin':
                    recognizer=KaldiRecognizer(model,16000);recognizer.SetWords(True);parts=[];words=[];tag=token
                elif kind=='pcm' and recognizer and token==tag:
                    if recognizer.AcceptWaveform(payload):
                        result=json.loads(recognizer.Result());parts.append(result.get('text',''));words.extend(result.get('result',[]))
                        self.partial.emit(' '.join(p for p in parts if p),tag)
                    else:
                        result=json.loads(recognizer.PartialResult());self.partial.emit(' '.join([*parts,result.get('partial','')]).strip(),tag)
                elif kind=='finish' and recognizer and token==tag:
                    result=json.loads(recognizer.FinalResult());parts.append(result.get('text',''));words.extend(result.get('result',[]))
                    confidence=min((float(w.get('conf',0)) for w in words),default=0)
                    self.recognized.emit(' '.join(p for p in parts if p).strip(),confidence,tag);recognizer=None
                elif kind=='cancel':recognizer=None;parts=[];words=[]
        except ImportError:self.failed.emit('The offline voice engine is not installed in this build. Use the bundled voice edition.')
        except Exception:self.failed.emit('The offline voice model could not be opened. Repair the bundled voice edition.')

class VoiceCapture(QObject):
    state=Signal(str);partial=Signal(str);recognized=Signal(str,float);started=Signal();stopped=Signal();failed=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.worker=None;self.source=None;self.device=None;self.converter=None;self.token=0;self.listening=False;self.loaded=False;self.loading=False
        self.pending=False;self.input_id='';self.silence_ms=1200;self.last_speech=0.;self.heard_speech=False;self.started_at=0
        self.silence_timer=QTimer(self);self.silence_timer.setInterval(100);self.silence_timer.timeout.connect(self.check_silence)
    def begin(self,device_id='',silence_ms=1200):
        if self.listening:return
        self.input_id=device_id;self.silence_ms=silence_ms
        if not self.worker:
            path=model_path()
            if path is None:self.failed.emit('No offline speech model is included. Voice stays off; typing and Bible projection still work.');return
            self.worker=RecognitionWorker(path,self);self.worker.ready.connect(self.model_ready)
            self.worker.partial.connect(self.partial_result)
            self.worker.recognized.connect(self.result);self.worker.failed.connect(self.fail);self.worker.finished.connect(self.worker_finished)
            self.loading=True;self.pending=True;self.state.emit('Loading the offline speech model…');self.worker.start();return
        if not self.loaded:self.pending=True;self.state.emit('Loading the offline speech model…');return
        self.start_microphone()
    @Slot(str,int)
    def partial_result(self,text,tag):
        if tag==self.token and self.listening:self.partial.emit(text)
    def worker_finished(self):
        self.loaded=False;self.loading=False;self.pending=False
        worker=self.worker;self.worker=None
        if worker:worker.deleteLater()
    def model_ready(self):
        self.loaded=True;self.loading=False
        if self.pending:self.pending=False;self.start_microphone()
    def start_microphone(self):
        inputs=QMediaDevices.audioInputs()
        device=next((d for d in inputs if bytes(d.id()).hex()==self.input_id),None) if self.input_id else QMediaDevices.defaultAudioInput()
        if device is None or device.isNull():self.failed.emit('No microphone is available. Check Windows microphone permissions and connect a microphone.');return
        fmt=QAudioFormat();fmt.setSampleRate(16000);fmt.setChannelCount(1);fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):fmt=device.preferredFormat()
        kinds={QAudioFormat.Int16:'int16',QAudioFormat.Int32:'int32',QAudioFormat.Float:'float',QAudioFormat.UInt8:'uint8'}
        if fmt.sampleFormat() not in kinds or fmt.channelCount()<1 or fmt.sampleRate()<8000:
            self.failed.emit('This microphone format is unsupported. Choose a different microphone.');return
        self.converter=PcmConverter(fmt.sampleRate(),fmt.channelCount(),kinds[fmt.sampleFormat()]);self.token+=1
        self.worker.submit(('begin',None,self.token));self.source=QAudioSource(device,fmt,self)
        self.source.stateChanged.connect(self.audio_state);self.device=self.source.start()
        if self.device is None or self.source.error()!=QAudio.NoError:
            self.fail('Microphone could not start. Check permissions and close apps using this microphone.');return
        self.listening=True;self.device.readyRead.connect(self.read_audio);self.heard_speech=False
        self.started_at=time.monotonic();self.last_speech=self.started_at;self.silence_timer.start();self.started.emit()
        self.state.emit('Listening offline. Repeat the reference; pause to finish, or press Listen again.')
    def read_audio(self):
        if not self.listening or not self.device:return
        pcm=self.converter.feed(bytes(self.device.readAll()))
        if not pcm:return
        values=array.array('h');values.frombytes(pcm)
        if sys.byteorder!='little':values.byteswap()
        rms=(sum(v*v for v in values)/len(values))**.5
        if rms>450:self.heard_speech=True;self.last_speech=time.monotonic()
        if not self.worker.submit(('pcm',pcm,self.token)):self.fail('Speech processing fell behind. Try a shorter reference.')
    def check_silence(self):
        now=time.monotonic()
        if self.heard_speech and self.silence_ms>0 and (now-self.last_speech)*1000>=self.silence_ms:self.finish()
        elif now-self.started_at>30:self.finish()
    def audio_state(self,state):
        if self.source and self.source.error() not in (QAudio.NoError,QAudio.UnderrunError):self.fail('Microphone capture stopped. Check the selected microphone.')
    def finish(self):
        if self.loading:self.pending=False;self.state.emit('Voice start cancelled.');return
        if not self.listening:return
        self.read_audio();self.listening=False;self.silence_timer.stop()
        if self.source:self.source.blockSignals(True);self.source.stop();self.source.deleteLater();self.source=None;self.device=None
        if not self.worker.submit(('finish',None,self.token)):
            self.fail('Speech processing fell behind. Try a shorter reference.');return
        self.stopped.emit();self.state.emit('Finding the passage offline…')
    def result(self,text,confidence,tag):
        if tag==self.token:self.recognized.emit(text,confidence)
    def cancel(self):
        self.pending=False;self.token+=1;self.listening=False;self.silence_timer.stop()
        if self.source:self.source.blockSignals(True);self.source.stop();self.source.deleteLater();self.source=None;self.device=None
        if self.worker:self.worker.submit(('cancel',None,self.token))
        self.stopped.emit()
    def fail(self,message):self.cancel();self.failed.emit(message)
    def shutdown(self):
        self.cancel()
        if self.worker and self.worker.isRunning():self.worker.requestInterruption()
        return not self.worker or not self.worker.isRunning()
