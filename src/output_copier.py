"""Copia física de las imágenes incluidas en un plan de salida."""

from pathlib import Path
from shutil import copy2


def copy_planned_images(plan: dict[str, object]) -> dict[str, object]:
    """Copia los archivos del plan sin sobrescribir destinos existentes."""
    destination_folder = Path(plan["destination_folder"])
    copied: list[str] = []
    skipped_existing: list[str] = []
    planned_missing: list[dict[str, object]] = []
    source_missing: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for planned_set in plan["sets"]:
        paths = planned_set["absolute_paths"]
        filenames = planned_set["filenames"]
        for channel in ("zoom", "wide", "thermal"):
            source_value = paths[channel]
            filename = filenames[channel]
            if source_value is None or filename is None:
                planned_missing.append(
                    {
                        "set_id": planned_set["set_id"],
                        "channel": channel,
                    }
                )
                continue

            source = Path(source_value)
            destination = destination_folder / filename
            if destination.exists():
                skipped_existing.append(str(destination))
                continue
            if not source.is_file():
                source_missing.append(
                    {
                        "source": str(source),
                        "destination": str(destination),
                        "channel": channel,
                    }
                )
                continue

            try:
                copy2(source, destination)
            except OSError as error:
                errors.append(
                    {
                        "source": str(source),
                        "destination": str(destination),
                        "error": str(error),
                    }
                )
            else:
                copied.append(str(destination))

    return {
        "sets_processed": len(plan["sets"]),
        "images_copied": len(copied),
        "images_existing": len(skipped_existing),
        "planned_missing": len(planned_missing),
        "source_missing": len(source_missing),
        "copy_errors": len(errors),
        "copied": copied,
        "skipped_existing": skipped_existing,
        "planned_missing_details": planned_missing,
        "source_missing_details": source_missing,
        "errors": errors,
    }
