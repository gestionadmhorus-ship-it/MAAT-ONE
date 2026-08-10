"""Formación lógica de SET W/Z/T en memoria."""


def _find_channel(
    zoom: dict[str, object],
    parsed_images: list[dict[str, object]],
    channel: str,
) -> tuple[dict[str, object] | None, int | None, list[dict[str, object]]]:
    candidates = [
        image
        for image in parsed_images
        if image.get("recognized")
        and image.get("channel") == channel
        and image.get("date") == zoom["date"]
        and image.get("sequence") == zoom["sequence"]
        and abs(image["time_seconds"] - zoom["time_seconds"]) <= 3
    ]
    if not candidates:
        return None, None, []

    minimum_difference = min(
        abs(image["time_seconds"] - zoom["time_seconds"])
        for image in candidates
    )
    closest = [
        image
        for image in candidates
        if abs(image["time_seconds"] - zoom["time_seconds"])
        == minimum_difference
    ]
    if len(closest) > 1:
        return None, None, closest

    selected = closest[0]
    signed_difference = selected["time_seconds"] - zoom["time_seconds"]
    return selected, signed_difference, []


def build_sets(
    zoom_results: list[dict[str, object]],
    parsed_images: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Construye un SET por cada Zoom exacta o aceptada manualmente."""
    sets: list[dict[str, object]] = []
    for zoom_result in zoom_results:
        if zoom_result.get("status") not in {"ZOOM_EXACT", "ZOOM_ACCEPTED"}:
            continue

        zoom = zoom_result["zoom"]
        wide, wide_difference, ambiguous_wide = _find_channel(
            zoom, parsed_images, "W"
        )
        thermal, thermal_difference, ambiguous_thermal = _find_channel(
            zoom, parsed_images, "T"
        )
        missing_channels = [
            channel
            for channel, image in (("W", wide), ("T", thermal))
            if image is None
        ]
        ambiguous_channels = {}
        if ambiguous_wide:
            ambiguous_channels["W"] = ambiguous_wide
        if ambiguous_thermal:
            ambiguous_channels["T"] = ambiguous_thermal

        sets.append(
            {
                "row_number": zoom_result["row_number"],
                "file_reference": zoom_result["file_reference"],
                "zoom": zoom,
                "wide": wide,
                "thermal": thermal,
                "sequence": zoom["sequence"],
                "status": (
                    "SET_COMPLETE" if not missing_channels else "SET_INCOMPLETE"
                ),
                "missing_channels": missing_channels,
                "ambiguous_channels": ambiguous_channels,
                "channel_differences": {
                    "Z": 0,
                    "W": wide_difference,
                    "T": thermal_difference,
                },
            }
        )

    return sets
