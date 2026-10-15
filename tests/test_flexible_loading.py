from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock

from src.excel_reader import find_project_excels, read_project_excel
from src.image_inventory import inventory_project_images
from src.ui.main_window import validate_root_folder
from src.output_generator import _find_project_inputs, _select_source_images


class FlexibleLoadingTests(unittest.TestCase):
    def test_arbitrary_names_nested_excel_and_images(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'Nombre libre á 2026'
            folder = root / 'Fotos sin formato' / 'Vuelo cualquiera'
            folder.mkdir(parents=True)
            excel = root / 'Trazabilidad MAAT One libre.XLSX'
            excel.touch()
            image = folder / 'DJI_20261015120000_0001_Z.JPG'
            image.write_bytes(b'image')
            direct = root / 'otra.jpg'
            direct.touch()
            self.assertTrue(validate_root_folder(root).startswith('Proyecto válido'))
            self.assertEqual(find_project_excels(root), [excel])
            images, duplicates = inventory_project_images(root)
            self.assertEqual({item['name'] for item in images}, {image.name, direct.name})
            self.assertEqual(duplicates, {})
            self.assertEqual(_find_project_inputs(root), (root, excel))
            self.assertEqual(len(_select_source_images(root, [{'zoom': {'name': image.name, 'path': str(image)}}])), 1)
            nested_excel = folder / 'datos libres.xls'
            excel.rename(nested_excel)
            self.assertTrue(validate_root_folder(root).startswith('Proyecto válido'))
            workbook = MagicMock()
            workbook.sheet_names = ['Hoja libre']
            workbook.get_sheet_by_name.return_value.to_python.return_value = [
                ['Fecha', 'Hora', 'Elemento', 'Anomalia', 'Recomendacion', 'Criticidad', 'Archivo'], ['2026-10-15', '', 'Poste', '', '', '', 120000]]
            with patch('src.excel_reader.CalamineWorkbook') as reader:
                reader.from_path.return_value = workbook
                records = read_project_excel(root)
                reader.from_path.assert_called_once_with(nested_excel)
                self.assertEqual(records[0]['file_reference'], 120000)

    def test_zero_or_multiple_excel_rejected_regardless_of_names(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(validate_root_folder(root).startswith('Proyecto no válido'))
            (root / 'datos.xlsx').touch()
            (root / 'sub').mkdir()
            (root / 'sub' / 'Trazabilidad MAAT One algo.xls').touch()
            self.assertIn('más de un', validate_root_folder(root))
            with self.assertRaises(ValueError):
                read_project_excel(root)

    def test_duplicate_image_protection_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'uno').mkdir()
            (root / 'dos').mkdir()
            (root / 'uno' / 'foto.jpg').touch()
            (root / 'dos' / 'foto.jpg').touch()
            images, duplicates = inventory_project_images(root)
            self.assertEqual(len(images), 2)
            self.assertEqual(len(duplicates['foto.jpg']), 2)


if __name__ == '__main__':
    unittest.main()
