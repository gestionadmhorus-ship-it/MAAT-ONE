"""Búsqueda geográfica de fotografías inventariadas por MAAT One."""

from math import isfinite
import re
from typing import Iterable

from src.gps_metadata import GPSMetadataCache, horizontal_distance


class GeographicSearchError(ValueError):
    """Error de validación o de referencia en una búsqueda geográfica."""


COORDINATE_TEXT_PATTERN = re.compile(r"\A[+-]?\d+\.\d{5,}\Z")


def _inventory_image(image: dict[str, str]) -> dict[str, str]:
    """Devuelve únicamente la identidad estable de una imagen inventariada."""
    try:
        name = str(image["name"])
        path = str(image["path"])
    except KeyError as error:
        raise GeographicSearchError(
            "Cada fotografía debe contener nombre y ruta."
        ) from error
    return {"name": name, "path": path}


def find_images_by_name(
    inventory: Iterable[dict[str, str]],
    query: str,
) -> dict[str, object]:
    """Busca por nombre exacto o parcial sin elegir entre coincidencias."""
    normalized_query = str(query).strip().casefold()
    if not normalized_query:
        raise GeographicSearchError("El nombre de búsqueda no puede estar vacío.")

    images = [_inventory_image(image) for image in inventory]
    exact_matches = [
        image for image in images if image["name"].casefold() == normalized_query
    ]
    if exact_matches:
        matches = exact_matches
        match_type = "exact"
    else:
        matches = [
            image for image in images if normalized_query in image["name"].casefold()
        ]
        match_type = "partial" if matches else "none"

    return {
        "query": str(query),
        "match_type": match_type,
        "match_count": len(matches),
        "multiple_matches": len(matches) > 1,
        "matches": matches,
    }


def validate_search_parameters(
    latitude: object,
    longitude: object,
    radius_m: object,
) -> tuple[float, float, float]:
    """Valida y normaliza coordenadas decimales y radio en metros."""
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
        radius_value = float(radius_m)
    except (TypeError, ValueError) as error:
        raise GeographicSearchError(
            "Latitud, longitud y radio deben ser valores numéricos."
        ) from error

    if not isfinite(latitude_value) or not -90.0 <= latitude_value <= 90.0:
        raise GeographicSearchError("La latitud debe estar entre -90 y 90.")
    if not isfinite(longitude_value) or not -180.0 <= longitude_value <= 180.0:
        raise GeographicSearchError("La longitud debe estar entre -180 y 180.")
    if not isfinite(radius_value) or radius_value <= 0.0:
        raise GeographicSearchError("El radio debe ser mayor que 0 metros.")
    return latitude_value, longitude_value, radius_value


def validate_coordinate_input(
    latitude_text: object,
    longitude_text: object,
) -> tuple[float, float]:
    """Valida coordenadas escritas con un mínimo de cinco decimales."""
    latitude_value = str(latitude_text).strip()
    longitude_value = str(longitude_text).strip()
    if COORDINATE_TEXT_PATTERN.fullmatch(latitude_value) is None:
        raise GeographicSearchError(
            "La latitud debe contener al menos 5 decimales."
        )
    if COORDINATE_TEXT_PATTERN.fullmatch(longitude_value) is None:
        raise GeographicSearchError(
            "La longitud debe contener al menos 5 decimales."
        )
    latitude, longitude, _radius = validate_search_parameters(
        latitude_value,
        longitude_value,
        1.0,
    )
    return latitude, longitude


def get_image_coordinates(
    image: dict[str, str],
    gps_cache: GPSMetadataCache,
) -> dict[str, object] | None:
    """Obtiene las coordenadas válidas de una fotografía inventariada."""
    inventoried_image = _inventory_image(image)
    coordinates = gps_cache.coordinates(inventoried_image["path"])
    if coordinates is None:
        return None
    try:
        latitude, longitude, _radius = validate_search_parameters(
            coordinates["latitude"], coordinates["longitude"], 1.0
        )
    except (KeyError, GeographicSearchError):
        return None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "source": coordinates.get("source"),
    }


def find_nearest_gps_image(
    inventory: Iterable[dict[str, str]],
    latitude: object,
    longitude: object,
    gps_cache: GPSMetadataCache | None = None,
) -> dict[str, object]:
    """Devuelve la fotografía GPS más próxima a unas coordenadas."""
    latitude_value, longitude_value, _radius = validate_search_parameters(
        latitude,
        longitude,
        1.0,
    )
    reference = {
        "latitude": latitude_value,
        "longitude": longitude_value,
    }
    cache = gps_cache if gps_cache is not None else GPSMetadataCache()
    nearest: dict[str, object] | None = None
    for raw_image in inventory:
        image = _inventory_image(raw_image)
        coordinates = get_image_coordinates(image, cache)
        if coordinates is None:
            continue
        distance_m = horizontal_distance(reference, coordinates)
        if nearest is None or distance_m < nearest["distance_m"]:
            nearest = {
                "name": image["name"],
                "path": image["path"],
                "latitude": coordinates["latitude"],
                "longitude": coordinates["longitude"],
                "source": coordinates.get("source"),
                "distance_m": distance_m,
            }
    if nearest is None:
        raise GeographicSearchError(
            "La carpeta seleccionada no contiene fotografías con GPS válido."
        )
    return nearest


def search_by_coordinates(
    inventory: Iterable[dict[str, str]],
    latitude: object,
    longitude: object,
    radius_m: object,
    gps_cache: GPSMetadataCache | None = None,
) -> dict[str, object]:
    """Busca fotografías dentro de un radio desde unas coordenadas."""
    latitude_value, longitude_value, radius_value = validate_search_parameters(
        latitude, longitude, radius_m
    )
    cache = gps_cache if gps_cache is not None else GPSMetadataCache()
    reference_coordinates = {
        "latitude": latitude_value,
        "longitude": longitude_value,
    }
    results: list[dict[str, object]] = []
    without_gps: list[dict[str, str]] = []

    for raw_image in inventory:
        image = _inventory_image(raw_image)
        coordinates = get_image_coordinates(image, cache)
        if coordinates is None:
            without_gps.append(image)
            continue
        distance_m = horizontal_distance(reference_coordinates, coordinates)
        if distance_m <= radius_value:
            results.append(
                {
                    "name": image["name"],
                    "path": image["path"],
                    "latitude": coordinates["latitude"],
                    "longitude": coordinates["longitude"],
                    "distance_m": distance_m,
                }
            )

    results.sort(key=lambda result: result["distance_m"])
    return {
        "reference": {
            "latitude": latitude_value,
            "longitude": longitude_value,
        },
        "radius_m": radius_value,
        "results": results,
        "without_gps": without_gps,
    }


def search_by_reference_image(
    inventory: Iterable[dict[str, str]],
    reference_image: dict[str, str],
    radius_m: object,
    gps_cache: GPSMetadataCache | None = None,
) -> dict[str, object]:
    """Busca desde una fotografía de referencia ya elegida por el llamador."""
    cache = gps_cache if gps_cache is not None else GPSMetadataCache()
    reference = _inventory_image(reference_image)
    coordinates = get_image_coordinates(reference, cache)
    if coordinates is None:
        raise GeographicSearchError(
            "La fotografía de referencia no contiene coordenadas GPS válidas."
        )

    search = search_by_coordinates(
        inventory,
        coordinates["latitude"],
        coordinates["longitude"],
        radius_m,
        cache,
    )
    search["reference"] = {
        "name": reference["name"],
        "path": reference["path"],
        "latitude": coordinates["latitude"],
        "longitude": coordinates["longitude"],
    }
    return search
