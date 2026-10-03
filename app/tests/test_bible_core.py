import csv
import unittest
from pathlib import Path
from bible_core import normalize_rows,search_rows,paginate

class BibleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (Path(__file__).resolve().parents[1]/'bundled_bibles/KJV.csv').open(encoding='utf-8') as f:
            cls.rows=normalize_rows(list(csv.DictReader(f)))

    def test_complete_kjv(self):
        self.assertEqual(len(self.rows),31102)
        self.assertEqual(len({r['book'] for r in self.rows}),66)
        self.assertEqual(self.rows[0]['book'],'Genesis')
        self.assertEqual(self.rows[-1]['book'],'Revelation')
        self.assertEqual(len(search_rows(self.rows,'Ps 119')),176)

    def test_references_ranges_and_aliases(self):
        for q in ['John 3:16-18','Jn3:16–18','John 3 : 16 - 18']:
            self.assertEqual([r['verse'] for r in search_rows(self.rows,q)],['16','17','18'])
        self.assertEqual(len(search_rows(self.rows,'1 Jn 1')),10)
        self.assertEqual(len(search_rows(self.rows,'Psalm 23')),6)
        self.assertFalse(search_rows(self.rows,'John 3:18-16'))
        self.assertTrue(search_rows(self.rows,'only begotten Son'))

    def test_validation_and_sorting(self):
        rows=[{'Book':' John ','Chapter':'03','Verse':'17','Text':'B'},
              {'Book':'John','Chapter':'3','Verse':'16','Text':'A'}]
        result=normalize_rows(rows)
        self.assertEqual([r['verse'] for r in result],['16','17'])
        self.assertEqual(result[0]['chapter'],'3')
        with self.assertRaises(ValueError): normalize_rows([{'book':'John','chapter':'x','verse':'1','text':'a'}])
        with self.assertRaises(ValueError): normalize_rows([{'book':'John','chapter':'3','verse':'0','text':'a'}])
        with self.assertRaises(ValueError): normalize_rows([{'book':'John','chapter':'3','verse':'1','text':''}])
        with self.assertRaises(ValueError): normalize_rows([{'book':'John','chapter':'3','verse':'1','text':'A'},
            {'book':'John','chapter':'3','verse':'1','text':'B'}])

    def test_long_verse_keeps_all_words(self):
        row={'book':'John','chapter':'3','verse':'16','text':' '.join('word'+str(i) for i in range(400))}
        slides=paginate([row],'KJV',1,300)
        self.assertGreater(len(slides),1)
        self.assertEqual(' '.join(s['body'] for s in slides),row['text'])
        self.assertTrue(all(s['reference']=='John 3:16 (KJV)' for s in slides))
        self.assertTrue(all(len(s['body'])<=300 for s in slides))

    def test_passages_do_not_merge_chapters_or_nonconsecutive_hits(self):
        rows=search_rows(self.rows,'John 3:35-36')+search_rows(self.rows,'John 4:1')
        slides=paginate(rows,'KJV',10,1000)
        self.assertEqual([s['reference'] for s in slides],['John 3:35–36 (KJV)','John 4:1 (KJV)'])
        slides=paginate([self.rows[0],self.rows[2]],'KJV',10,1000)
        self.assertEqual(len(slides),2)

if __name__=='__main__': unittest.main()
