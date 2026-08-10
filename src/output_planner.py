"""Planificación en memoria de la salida final de MAAT One."""

from pathlib import Path


def _identity(item: dict[str, object]) -> tuple[object, str]:
    return item.get("row_number"), str(item.get("file_reference"))


def _channel_data(
    image: dict[str, object] | None,
) -> tuple[str | None, str | None]:
    if image is None:
        return None, None
    return str(Path(image["path"]).resolve()), str(image["name"])


def _trace_channel(
    image: dict[str, object] | None,
    difference_seconds: int | None,
) -> dict[str, object]:
    return {
        "filename": image.get("name") if image is not None else None,
        "difference_seconds": difference_seconds,
    }


def _trace_set(set_result: dict[str, object]) -> dict[str, object]:
    differences = dict(set_result["channel_differences"])
    ambiguous_channels = {
        channel: [candidate["name"] for candidate in candidates]
        for channel, candidates in set_result["ambiguous_channels"].items()
    }
    return {
        "set_id": set_result["set_id"],
        "status": set_result["status"],
        "sequence": set_result["sequence"],
        "wide": _trace_channel(set_result["wide"], differences["W"]),
        "zoom": _trace_channel(set_result["zoom"], differences["Z"]),
        "thermal": _trace_channel(
            set_result["thermal"], differences["T"]
        ),
        "missing_channels": list(set_result["missing_channels"]),
        "ambiguous_channels": ambiguous_channels,
        "channel_differences": differences,
    }


def build_output_plan(
    root_folder: Path,
    zoom_results: list[dict[str, object]],
    set_results: list[dict[str, object]],
) -> dict[str, object]:
    """Construye el plan reutilizable sin escribir ni copiar archivos."""
    root_folder = root_folder.resolve()
    destination_name = f"Anomalias One {root_folder.name}"
    destination_folder = root_folder / destination_name
    zoom_by_identity = {_identity(result): result for result in zoom_results}
    set_by_identity = {_identity(result): result for result in set_results}
    planned_sets: list[dict[str, object]] = []
    unique_images: set[str] = set()
    missing_images = 0

    for set_result in set_results:
        zoom_result = zoom_by_identity[_identity(set_result)]
        zoom_path, zoom_name = _channel_data(set_result["zoom"])
        wide_path, wide_name = _channel_data(set_result["wide"])
        thermal_path, thermal_name = _channel_data(set_result["thermal"])
        for path in (zoom_path, wide_path, thermal_path):
            if path is not None:
                unique_images.add(path.casefold())
        missing_images += sum(
            path is None for path in (zoom_path, wide_path, thermal_path)
        )

        validation = zoom_result.get("validation")
        planned_sets.append(
            {
                "set_id": set_result["set_id"],
                "row_number": set_result["row_number"],
                "file_reference": set_result["file_reference"],
                "sequence": set_result["sequence"],
                "set_status": set_result["status"],
                "validation": validation,
                "manual_decision": (
                    zoom_result.get("approved")
                    if validation == "MANUAL"
                    else None
                ),
                "time_differences": {
                    "Z": zoom_result.get("time_difference"),
                    "W": set_result["channel_differences"]["W"],
                    "T": set_result["channel_differences"]["T"],
                },
                "absolute_paths": {
                    "zoom": zoom_path,
                    "wide": wide_path,
                    "thermal": thermal_path,
                },
                "filenames": {
                    "zoom": zoom_name,
                    "wide": wide_name,
                    "thermal": thermal_name,
                },
                "destination_folder": str(destination_folder),
            }
        )

    records: list[dict[str, object]] = []
    for zoom_result in zoom_results:
        zoom = zoom_result.get("zoom")
        original_suggested = zoom_result.get("original_suggested_zoom") or zoom
        finally_used = zoom_result.get("finally_used_zoom") or zoom
        candidates = zoom_result.get("candidates", [])
        matching_set = set_by_identity.get(_identity(zoom_result))
        records.append(
            {
                "row_number": zoom_result.get("row_number"),
                "file_reference": zoom_result.get("file_reference"),
                "normalized_date": zoom_result.get("normalized_date"),
                "target_time": zoom_result.get("target_time"),
                "status": zoom_result.get("status"),
                "validation": zoom_result.get("validation"),
                "approved": zoom_result.get("approved"),
                "filename": (
                    zoom.get("name") if zoom is not None else None
                ),
                "original_suggested_filename": (
                    original_suggested.get("name")
                    if original_suggested is not None
                    else None
                ),
                "final_used_filename": (
                    finally_used.get("name")
                    if finally_used is not None
                    else None
                ),
                "override_reason": zoom_result.get("override_reason"),
                "override_distance_m": zoom_result.get(
                    "override_distance_m"
                ),
                "zoom_time": zoom_result.get("zoom_time"),
                "time_difference": zoom_result.get("time_difference"),
                "candidates": [
                    candidate["name"] for candidate in candidates
                ],
                "set": (
                    _trace_set(matching_set)
                    if matching_set is not None
                    else None
                ),
            }
        )

    summary = {
        "complete_sets": sum(
            result["status"] == "SET_COMPLETE" for result in set_results
        ),
        "incomplete_sets": sum(
            result["status"] == "SET_INCOMPLETE" for result in set_results
        ),
        "discarded_sets": len(zoom_results) - len(set_results),
        "images_to_copy": len(unique_images),
        "missing_images": missing_images,
        "manual_decisions": sum(
            result.get("validation") == "MANUAL" for result in zoom_results
        ),
        "automatic_validations": sum(
            result.get("validation") == "AUTO" for result in zoom_results
        ),
    }
    reason = None
    if not planned_sets:
        reason = (
            "No existe ningún SET apto para copiar. "
            "Ninguna Zoom quedó exacta o aceptada manualmente."
        )

    return {
        "root_folder": str(root_folder),
        "destination_name": destination_name,
        "destination_folder": str(destination_folder),
        "sets": planned_sets,
        "records": records,
        "summary": summary,
        "reason": reason,
    }
