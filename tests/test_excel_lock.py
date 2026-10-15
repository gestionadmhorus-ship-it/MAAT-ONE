from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from src.excel_reader import find_project_excels

class ExcelLockTests(unittest.TestCase):
    def test_lock_is_not_a_second_workbook(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            original=root/'original.xlsx'; original.touch()
            (root/'~$original.xlsx').write_bytes(b'lock')
            self.assertEqual(find_project_excels(root),[original])
            (root/'second.xls').touch()
            self.assertEqual(len(find_project_excels(root)),2)
