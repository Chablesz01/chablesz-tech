import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtGui import QImage,QColor
import test_ui
from voice_reference import spoken_reference,validated_spoken_rows
from offline_voice import PcmConverter,RecognitionWorker
from camera_capture import JpegStream,CameraView,camera_url
from agenda_core import save_package,load_package

class ParsingTests(unittest.TestCase):
    def test_spoken_references_ranges_numbered_books(self):
        examples={'John chapter two verses three to eight':'John 2:3-8','please open first John chapter three verse sixteen':'1 John 3:16','Psalms one hundred and nineteen verse one hundred and seventy six':'Psalms 119:176','Second Corinthians five seventeen':'2 Corinthians 5:17','Genesis 2:1-6':'Genesis 2:1-6','song of songs chapter two verse one':'Song of Solomon 2:1'}
        for text,query in examples.items():
            with self.subTest(text=text):self.assertEqual(spoken_reference(text).query,query)
    def test_ambiguous_invalid_and_incomplete_ranges_not_guessed(self):
        for text in ('John chapter two verse three to','John 2:8-3','John 0:1','John 2:900','John said Matthew chapter two verse one','John 2:3 and 8','John chapter two verse three something else','unknown book 2:3'):
            with self.subTest(text=text):self.assertIsNone(spoken_reference(text))
    def test_missing_numbered_verse_prevents_partial_range(self):
        reference=spoken_reference('John 2:3-5')
        rows=[{'book':'John','chapter':'2','verse':str(n),'text':'text'} for n in (3,5)]
        self.assertEqual(validated_spoken_rows(rows,reference),[])
    def test_pcm_conversion_chunk_independence_and_stereo(self):
        data=b''.join(struct.pack('<hh',n*100,-n*50) for n in range(300))
        one=PcmConverter(48000,2,'int16').feed(data)
        conv=PcmConverter(48000,2,'int16');chunks=b''.join(conv.feed(data[i:i+17]) for i in range(0,len(data),17))
        self.assertEqual(one,chunks);self.assertGreater(len(one),100)
    def test_mjpeg_partial_headers_frames_and_limit(self):
        parser=JpegStream(100)
        self.assertEqual(parser.feed(b'HTTP header\r\n\xff'),[])
        self.assertEqual(parser.feed(b'\xd8abc'),[])
        self.assertEqual(parser.feed(b'\xff\xd9\r\n--boundary\xff\xd8d\xff\xd9'),[b'\xff\xd8abc\xff\xd9',b'\xff\xd8d\xff\xd9'])
        with self.assertRaises(ValueError):parser.feed(b'\xff\xd8'+b'a'*110)
    def test_full_speech_queue_can_be_cancelled_without_repeated_errors(self):
        worker=RecognitionWorker(Path("unused"))
        for _ in range(128):self.assertTrue(worker.submit(("pcm",b"audio",1)))
        self.assertFalse(worker.submit(("pcm",b"audio",1)))
        self.assertTrue(worker.submit(("cancel",None,2)))
        self.assertEqual(worker.commands.qsize(),1)
    def test_stream_url_validation(self):
        self.assertEqual(camera_url('http://192.168.1.5:8080/video','MJPEG'),'http://192.168.1.5:8080/video')
        for url in ('file:///secret','https://user:pass@example.com/video','http://','http://example.com:bad/video'):
            with self.assertRaises(ValueError):camera_url(url,'MJPEG')

class CaptureUITests(unittest.TestCase):
    setUpClass=classmethod(test_ui.UITests.setUpClass.__func__)
    setUp=test_ui.UITests.setUp
    tearDown=test_ui.UITests.tearDown
    search=test_ui.UITests.search
    open_live=test_ui.UITests.open_live
    def prepare(self):self.w.build_capture_dialog();self.search('John 3:16');self.w.verse_live();self.before=self.w.live.current
    def test_voice_auto_live_range_and_normal_bible_keyboard_flow(self):
        self.prepare();self.w.voice_text.setText('John chapter two verses three to eight')
        self.w.voice_lookup(self.w.voice_text.text(),.95,True)
        self.assertIn('John 2:3',self.w.live.current['reference']);self.assertEqual(self.w.live.current['passage_reference'],'John 2:3-8')
        self.w.next_live();self.assertIn('John 2:4',self.w.live.current['reference'])
    def test_low_confidence_words_and_invalid_reference_keep_live(self):
        self.prepare()
        for text,confidence in [('John chapter two verse three',.4),('valley shadow death',.98),('John chapter two verse ninety nine',.99)]:
            self.w.voice_text.setText(text);self.w.voice_lookup(text,confidence,True)
            self.assertEqual(self.w.live.current,self.before)
        self.w.voice_text.setText('valley shadow death');self.w.voice_lookup('valley shadow death',.9,True)
        self.assertTrue(self.w.voice_rows);self.w.send_voice_selected();self.assertIn('Psalms 23:4',self.w.live.current['reference'])
    def test_missing_voice_engine_model_does_not_change_bible(self):
        self.prepare()
        with patch('offline_voice.model_path',return_value=None):self.w.voice_capture.begin()
        self.assertEqual(self.w.live.current,self.before);self.assertIsNone(self.w.voice_capture.worker)
    def test_camera_preview_live_bible_return_failure_and_black_restore(self):
        self.prepare();self.w.camera_session.begin()
        image=QImage(640,360,QImage.Format_RGB32);image.fill(QColor('#cc3344'));self.w.camera_session.accept_frame(image)
        self.assertEqual(self.w.live.current,self.before)
        self.w.camera_live();self.assertEqual(self.w.live.current['kind'],'camera')
        self.assertIsInstance(self.w.live.content_layout.itemAt(0).widget(),CameraView)
        self.w.black_outputs();self.assertTrue(self.w.live.black)
        self.w.restore_outputs();self.assertFalse(self.w.live.black)
        self.w.return_bible_live();self.assertEqual(self.w.live.current,self.before)
        self.w.camera_live();self.w.camera_session.fail('Disconnected')
        self.assertTrue(self.w.live.black);self.w.return_bible_live();self.assertEqual(self.w.live.current,self.before)
    def test_disconnected_camera_cannot_replace_bible_and_export_roundtrip(self):
        self.prepare();self.w.items.append({'kind':'camera','title':'Live camera'});self.w.refresh_list(preview=False);self.w.list.setCurrentRow(len(self.w.items)-1);self.w.go_live()
        self.assertEqual(self.w.live.current,self.before)
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'service.chablesz';save_package(target,self.w.items)
            imported=load_package(target,Path(d)/'assets');self.assertEqual(imported[-1]['kind'],'camera')
    def test_changed_translation_while_listening_does_not_go_live(self):
        self.prepare();self.w.voice_version='KJV';self.w.version.setCurrentText('ASV');self.w.voice_result('John chapter two verse three',.99)
        self.assertEqual(self.w.live.current,self.before)
    def test_capture_panel_fits_small_laptop(self):
        self.w.build_capture_dialog();self.w.capture_dialog.resize(790,660);self.w.capture_dialog.show();self.qt.processEvents()
        self.assertLessEqual(self.w.capture_dialog.minimumSizeHint().width(),790)
        self.assertLessEqual(self.w.capture_dialog.minimumSizeHint().height(),660)
if __name__=='__main__':unittest.main()
