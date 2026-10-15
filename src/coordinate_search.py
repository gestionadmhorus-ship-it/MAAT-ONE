"""Búsqueda geográfica de imágenes y exportación portátil a KMZ.

Este módulo no depende de la interfaz de MAAT One: así el buscador puede
validarse y evolucionar sin afectar el flujo de preparación de SET.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from shutil import copy2, rmtree
from typing import Callable
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from src.gps_metadata import GPSMetadataCache, horizontal_distance


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}
DEFAULT_RADIUS_METERS = 1.0


class CoordinateSearchError(ValueError):
    """Error de entrada o de generación del buscador geográfico."""


def validate_search_parameters(
    latitude: object,
    longitude: object,
    radius_meters: object,
) -> tuple[dict[str, float], float]:
    """Convierte y valida coordenadas decimales y radio en metros.

    Las coordenadas se conservan con toda la precisión que haya indicado la
    persona usuaria. El valor predeterminado de 1 m es un radio, no un
    redondeo de coordenadas; redondear alteraría la posición buscada.
    """
    try:
        latitude_value = float(str(latitude).strip().replace(",", "."))
        longitude_value = float(str(longitude).strip().replace(",", "."))
        radius_value = float(str(radius_meters).strip().replace(",", "."))
    except (TypeError, ValueError) as error:
        raise CoordinateSearchError(
            "Latitud, longitud y radio deben ser números válidos."
        ) from error
    if not -90.0 <= latitude_value <= 90.0:
        raise CoordinateSearchError("La latitud debe estar entre -90 y 90.")
    if not -180.0 <= longitude_value <= 180.0:
        raise CoordinateSearchError(
            "La longitud debe estar entre -180 y 180."
        )
    if radius_value <= 0 or radius_value > 100_000:
        raise CoordinateSearchError(
            "El radio debe ser mayor que 0 y no superar 100.000 metros."
        )
    return {
        "latitude": latitude_value,
        "longitude": longitude_value,
    }, radius_value


def find_images(root_folder: Path) -> list[Path]:
    """Devuelve imágenes soportadas bajo cualquier carpeta del origen."""
    try:
        return sorted(
            (
                path for path in root_folder.rglob("*")
                if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
                and not any(
                    parent.name.startswith("Búsqueda coordenadas")
                    or parent.name.startswith(".Búsqueda coordenadas")
                    for parent in path.relative_to(root_folder).parents
                )
            ),
            key=lambda path: str(path).casefold(),
        )
    except OSError as error:
        raise CoordinateSearchError(
            "No se pudo recorrer la carpeta seleccionada."
        ) from error


def search_images_by_coordinates(
    root_folder: str | Path,
    target: dict[str, float],
    radius_meters: float,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, object]:
    """Busca imágenes con GPS dentro del radio y conserva la más cercana."""
    root = Path(root_folder)
    if not root.is_dir():
        raise CoordinateSearchError("La carpeta raíz seleccionada no existe.")
    images = find_images(root)
    cache = GPSMetadataCache()
    matches: list[dict[str, object]] = []
    nearest: dict[str, object] | None = None
    metadata_missing = 0
    total = len(images)
    for index, path in enumerate(images, start=1):
        coordinates = cache.coordinates(path)
        if coordinates is None:
            metadata_missing += 1
        else:
            distance = horizontal_distance(target, coordinates)
            candidate = {
                "path": path,
                "name": path.name,
                "coordinates": coordinates,
                "distance_meters": distance,
            }
            if nearest is None or distance < nearest["distance_meters"]:
                nearest = candidate
            if distance <= radius_meters:
                matches.append(candidate)
        if progress is not None and (index == total or index % 20 == 0):
            progress(index, total)
    matches.sort(key=lambda item: (item["distance_meters"], item["name"].casefold()))
    return {
        "root_folder": root,
        "target": dict(target),
        "radius_meters": radius_meters,
        "images_scanned": total,
        "metadata_missing": metadata_missing,
        "matches": matches,
        "nearest": nearest,
    }


def _unique_name(name: str, used_names: set[str]) -> str:
    candidate = Path(name)
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    resolved = name
    while resolved.casefold() in used_names:
        resolved = f"{stem} ({index}){suffix}"
        index += 1
    used_names.add(resolved.casefold())
    return resolved


def _kml_document(items: list[dict[str, object]], archive_names: dict[Path, str]) -> str:
    placemarks: list[str] = []
    for item in items:
        coordinates = item["coordinates"]
        path = Path(item["path"])
        archive_name = archive_names[path]
        description = (
            "<![CDATA[<p><b>Distancia:</b> "
            f"{item['distance_meters']:.2f} m</p>"
            f"<img src=\"files/{escape(archive_name, quote=True)}\" "
            "width=\"600\"/>]]>"
        )
        placemarks.append(
            "<Placemark>"
            f"<name>{escape(path.name)}</name>"
            f"<description>{description}</description>"
            "<Point><coordinates>"
            f"{coordinates['longitude']:.8f},{coordinates['latitude']:.8f},0"
            "</coordinates></Point>"
            "</Placemark>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<name>MAAT One - Búsqueda por coordenadas</name>"
        + "".join(placemarks)
        + "</Document></kml>"
    )


def save_coordinate_search(
    root_folder: str | Path,
    target: dict[str, float],
    radius_meters: float,
    selected_items: list[dict[str, object]],
) -> dict[str, object]:
    """Copia la selección y crea un KMZ autocontenido en la misma raíz.

    La salida se prepara en una carpeta temporal y solo se publica al final,
    evitando dejar resultados a medio generar ante un fallo de copia.
    """
    root = Path(root_folder)
    if not root.is_dir():
        raise CoordinateSearchError("La carpeta raíz seleccionada no existe.")
    if not selected_items:
        raise CoordinateSearchError("Seleccione al menos una imagen para guardar.")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = root / f"Búsqueda coordenadas {timestamp}"
    while destination.exists():
        destination = root / f"Búsqueda coordenadas {timestamp}-{uuid4().hex[:6]}"
    temporary = root / f".{destination.name}.tmp-{uuid4().hex}"
    images_folder = temporary / "Imágenes"
    kmz_path = temporary / "Imágenes seleccionadas.kmz"
    archive_names: dict[Path, str] = {}
    used_names: set[str] = set()
    try:
        images_folder.mkdir(parents=True)
        for item in selected_items:
            source = Path(item["path"])
            if not source.is_file():
                raise CoordinateSearchError(
                    f"La imagen ya no está disponible: {source.name}"
                )
            output_name = _unique_name(source.name, used_names)
            copy2(source, images_folder / output_name)
            archive_names[source] = output_name
        with ZipFile(kmz_path, "w", ZIP_DEFLATED) as archive:
            archive.writestr("doc.kml", _kml_document(selected_items, archive_names))
            for source, archive_name in archive_names.items():
                archive.write(source, f"files/{archive_name}")
        (temporary / "README.txt").write_text(
            "Resultados de MAAT One - Búsqueda por coordenadas\n"
            f"Latitud: {target['latitude']:.8f}\n"
            f"Longitud: {target['longitude']:.8f}\n"
            f"Radio: {radius_meters:.2f} m\n"
            f"Imágenes seleccionadas: {len(selected_items)}\n",
            encoding="utf-8",
        )
        temporary.rename(destination)
    except (OSError, CoordinateSearchError) as error:
        rmtree(temporary, ignore_errors=True)
        if isinstance(error, CoordinateSearchError):
            raise
        raise CoordinateSearchError(
            f"No se pudo guardar el resultado: {error}"
        ) from error
    return {
        "destination": destination,
        "images_folder": destination / "Imágenes",
        "kmz_path": destination / "Imágenes seleccionadas.kmz",
        "images_saved": len(selected_items),
    }
