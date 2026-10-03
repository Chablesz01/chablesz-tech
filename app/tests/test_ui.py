import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX','1')
import tempfile
import unittest
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QIODevice
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication,QWidget
import app as module
from projection import SlideCanvas,slide_parts

class FakeReply(QNetworkReply):
    def __init__(self,data,parent):
        super().__init__(parent);self.data=data
        self.open(QIODevice.ReadOnly)
        QTimer.singleShot(10,self.deliver)
    def deliver(self):
        self.readyRead.emit();self.setFinished(True);self.finished.emit()
    def readData(self,count):
        chunk=self.data[:count];self.data=self.data[count:];return chunk
    def bytesAvailable(self):return len(self.data)+super().bytesAvailable()
    def abort(self):
        self.setError(QNetworkReply.OperationCanceledError,'Cancelled');self.setFinished(True);self.finished.emit()

class FakeNetwork:
    def __init__(self,parent,data):self.parent,self.data=parent,data
    def get(self,request):return FakeReply(self.data,self.parent)

class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.qt=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        for name,sub in [('DATA',''),('MEDIA','slides'),('ASSETS','agenda_media'),('BIBLES','bibles')]:
            path=root/sub;path.mkdir(exist_ok=True);setattr(module,name,path)
        self.w=module.App();self.w.show();self.qt.processEvents()
        self.w.open_live=self.open_live
    def open_live(self):
        self.w.live.resize(1280,720);self.w.live.show();self.qt.processEvents();return True
    def tearDown(self):
        self.w.close();self.qt.processEvents();self.w.deleteLater();self.qt.processEvents();self.temp.cleanup()
    def search(self,q):self.w.query.setText(q);self.w.search_bible();self.qt.processEvents()

    def test_laptop_sizes_and_all_library_sections(self):
        for width,height in [(1024,600),(1366,768),(1920,1080)]:
            self.w.resize(width,height);self.qt.processEvents()
            self.assertEqual(self.w.width(),width);self.assertEqual(self.w.height(),height)
            self.assertLessEqual(self.w.minimumSizeHint().height(),600)
            for index in range(5):
                self.w.library_section.setCurrentIndex(index);self.qt.processEvents()
                scroll=self.w.library_pages.currentWidget()
                self.assertLessEqual(scroll.widget().width(),scroll.viewport().width())
            self.w.library_section.setCurrentIndex(0)

    def test_preview_does_not_change_live_and_invalid_verse_cannot_go_live(self):
        self.search('John 3:16');self.w.verse_live();self.qt.processEvents()
        live=self.w.live.current
        self.search('Ps 23:1');self.assertEqual(self.w.live.current,live)
        self.assertIn('Psalms 23:1',self.w.preview.current['title'])
        self.search('John 999:1');self.w.verse_live()
        self.assertEqual(self.w.live.current,live)
        self.assertEqual(len(self.w.items),1)

    def test_navigation_across_chapters_and_chapter_query(self):
        self.search('John 3:36');self.w.step_bible(1)
        self.assertEqual(self.w.selected_verse()['reference'],'John 4:1 (KJV)')
        self.w.step_bible(-1);self.assertEqual(self.w.selected_verse()['reference'],'John 3:36 (KJV)')
        self.w.show_chapter();self.assertEqual(len(self.w.matches),36)
        self.w.query.setText('Ps 119');self.w.search_bible();self.assertEqual(len(self.w.matches),176)

    def test_fit_at_preview_720p_and_1080p_and_no_old_visible_content(self):
        stage=self.w.live;stage.show()
        item={'kind':'verse','body':' '.join(['A long verse with many words.']*35),'reference':'John 3:16 (KJV)','title':'Long verse'}
        for width,height in [(320,180),(1280,720),(1920,1080)]:
            stage.resize(width,height);stage.display(item);self.qt.processEvents();stage.grab()
            canvas=stage.content_layout.itemAt(0).widget()
            self.assertIsInstance(canvas,SlideCanvas)
            self.assertLessEqual(canvas.document_height,canvas.body_rect.height()+1)
            self.assertLessEqual(canvas.body_rect.bottom(),canvas.reference_rect.top())
        old=stage.content_layout.itemAt(0).widget()
        stage.display(dict(item,body='New verse'))
        self.assertFalse(old.isVisible())
        self.assertEqual(stage.content_layout.count(),1)
        self.assertEqual(slide_parts({'kind':'verse','text':'Old format\n\nJohn 1:1 (KJV)'}),('Old format','John 1:1 (KJV)'))

    def test_alert_lane_and_blackout_restore(self):
        self.search('John 3:16');self.w.verse_live();stage=self.w.live
        for motion in ['Static','Scroll left','Scroll right','Rolling','Bottom to top','Top to bottom']:
            stage.settings['alert_motion']=motion;stage.set_alert('Please silence your phones');self.qt.processEvents()
            self.assertTrue(stage.alert_host.isVisible())
            self.assertFalse(stage.content.geometry().intersects(stage.alert_host.geometry()))
            stage.animate_alert()
        stage.blackout(True);self.qt.processEvents()
        self.assertEqual(stage.grab().toImage().pixelColor(100,100).name(),'#000000')
        self.assertFalse(stage.alert_host.isVisible())
        stage.blackout(False);self.qt.processEvents()
        self.assertTrue(stage.alert_host.isVisible());self.assertEqual(stage.content_layout.count(),1)
        stage.clear_alert();self.qt.processEvents();self.assertFalse(stage.alert_host.isVisible())

    def test_invalid_import_preserves_previous_library(self):
        self.w.install_bible([{'book':'John','chapter':'3','verse':'16','text':'Valid verse'}])
        path=module.BIBLES/'KJV.csv';before=path.read_bytes()
        with self.assertRaises(ValueError):self.w.install_bible([{'book':'John','chapter':'bad','verse':'16','text':'bad'}])
        self.assertEqual(path.read_bytes(),before)

    def test_async_import_retains_target_translation(self):
        self.search('John 3:16');self.w.verse_live();live=self.w.live.current
        self.w.network=FakeNetwork(self.w,b'book,chapter,verse,text\nJohn,3,16,Test verse\n')
        self.w.download_csv('https://example.com/bible.csv',lambda rows:self.w.install_bible(rows,'ASV'),'Bible ASV')
        self.assertEqual(len(self.w.online_replies),1)
        self.w.version.setCurrentText('WEB')
        from PySide6.QtTest import QTest
        QTest.qWait(40)
        self.assertTrue((module.BIBLES/'ASV.csv').exists());self.assertEqual(self.w.version.currentText(),'WEB')
        self.assertEqual(self.w.live.current,live);self.assertEqual(self.w.online_replies,[])

    def test_options_pages_fit_and_apply_preserves_bible_preview(self):
        self.search('John 3:16');dialog=module.OptionsDialog(self.w);dialog.show();dialog.resize(820,580)
        for index in range(len(dialog.SECTIONS)):
            dialog.navigation.setCurrentRow(index);self.qt.processEvents()
            self.assertLessEqual(dialog.width(),820);self.assertLessEqual(dialog.height(),580)
        dialog.controls['font_size'].setValue(60)
        self.assertTrue(dialog.apply_changes());self.assertEqual(self.w.live.settings['font_size'],60)
        self.assertEqual(self.w.preview.current['kind'],'verse');dialog.close()

if __name__=='__main__': unittest.main()
