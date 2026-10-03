import importlib.util, pathlib, tempfile, unittest
from unittest.mock import patch
HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('launcher',HERE/'launcher.py');launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
spec=importlib.util.spec_from_file_location('payload_builder',HERE/'build_payload.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
class PackagingChecks(unittest.TestCase):
    def test_migration_preserves_original_and_existing_new_data(self):
        with tempfile.TemporaryDirectory() as d:
            old=pathlib.Path(d)/'old';old.mkdir();(old/'songs.json').write_text('[{"title":"Old"}]')
            (old/'bibles').mkdir();(old/'bibles/KJV.csv').write_text('original')
            new=pathlib.Path(d)/'new';launcher.migrate(old,new)
            self.assertEqual((new/'songs.json').read_text(),(old/'songs.json').read_text())
            (new/'songs.json').write_text('new library');launcher.migrate(old,new)
            self.assertEqual((new/'songs.json').read_text(),'new library')
            self.assertEqual((old/'bibles/KJV.csv').read_text(),'original')
    def test_archive_paths_cannot_escape_payload(self):
        for name in ('../escape','/absolute','C:/absolute','a\\evil'):
            with self.assertRaises(ValueError):builder.safe_path(pathlib.Path('/tmp/payload'),name)
    def test_existing_invalid_dependency_is_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'dependency.zip';p.write_bytes(b'wrong')
            with patch.object(builder.urllib.request,'urlopen',side_effect=RuntimeError('network blocked')):
                with self.assertRaises(RuntimeError):builder.fetch('https://example.com/wheel',p,'0'*64)
if __name__=='__main__':unittest.main()
