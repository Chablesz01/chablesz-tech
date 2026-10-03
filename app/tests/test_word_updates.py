import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
import test_ui
from word_finder import find_words,WordFinder
from release_package import checked_manifest,validate_package,FILES
from update_apply import apply_release

class WordUpdateTests(unittest.TestCase):
    setUpClass=classmethod(test_ui.UITests.setUpClass.__func__)
    setUp=test_ui.UITests.setUp
    tearDown=test_ui.UITests.tearDown
    search=test_ui.UITests.search
    open_live=test_ui.UITests.open_live

    def test_remembered_words_phrase_and_book_filter(self):
        rows=self.w.verses
        found=find_words(rows,'valley shadow death')
        self.assertTrue(any(r['book']=='Psalms' and r['chapter']=='23' and r['verse']=='4' for r in found))
        phrase=find_words(rows,'valley of the shadow of death','Exact phrase','Psalms')
        self.assertTrue(any(r['chapter']=='23' and r['verse']=='4' for r in phrase))
        self.assertTrue(all(r['book']=='Psalms' for r in phrase))
        self.assertEqual(find_words(rows,'notexistingword'),[])
        sample=[{'book':'John','chapter':'1','verse':'1','text':'Love is strong; beloved is loved.'}]
        self.assertEqual(find_words(sample,'lov'),[])
        self.assertEqual(len(find_words(sample,'STRONG')) ,1)

    def test_finder_sends_chapter_from_selected_result(self):
        finder=WordFinder(self.w);finder.query.setText('valley shadow death');finder.search()
        row=next(i for i,r in enumerate(finder.found) if r['book']=='Psalms' and r['chapter']=='23' and r['verse']=='4')
        finder.results.setCurrentRow(row);finder.send('chapter')
        self.assertIn('Psalms 23:4',self.w.live.current['reference'])
        self.w.next_live();self.assertIn('Psalms 23:5',self.w.live.current['reference'])
        finder.close()

    def test_update_guard_and_missing_feed_do_not_change_live(self):
        self.search('John 3:16');self.w.verse_live();before=self.w.live.current
        self.w.pending_update={'path':'not-applied.zip','version':16}
        self.assertFalse(self.w.apply_downloaded_update())
        self.assertEqual(self.w.live.current,before)
        self.assertIn('checked full Windows installer',self.w.status.text())
        self.w.pending_update=None;self.w.update_url.clear();self.w.refresh_app()
        self.assertIn('checked Windows installer',self.w.status.text())
        self.assertEqual(self.w.live.current,before)

    def test_release_manifest_package_and_transactional_rollback(self):
        manifest={'app':'ChableszShow','version':16,'url':'https://example.com/release.zip','sha256':'0'*64,'size':3000}
        self.assertEqual(checked_manifest(json.dumps(manifest))['version'],16)
        for value in ['http://example.com/x','https://user:pw@example.com/x']:
            with self.assertRaises(ValueError):checked_manifest(json.dumps(dict(manifest,url=value)))
        root=Path(self.temp.name)/'fake_install';root.mkdir()
        (root/'app.py').write_text('old app');(root/'requirements.txt').write_text('old deps')
        package=Path(self.temp.name)/'release.zip'
        with zipfile.ZipFile(package,'w') as z:
            for name in FILES:
                if name.endswith('.py') or name=='requirements.txt':
                    text='VERSION=16\n' if name=='release_info.py' else 'new deps' if name=='requirements.txt' else 'new app'
                    z.writestr('ChableszShow/'+name,text)
        self.assertIn('app.py',validate_package(package,16))
        with patch('update_apply.subprocess.run',side_effect=RuntimeError('dependency failed')):
            with self.assertRaises(RuntimeError):apply_release(package,root,16)
        self.assertEqual((root/'app.py').read_text(),'old app')
        self.assertEqual((root/'requirements.txt').read_text(),'old deps')
        self.assertFalse((root/'word_finder.py').exists())
        bad=Path(self.temp.name)/'bad.zip'
        with zipfile.ZipFile(bad,'w') as z:z.writestr('ChableszShow/../outside.txt','bad')
        with self.assertRaises(ValueError):validate_package(bad,16)
