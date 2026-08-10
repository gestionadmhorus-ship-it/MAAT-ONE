"""Inventario de imágenes JPG contenidas en Materiales."""

from pathlib import Path


def find_materials_folder(root_folder: Path) -> Path:
    """Localiza la carpeta Materiales entre los hijos directos de la raíz."""
    for entry in root_folder.iterdir():
        if entry.is_dir() and entry.name.casefold() == "materiales":
            return entry
    raise ValueError("No se encontró la carpeta Materiales.")


def build_image_inventory(materials_folder: Path) -> list[dict[str, str]]:
    """Recorre Materiales y conserva nombre y ruta de cada JPG."""
    images = [
        {"name": path.name, "path": str(path.resolve())}
        for path in materials_folder.rglob("*")
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
    """Construye el inventario y detecta duplicados dentro de Materiales."""
    materials_folder = find_materials_folder(root_folder)
    images = build_image_inventory(materials_folder)
    return images, detect_duplicate_names(images)
