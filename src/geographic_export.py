"""Exportación segura de selecciones geográficas de MAAT One."""

from math import isfinite
from pathlib import Path
from html import escape
import shutil
import tempfile
from urllib.parse import quote
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from src.dji_parser import parse_dji_image


KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
INVALID_WINDOWS_CHARACTERS = frozenset('<>:"/\\|?*')
RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class GeographicExportError(Exception):
    """Error controlado de validación o generación geográfica."""


def validate_export_folder_name(requested_name: object) -> str:
    """Valida un único nombre de carpeta compatible con Windows."""
    if not isinstance(requested_name, str):
        raise GeographicExportError("El nombre de la carpeta debe ser texto.")
    name = requested_name
    if not name or not name.strip():
        raise GeographicExportError("El nombre de la carpeta no puede estar vacío.")
    if name != name.strip():
        raise GeographicExportError(
            "El nombre de la carpeta no puede empezar o terminar con espacios."
        )
    if Path(name).is_absolute():
        raise GeographicExportError("El nombre no puede ser una ruta absoluta.")
    if ".." in name:
        raise GeographicExportError("El nombre no puede contener '..'.")
    if any(character in INVALID_WINDOWS_CHARACTERS for character in name):
        raise GeographicExportError(
            "El nombre contiene caracteres no válidos para Windows."
        )
    if any(ord(character) < 32 for character in name):
        raise GeographicExportError("El nombre contiene caracteres de control.")
    if name.endswith("."):
        raise GeographicExportError("El nombre no puede terminar con un punto.")
    if name.split(".", 1)[0].upper() in RESERVED_WINDOWS_NAMES:
        raise GeographicExportError("El nombre está reservado por Windows.")
    return name


def _validated_selection(
    root_folder: Path,
    selected_images: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not selected_images:
        raise GeographicExportError("La selección de fotografías está vacía.")

    validated: list[dict[str, object]] = []
    names: set[str] = set()
    for image in selected_images:
        try:
            name = str(image["name"])
            source = Path(str(image["path"])).resolve(strict=True)
            latitude = float(image["latitude"])
            longitude = float(image["longitude"])
            distance_m = float(image["distance_m"])
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise GeographicExportError(
                "La selección contiene una fotografía no válida."
            ) from error
        if not source.is_file() or not source.is_relative_to(root_folder):
            raise GeographicExportError(
                f"La fotografía no pertenece al proyecto: {name}"
            )
        if source.name != name or Path(name).name != name:
            raise GeographicExportError(
                f"El nombre no coincide con el archivo original: {name}"
            )
        if (
            ".." in name
            or any(character in INVALID_WINDOWS_CHARACTERS for character in name)
            or any(ord(character) < 32 for character in name)
        ):
            raise GeographicExportError(
                f"El nombre de fotografía no es seguro para el KMZ: {name}"
            )
        if name.casefold() in names:
            raise GeographicExportError(
                f"La selección contiene un nombre duplicado: {name}"
            )
        if not isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise GeographicExportError(f"Latitud no válida para {name}.")
        if not isfinite(longitude) or not -180.0 <= longitude <= 180.0:
            raise GeographicExportError(f"Longitud no válida para {name}.")
        if not isfinite(distance_m) or distance_m < 0.0:
            raise GeographicExportError(f"Distancia no válida para {name}.")
        names.add(name.casefold())
        validated.append(
            {
                "name": name,
                "source": source,
                "latitude": latitude,
                "longitude": longitude,
                "distance_m": distance_m,
            }
        )
    return validated


def _group_images(
    images: list[dict[str, object]],
) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], dict[str, object]] = {}
    for index, image in enumerate(images):
        parsed = parse_dji_image(
            {"name": str(image["name"]), "path": str(image["source"])}
        )
        if parsed["recognized"]:
            key = (
                "DJI",
                parsed["date"],
                parsed["time"],
                parsed["sequence"],
            )
            pin_name = (
                f"DJI_{parsed['date']}{parsed['time']}_"
                f"{parsed['sequence']}"
            )
            channel = parsed["channel"]
        else:
            key = ("FILE", index)
            pin_name = str(image["name"])
            channel = None
        group = groups.setdefault(
            key,
            {"name": pin_name, "images": []},
        )
        group["images"].append({**image, "channel": channel})
    return list(groups.values())


def _pin_image(images: list[dict[str, object]]) -> dict[str, object]:
    for channel in ("Z", "W", "T"):
        for image in images:
            if image["channel"] == channel:
                return image
    return images[0]


