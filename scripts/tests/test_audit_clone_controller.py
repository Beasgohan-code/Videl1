"""Offline tests for the read-only clone migration import inventory."""
import tempfile
import unittest
from pathlib import Path

from scripts.audit_clone_controller import imports_in_source, inventory


class CloneImportInventoryTests(unittest.TestCase):
    def test_static_import_forms_and_dynamic_literals(self):
        source = '''\nfrom filestore.worker_bot.engine import worker_engine\nfrom filestore.worker_bot import engine, group_profile\nimport filestore.worker_bot.group_filters as gf\nimportlib.import_module("filestore.worker_bot.group_notes")\n__import__("filestore.worker_bot.engine")\nimport_module(variable)\n# from filestore.worker_bot.engine import bogus\nvalue = "filestore.worker_bot.group_ui"\n'''
        rows = imports_in_source(source, "example.py")
        self.assertEqual([(r['line'], r['category'], r['form']) for r in rows], [
            (2, 'engine', 'from'), (3, 'engine', 'from'), (3, 'group', 'from'),
            (4, 'group', 'import'), (5, 'group', 'dynamic-literal'), (6, 'engine', 'dynamic-literal'),
        ])

    def test_inventory_excludes_tests_and_reports_errors_without_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tests').mkdir()
            (root / 'tests' / 'skip.py').write_text('import filestore.worker_bot.engine')
            (root / 'one.py').write_text('from filestore.worker_bot.engine import worker_engine')
            (root / 'broken.py').write_text('synthetic_secret = (')
            result = inventory(root)
            self.assertEqual([r['path'] for r in result['references']], ['VidelBot/one.py'])
            self.assertEqual(result['errors'], [{'path': 'VidelBot/broken.py', 'error': 'SyntaxError'}])
            self.assertNotIn('synthetic_secret', str(result))

    def test_repository_finds_engine_entry_and_group_dependencies(self):
        result = inventory()
        self.assertFalse(result['errors'])
        self.assertTrue(any(r['path'] == 'VidelBot/run.py' and r['category'] == 'engine' for r in result['references']))
        self.assertTrue(any(r['path'] == 'VidelBot/filestore/worker_bot/engine.py' and r['module'].endswith('.group_profile') for r in result['references']))

    def test_missing_root_fails_instead_of_empty_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                inventory(Path(directory) / 'missing')


if __name__ == '__main__':
    unittest.main()
