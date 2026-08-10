"""Interpretación estricta de nombres de imágenes DJI."""

from datetime import datetime
import re


DJI_NAME_PATTERN = re.compile(
    r"\ADJI_(?P<date>\d{8})(?P<time>\d{6})_"
    r"(?P<sequence>\d{4})_(?P<channel>[WZT])\.JPG\Z",
    re.IGNORECASE,
)


def parse_dji_image(image: dict[str, str]) -> dict[str, object]:
    """Clasifica una imagen y extrae los datos de un nombre DJI válido."""
    name = image["name"]
    path = image["path"]
    match = DJI_NAME_PATTERN.fullmatch(name)
    if match is None:
        return {"name": name, "path": path, "recognized": False}

    date_text = match.group("date")
    time_text = match.group("time")
    try:
        datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")
    except ValueError:
        return {"name": name, "path": path, "recognized": False}

    hours = int(time_text[0:2])
    minutes = int(time_text[2:4])
    seconds = int(time_text[4:6])
    return {
        "name": name,
        "path": path,
        "recognized": True,
        "date": date_text,
        "time": time_text,
        "time_seconds": hours * 3600 + minutes * 60 + seconds,
        "sequence": match.group("sequence"),
        "channel": match.group("channel").upper(),
    }


def parse_dji_images(
    images: list[dict[str, str]],
) -> list[dict[str, object]]:
    """Interpreta todos los nombres de un inventario sin agruparlos."""
    return [parse_dji_image(image) for image in images]
