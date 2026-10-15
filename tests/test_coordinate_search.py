"""Pruebas del motor independiente del buscador geográfico."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from src.coordinate_search import (
    CoordinateSearchError,
    save_coordinate_search,
    search_images_by_coordinates,
    validate_search_parameters,
)


class FakeGPSCache:
    coordinates_by_name = {
        "near.jpg": {"latitude": -34.0, "longitude": -58.0, "source": "EXIF"},
        "far.jpg": {"latitude": -34.001, "longitude": -58.0, "source": "XMP"},
    }

    def coordinates(self, path: str | Path):
        return self.coordinates_by_name.get(Path(path).name)


class CoordinateSearchTests(unittest.TestCase):
    def test_validates_decimal_commas_and_radius(self) -> None:
        target, radius = validate_search_parameters("-34,123456", "-58.25", "1,5")
        self.assertEqual(target["latitude"], -34.123456)
        self.assertEqual(target["longitude"], -58.25)
        self.assertEqual(radius, 1.5)
        with self.assertRaises(CoordinateSearchError):
            validate_search_parameters("91", "0", "1")

    def test_search_keeps_nearest_and_filters_radius(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("near.jpg", "far.jpg", "no-gps.jpg"):
                (root / name).write_bytes(b"image")
            with patch("src.coordinate_search.GPSMetadataCache", FakeGPSCache):
                result = search_images_by_coordinates(
                    root,
                    {"latitude": -34.0, "longitude": -58.0},
                    1.0,
                )
            self.assertEqual([item["name"] for item in result["matches"]], ["near.jpg"])
            self.assertEqual(result["nearest"]["name"], "near.jpg")
            self.assertEqual(result["metadata_missing"], 1)

    def test_save_copies_images_and_writes_portable_kmz(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "near.jpg"
            source.write_bytes(b"image")
            selected = [{
                "path": source,
                "name": source.name,
                "coordinates": {"latitude": -34.0, "longitude": -58.0, "source": "EXIF"},
                "distance_meters": 0.0,
            }]
            result = save_coordinate_search(
                root, {"latitude": -34.0, "longitude": -58.0}, 1.0, selected
            )
            self.assertTrue((result["images_folder"] / "near.jpg").is_file())
            with ZipFile(result["kmz_path"]) as archive:
                self.assertIn("doc.kml", archive.namelist())
                self.assertIn("files/near.jpg", archive.namelist())


if __name__ == "__main__":
    unittest.main()
