"""Lectura GPS mínima y distancia horizontal para imágenes DJI."""

from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from xml.etree import ElementTree

from PIL import ExifTags, Image


DJI_XMP_NAMESPACE = "http://www.dji.com/drone-dji/1.0/"
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
EARTH_MEAN_RADIUS_METERS = 6_371_008.8


def _valid_coordinates(
    latitude: object,
    longitude: object,
    source: str,
) -> dict[str, object] | None:
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        return None
    if not -90.0 <= latitude_value <= 90.0:
        return None
    if not -180.0 <= longitude_value <= 180.0:
        return None
    return {
        "latitude": latitude_value,
        "longitude": longitude_value,
        "source": source,
    }


def _read_xmp_packet(path: Path) -> bytes | None:
    try:
        with path.open("rb") as image_file:
            if image_file.read(2) != b"\xff\xd8":
                return None
            while True:
                prefix = image_file.read(1)
                if not prefix:
                    return None
                if prefix != b"\xff":
                    continue
                marker = image_file.read(1)
                while marker == b"\xff":
                    marker = image_file.read(1)
                if not marker or marker in {b"\xda", b"\xd9"}:
                    return None
                if marker in {b"\x01"} or b"\xd0" <= marker <= b"\xd7":
                    continue
                length_bytes = image_file.read(2)
                if len(length_bytes) != 2:
                    return None
                segment_length = int.from_bytes(length_bytes, "big")
                if segment_length < 2:
                    return None
                payload = image_file.read(segment_length - 2)
                if len(payload) != segment_length - 2:
                    return None
                if marker == b"\xe1" and payload.startswith(XMP_HEADER):
                    return payload[len(XMP_HEADER):]
    except OSError:
        return None


def _read_xmp_gps(path: Path) -> dict[str, object] | None:
    packet = _read_xmp_packet(path)
    if packet is None:
        return None
    try:
        root = ElementTree.fromstring(packet)
    except ElementTree.ParseError:
        return None
    latitude = longitude = None
    latitude_key = f"{{{DJI_XMP_NAMESPACE}}}GpsLatitude"
    longitude_key = f"{{{DJI_XMP_NAMESPACE}}}GpsLongitude"
    for element in root.iter():
        if latitude is None:
            latitude = element.attrib.get(latitude_key)
        if longitude is None:
            longitude = element.attrib.get(longitude_key)
        if latitude is not None and longitude is not None:
            break
    return _valid_coordinates(latitude, longitude, "XMP")


def _degrees(value: object) -> float:
    degrees, minutes, seconds = value
    return float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0


def _read_exif_gps(path: Path) -> dict[str, object] | None:
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        latitude = _degrees(gps[2])
        longitude = _degrees(gps[4])
        if str(gps[1]).upper() == "S":
            latitude = -latitude
        if str(gps[3]).upper() == "W":
            longitude = -longitude
    except (KeyError, OSError, TypeError, ValueError, ZeroDivisionError):
        return None
    return _valid_coordinates(latitude, longitude, "EXIF")


def read_gps_coordinates(path: str | Path) -> dict[str, object] | None:
    """Obtiene GPS DJI XMP y utiliza EXIF solo como alternativa."""
    image_path = Path(path)
    return _read_xmp_gps(image_path) or _read_exif_gps(image_path)


def horizontal_distance(
    first: dict[str, object],
    second: dict[str, object],
) -> float:
    """Calcula la distancia Haversine horizontal en metros."""
    latitude_1 = radians(first["latitude"])
    longitude_1 = radians(first["longitude"])
    latitude_2 = radians(second["latitude"])
    longitude_2 = radians(second["longitude"])
    latitude_delta = latitude_2 - latitude_1
    longitude_delta = longitude_2 - longitude_1
    haversine = (
        sin(latitude_delta / 2.0) ** 2
        + cos(latitude_1)
        * cos(latitude_2)
        * sin(longitude_delta / 2.0) ** 2
    )
    return 2.0 * EARTH_MEAN_RADIUS_METERS * asin(sqrt(haversine))


class GPSMetadataCache:
    """Cache de coordenadas que vive durante la sesión de la aplicación."""

    def __init__(self) -> None:
        self._coordinates: dict[str, dict[str, object] | None] = {}

    def coordinates(self, path: str | Path) -> dict[str, object] | None:
        cache_key = str(Path(path).resolve()).casefold()
        if cache_key not in self._coordinates:
            self._coordinates[cache_key] = read_gps_coordinates(path)
        return self._coordinates[cache_key]

    def distance_from_suggested(
        self,
        suggested_path: str | Path,
        candidate_path: str | Path,
    ) -> float | None:
        """Mide siempre desde la Zoom sugerida originalmente."""
        suggested = self.coordinates(suggested_path)
        candidate = self.coordinates(candidate_path)
        if suggested is None or candidate is None:
            return None
        return horizontal_distance(suggested, candidate)
