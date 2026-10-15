"""Correlación de registros Excel con imágenes Zoom de referencia."""

from datetime import date, datetime, timedelta
import re

from src.dji_parser import parse_dji_image


TIME_PATTERN = re.compile(r"\A\d{6}\Z")
DATE_PATTERN = re.compile(
    r"\A(?:(?P<day>\d{1,2})[/-](?P<month>\d{1,2})[/-](?P<year>\d{2}|\d{4})|"
    r"(?P<iso_year>\d{4})[/-](?P<iso_month>\d{1,2})[/-](?P<iso_day>\d{1,2}))\Z"
)


def normalize_date(value: object) -> str | None:
    """Normaliza las fechas admitidas a AAAAMMDD."""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            excel_date = datetime(1899, 12, 30) + timedelta(days=value)
        except (OverflowError, TypeError, ValueError):
            return None
        return excel_date.strftime("%Y%m%d")

    match = DATE_PATTERN.fullmatch(str(value).strip())
    if match is None:
        return None
    if match.group("iso_year"):
        year = int(match.group("iso_year"))
        month = int(match.group("iso_month"))
        day = int(match.group("iso_day"))
    else:
        year_text = match.group("year")
        year = int(year_text)
        if len(year_text) == 2:
            year += 2000 if year < 50 else 1900
        month = int(match.group("month"))
        day = int(match.group("day"))
    try:
        return date(year, month, day).strftime("%Y%m%d")
    except ValueError:
        return None


def extract_target_time(file_reference: object) -> tuple[str, int] | None:
    """Interpreta G como HHMMSS; recupera ceros iniciales perdidos por Excel."""
    if isinstance(file_reference, bool):
        return None
    if isinstance(file_reference, float):
        if not file_reference.is_integer():
            return None
        file_reference = int(file_reference)
    reference = str(file_reference).strip()
    if not re.fullmatch(r"[0-9]{1,6}", reference):
        return None
    time_text = reference.zfill(6)
    try:
        parsed_time = datetime.strptime(time_text, "%H%M%S")
    except ValueError:
        return None
    seconds = parsed_time.hour * 3600 + parsed_time.minute * 60 + parsed_time.second
    return time_text, seconds


def match_zoom_images(
    records: list[dict[str, object]],
    parsed_images: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Busca para cada registro una única Zoom entre cero y cinco segundos."""
    zoom_images = [
        image
        for image in parsed_images
        if image.get("recognized") and image.get("channel") == "Z"
    ]
    results: list[dict[str, object]] = []

    for record in records:
        file_reference = record.get("file_reference")
        normalized_date = normalize_date(record.get("date"))
        target = extract_target_time(file_reference)
        base_result = {
            "row_number": record.get("row_number"),
            "file_reference": file_reference,
            "normalized_date": normalized_date,
            "target_time": target[0] if target else None,
            "validation": "NONE",
            "approved": None,
        }
        if target is None or normalized_date is None:
            results.append({**base_result, "status": "INVALID_REFERENCE"})
            continue

        candidates = [
            image
            for image in zoom_images
            if image["date"] == normalized_date
            and abs(image["time_seconds"] - target[1]) <= 5
        ]
        if not candidates:
            results.append({**base_result, "status": "ZOOM_NOT_FOUND"})
            continue

        minimum_difference = min(
            abs(image["time_seconds"] - target[1]) for image in candidates
        )
        closest = [
            image
            for image in candidates
            if abs(image["time_seconds"] - target[1]) == minimum_difference
        ]
        if len(closest) > 1:
            results.append(
                {
                    **base_result,
                    "status": "ZOOM_AMBIGUOUS",
                    "candidates": closest,
                }
            )
            continue
        zoom = closest[0]
        signed_difference = zoom["time_seconds"] - target[1]
        if signed_difference == 0:
            results.append(
                {
                    **base_result,
                    "status": "ZOOM_EXACT",
                    "zoom": zoom,
                    "zoom_time": zoom["time"],
                    "time_difference": 0,
                    "validation": "AUTO",
                    "approved": True,
                }
            )
            continue
        results.append(
            {
                **base_result,
                "status": "ZOOM_PENDING_REVIEW",
                "zoom": zoom,
                "zoom_time": zoom["time"],
                "time_difference": signed_difference,
                "validation": "REVIEW_REQUIRED",
            }
        )

    return results


def decide_zoom(result: dict[str, object], approved: bool) -> None:
    """Registra o cambia en memoria la decisión sobre una Zoom revisable."""
    reviewable_statuses = {
        "ZOOM_EXACT",
        "ZOOM_PENDING_REVIEW",
        "ZOOM_ACCEPTED",
        "ZOOM_REJECTED",
    }
    if result.get("status") not in reviewable_statuses:
        raise ValueError("La correlación no admite una decisión manual.")
    result["status"] = "ZOOM_ACCEPTED" if approved else "ZOOM_REJECTED"
    result["validation"] = "MANUAL"
    result["approved"] = approved
