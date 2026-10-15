"""Inventario de imágenes JPG contenidas en la raíz y sus subcarpetas."""

from pathlib import Path
from src.project_files import input_files


def build_image_inventory(root_folder: Path) -> list[dict[str, str]]:
    """Recorre la raíz y sus subcarpetas y conserva nombre y ruta de cada JPG."""
    images = [
        {"name": path.name, "path": str(path.resolve())}
        for path in input_files(root_folder)
        if path.is_file() and path.suffix.casefold() == ".jpg"
    ]
    return sorted(images, key=lambda image: image["path"].casefold())


def detect_duplicate_names(
    images: list[dict[str, str]],
) -> dict[str, list[str]]:
    """Agrupa todas las rutas de nombres repetidos sin distinguir el caso."""
    groups: dict[str, list[dict[str, str]]] = {}
    for image in images:
        groups.setdefault(image["name"].casefold(), []).append(image)

    return {
        group[0]["name"]: [image["path"] for image in group]
        for group in groups.values()
        if len(group) > 1
    }


def inventory_project_images(
    root_folder: Path,
) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    """Construye el inventario y detecta duplicados dentro de la raíz y sus subcarpetas."""
    images = build_image_inventory(root_folder)
    return images, detect_duplicate_names(images)
