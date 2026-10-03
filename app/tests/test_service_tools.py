import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import test_ui
import app as module
from service_tools import song_sections

class ServiceTests(test_ui.unittest.TestCase):
    setUpClass=classmethod(test_ui.UITests.setUpClass.__func__)
    setUp=test_ui.UITests.setUp
    tearDown=test_ui.UITests.tearDown
    search=test_ui.UITests.search
    open_live=test_ui.UITests.open_live

    def test_service_save_load_and_section_jump(self):
        self.w.add_sunday_outline();self.assertEqual(len(self.w.items),8)
        self.w.service_name.setText('My Sunday');self.w.save_service()
        self.w.items.append({'kind':'text','text':'Later','title':'Later'})
        self.w.load_service();self.assertEqual(len(self.w.items),8)
        self.assertTrue((module.DATA/'services'/'previous_agenda.json').exists())
        self.w.service_section.setCurrentText('Sermon');self.w.jump_service_section()
        self.assertEqual(self.w.preview.current['title'],'Sermon');self.assertIsNone(self.w.live.current)

    def test_bible_shortcut_and_continue_across_chapter(self):
        self.search('Genesis 2:25');self.w.verse_live();self.w.continue_bible()
        self.assertIn('Genesis 3:1',self.w.live.current['reference'])
        self.w.library_section.setCurrentIndex(5);self.w.activateWindow();self.qt.processEvents()
        QTest.keyClick(self.w,Qt.Key_B,Qt.ControlModifier);self.qt.processEvents()
        self.assertEqual(self.w.library_section.currentIndex(),0)
        self.assertTrue(self.w.query.hasFocus())

    def test_song_labels_repeat_and_section_live(self):
        song={'title':'Test song','category':'RCCG Hymn','lyrics':'Verse 1\nFirst lyric\n\nChorus\nChorus lyric\n\nVerse 2\nSecond lyric\n\nBridge\nBridge lyric'}
        slides=song_sections(song,5,True)
        self.assertEqual([s['song_section'] for s in slides],['Verse 1','Chorus','Verse 2','Chorus','Bridge'])
        self.assertNotIn('Verse 1',slides[0]['text'])
        self.w.songs=[song];self.w.search_songs();self.w.song_list.setCurrentRow(0)
        self.w.repeat_chorus.setChecked(True);self.w.queue_song()
        self.w.send_song_section(3);self.assertEqual(self.w.live.current['song_section'],'Chorus')

    def test_quick_controls_and_missing_media_readiness(self):
        self.w.quick_welcome.setText('Welcome church');self.w.quick_output('welcome')
        self.assertEqual(self.w.live.current['text'],'Welcome church')
        self.w.quick_output('clear');self.assertEqual(self.w.live.current['text'],'')
        self.assertFalse(self.w.live.brand.isVisible())
        self.w.items.append({'kind':'video','title':'Missing video','path':str(module.DATA/'missing.mp4')})
        self.assertTrue(any(state=='MISSING' and name=='Missing video' for state,name,_ in self.w.readiness_report()))
        self.w.quick_output('logo');self.assertEqual(self.w.live.current['title'],'Clear text')

    def test_recovery_after_restart_and_private_presenter_notes(self):
        self.search('Genesis 2:1-3');self.w.verse_live();self.w.next_live()
        self.w.seconds=120;self.w.stage_note.setText('Private minister note');self.w.save_recovery()
        saved=json.loads((module.DATA/'recovery.json').read_text())
        self.assertIn('Genesis 2:2',saved['live_item']['reference'])
        self.w.close();self.w.deleteLater();self.qt.processEvents()
        self.w=module.App();self.w.open_live=self.open_live;self.w.show();self.qt.processEvents()
        self.assertIsNone(self.w.live.current)
        self.w.resume_last_output();self.assertIn('Genesis 2:2',self.w.live.current['reference'])
        self.w.next_live();self.assertIn('Genesis 2:3',self.w.live.current['reference'])
        self.assertEqual(self.w.seconds,120)
        self.w.confidence.show();self.w.refresh_confidence();self.qt.processEvents()
        self.assertIn('Private minister note',self.w.confidence.notes_label.text())
        self.assertNotIn('Private minister note',self.w.live.current['text'])
        self.assertIn('3 ',self.w.confidence.current_label.text())
        self.assertIsNone(self.w.next_cue())

    def test_service_tools_fit_small_laptop(self):
        self.w.resize(1024,600);self.w.library_section.setCurrentIndex(5);self.qt.processEvents()
        scroll=self.w.library_pages.currentWidget()
        self.assertLessEqual(scroll.widget().width(),scroll.viewport().width())