def _description_html(group: dict[str, object]) -> str:
    images = group["images"]
    parts = [
        '<div style="min-width:600px;max-width:890px">',
        f"<h3>{escape(str(group['name']))}</h3>",
        '<table style="border-collapse:collapse;width:100%">',
    ]

    def image_cell(
        image: dict[str, object],
        max_width: int,
        max_height: int,
        colspan: int = 1,
    ) -> str:
        label = image["channel"] or "Fotografía"
        internal_uri = "images/" + quote(str(image["name"]), safe="")
        return (
            f'<td colspan="{colspan}" style="text-align:center;'
            'vertical-align:top;padding:4px">'
            f'<b>{escape(str(label))}</b><br/>'
            f'<img src="{internal_uri}" style="max-width:{max_width}px;'
            f'max-height:{max_height}px;height:auto;width:auto"/></td>'
        )

    by_channel = {image["channel"]: image for image in images}
    primary = by_channel.get("Z")
    secondary = [
        by_channel[channel]
        for channel in ("W", "T")
        if channel in by_channel
    ]

    if primary is not None:
        parts.append(f"<tr>{image_cell(primary, 850, 480, 2)}</tr>")
        if len(secondary) == 2:
            parts.append(
                "<tr>"
                + image_cell(secondary[0], 410, 230)
                + image_cell(secondary[1], 410, 230)
                + "</tr>"
            )
        elif secondary:
            parts.append(f"<tr>{image_cell(secondary[0], 500, 280, 2)}</tr>")
    elif len(secondary) == 2:
        parts.append(
            "<tr>"
            + image_cell(secondary[0], 425, 240)
            + image_cell(secondary[1], 425, 240)
            + "</tr>"
        )
    else:
        available = secondary or list(images)
        if available:
            parts.append(f"<tr>{image_cell(available[0], 850, 480, 2)}</tr>")

    parts.extend(("</table>", "</div>"))
    return "".join(parts)


def _build_kml(images: list[dict[str, object]]) -> bytes:
    ElementTree.register_namespace("", KML_NAMESPACE)
    kml = ElementTree.Element(f"{{{KML_NAMESPACE}}}kml")
    document = ElementTree.SubElement(kml, f"{{{KML_NAMESPACE}}}Document")
    ElementTree.SubElement(document, f"{{{KML_NAMESPACE}}}name").text = (
        "Selección geográfica MAAT One"
    )
    for group in _group_images(images):
        group_images = group["images"]
        pin_image = _pin_image(group_images)
        placemark = ElementTree.SubElement(
            document, f"{{{KML_NAMESPACE}}}Placemark"
        )
        ElementTree.SubElement(
            placemark, f"{{{KML_NAMESPACE}}}name"
        ).text = str(group["name"])
        description = ElementTree.SubElement(
            placemark, f"{{{KML_NAMESPACE}}}description"
        )
        description.text = _description_html(group)
        point = ElementTree.SubElement(
            placemark, f"{{{KML_NAMESPACE}}}Point"
        )
        ElementTree.SubElement(
            point, f"{{{KML_NAMESPACE}}}coordinates"
        ).text = (
            f"{pin_image['longitude']},{pin_image['latitude']},0"
        )
    return ElementTree.tostring(kml, encoding="utf-8", xml_declaration=True)


def _write_kmz(
    kmz_path: Path,
    images: list[dict[str, object]],
) -> None:
    with ZipFile(kmz_path, "x", compression=ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", _build_kml(images))
        for image in images:
            archive.write(image["source"], f"images/{image['name']}")


def export_geographic_selection(
    root_folder: str | Path,
    requested_name: object,
    selected_images: list[dict[str, object]],
) -> dict[str, object]:
    """Copia una selección y publica su KMZ mediante un renombrado final."""
    try:
        root = Path(root_folder).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise GeographicExportError(
            "La carpeta raíz del proyecto no es válida."
        ) from error
    if not root.is_dir():
        raise GeographicExportError("La carpeta raíz del proyecto no es válida.")
    folder_name = validate_export_folder_name(requested_name)
    destination = root / folder_name
    if destination.exists():
        raise GeographicExportError(
            f"Ya existe una salida con el nombre: {folder_name}"
        )
    images = _validated_selection(root, selected_images)

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{folder_name}.maat-export-", dir=root)
    )
    try:
        copied: list[str] = []
        for image in images:
            destination_image = temporary / str(image["name"])
            if destination_image.exists():
                raise GeographicExportError(
                    f"Ya existe una fotografía de destino: {image['name']}"
                )
            shutil.copy2(image["source"], destination_image)
            copied.append(str(destination / str(image["name"])))

        temporary_kmz = temporary / f"{folder_name}.kmz"
        if temporary_kmz.exists():
            raise GeographicExportError("Ya existe el KMZ de destino.")
        _write_kmz(temporary_kmz, images)
        if destination.exists():
            raise GeographicExportError(
                f"Ya existe una salida con el nombre: {folder_name}"
            )
        temporary.rename(destination)
    except Exception as error:
        shutil.rmtree(temporary, ignore_errors=True)
        if isinstance(error, GeographicExportError):
            raise
        raise GeographicExportError(
            "No fue posible completar la exportación geográfica."
        ) from error

    return {
        "destination_folder": str(destination),
        "kmz_path": str(destination / f"{folder_name}.kmz"),
        "image_count": len(images),
        "copied_images": copied,
    }
