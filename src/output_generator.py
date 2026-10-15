"""Generación final controlada de imágenes y trazabilidad de MAAT One."""

from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from src.excel_reader import find_project_excels


class OutputGenerationError(Exception):
    """Error controlado durante el precheck o la generación final."""


def _identity(item: dict[str, object]) -> tuple[object, str]:
    return item.get("row_number"), str(item.get("file_reference"))


def _find_project_inputs(root_folder: Path) -> tuple[Path, Path]:
    excels = find_project_excels(root_folder)
    if len(excels) != 1:
        raise OutputGenerationError("El proyecto debe contener un único Excel.")
    return root_folder, excels[0]


def _select_source_images(
    root_folder: Path,
    set_results: list[dict[str, object]],
) -> list[dict[str, object]]:
    materials, _excel = _find_project_inputs(root_folder)
    materials_path = materials.resolve()
    selected: dict[str, dict[str, object]] = {}

    for set_result in set_results:
        for channel in ("zoom", "wide", "thermal"):
            image = set_result.get(channel)
            if image is None:
                continue
            source = Path(image["path"])
            if source.name != image["name"]:
                raise OutputGenerationError(
                    f"El nombre del archivo fuente no coincide: {source.name}"
                )
            try:
                resolved_source = source.resolve(strict=True)
            except OSError as error:
                raise OutputGenerationError(
                    f"Archivo fuente desaparecido: {source.name}"
                ) from error
            if not resolved_source.is_file() or not resolved_source.is_relative_to(
                materials_path
            ):
                raise OutputGenerationError(
                    f"Archivo fuente no válido: {source.name}"
                )
            try:
                with resolved_source.open("rb") as source_file:
                    source_file.read(1)
            except OSError as error:
                raise OutputGenerationError(
                    f"No se puede leer el archivo fuente: {source.name}"
                ) from error

            destination_key = source.name.casefold()
            existing = selected.get(destination_key)
            if existing and existing["resolved_path"] != resolved_source:
                raise OutputGenerationError(
                    f"Conflicto de nombre de destino: {source.name}"
                )
            if existing is None:
                selected[destination_key] = {
                    "name": source.name,
                    "resolved_path": resolved_source,
                }

    return list(selected.values())


def _serialize_channel(
    image: dict[str, object] | None,
    difference: int | None,
) -> dict[str, object] | None:
    if image is None:
        return None
    return {
        "filename": image["name"],
        "difference_seconds": difference,
    }


def _serialize_set(set_result: dict[str, object]) -> dict[str, object]:
    differences = set_result["channel_differences"]
    return {
        "set_id": set_result["set_id"],
        "status": set_result["status"],
        "sequence": set_result["sequence"],
        "wide": _serialize_channel(set_result["wide"], differences["W"]),
        "zoom": _serialize_channel(set_result["zoom"], differences["Z"]),
        "thermal": _serialize_channel(
            set_result["thermal"], differences["T"]
        ),
        "missing_channels": list(set_result["missing_channels"]),
        "ambiguous_channels": {
            channel: [candidate["name"] for candidate in candidates]
            for channel, candidates in set_result["ambiguous_channels"].items()
        },
        "channel_differences": dict(differences),
    }


def _serialize_zoom(zoom_result: dict[str, object]) -> dict[str, object]:
    zoom = zoom_result.get("zoom")
    candidates = zoom_result.get("candidates", [])
    return {
        "status": zoom_result["status"],
        "validation": zoom_result.get("validation"),
        "approved": zoom_result.get("approved"),
        "filename": zoom["name"] if zoom else None,
        "zoom_time": zoom_result.get("zoom_time"),
        "time_difference": zoom_result.get("time_difference"),
        "candidates": [candidate["name"] for candidate in candidates],
    }


def _build_traceability(
    root_folder: Path,
    excel_file: Path,
    records: list[dict[str, object]],
    zoom_results: list[dict[str, object]],
    set_results: list[dict[str, object]],
    inventory_count: int,
    recognized_count: int,
    unrecognized_count: int,
    copied_count: int,
) -> dict[str, object]:
    zoom_by_identity = {_identity(result): result for result in zoom_results}
    sets_by_identity = {_identity(result): result for result in set_results}
    if len(zoom_results) != len(records):
        raise OutputGenerationError(
            "La correlación Zoom no contiene todos los registros Excel."
        )

    trace_records = []
    for record in records:
        identity = _identity(record)
        zoom_result = zoom_by_identity.get(identity)
        if zoom_result is None:
            raise OutputGenerationError(
                f"Falta trazabilidad Zoom para la fila {record.get('row_number')}."
            )
        set_result = sets_by_identity.get(identity)
        trace_records.append(
            {
                "row_number": record.get("row_number"),
                "file_reference": record.get("file_reference"),
                "normalized_date": zoom_result.get("normalized_date"),
                "target_time": zoom_result.get("target_time"),
                "zoom": _serialize_zoom(zoom_result),
                "set": _serialize_set(set_result) if set_result else None,
            }
        )

    statuses = [result["status"] for result in zoom_results]
    return {
        "app": "MAAT One",
        "schema_version": "1.0",
        "project": {
            "root_name": root_folder.name,
            "excel_filename": excel_file.name,
            "generated_at": datetime.now().astimezone().isoformat(),
        },
        "summary": {
            "excel_records_total": len(records),
            "jpg_images_inventoried": inventory_count,
            "dji_names_recognized": recognized_count,
            "dji_names_unrecognized": unrecognized_count,
            "zoom_exact": statuses.count("ZOOM_EXACT"),
            "zoom_accepted_manual": statuses.count("ZOOM_ACCEPTED"),
            "zoom_rejected": statuses.count("ZOOM_REJECTED"),
            "zoom_not_found": statuses.count("ZOOM_NOT_FOUND"),
            "zoom_ambiguous": statuses.count("ZOOM_AMBIGUOUS"),
            "invalid_references": statuses.count("INVALID_REFERENCE"),
            "sets_complete": sum(
                result["status"] == "SET_COMPLETE" for result in set_results
            ),
            "sets_incomplete": sum(
                result["status"] == "SET_INCOMPLETE" for result in set_results
            ),
            "unique_images_copied": copied_count,
        },
        "records": trace_records,
    }


def generate_output(
    root_folder: Path,
    records: list[dict[str, object]],
    zoom_results: list[dict[str, object]],
    set_results: list[dict[str, object]],
    inventory_count: int,
    recognized_count: int,
    unrecognized_count: int,
) -> dict[str, object]:
    """Ejecuta precheck, copia segura y escritura de trazabilidad."""
    if any(
        result.get("status") == "ZOOM_PENDING_REVIEW"
        for result in zoom_results
    ):
        raise OutputGenerationError(
            "Existen revisiones de Zoom pendientes."
        )

    root_folder = root_folder.resolve()
    _materials, excel_file = _find_project_inputs(root_folder)
    output_name = f"Anomalias one {root_folder.name}"
    trace_name = f"Trazabilidad MAAT One {root_folder.name}.json"
    output_folder = root_folder / output_name
    trace_file = root_folder / trace_name
    if output_folder.exists() or trace_file.exists():
        raise OutputGenerationError(
            "Ya existe una salida de MAAT One para este proyecto.\n"
            "Retire o renombre la salida anterior antes de generar una nueva."
        )

    selected_images = _select_source_images(root_folder, set_results)
    traceability = _build_traceability(
        root_folder,
        excel_file,
        records,
        zoom_results,
        set_results,
        inventory_count,
        recognized_count,
        unrecognized_count,
        len(selected_images),
    )

    temporary_folder: Path | None = None
    temporary_trace = root_folder / f".maat-one-trace-{uuid4().hex}.tmp"
    final_folder_created = False
    try:
        temporary_folder = Path(
            tempfile.mkdtemp(prefix=".maat-one-output-", dir=root_folder)
        )
        for image in selected_images:
            shutil.copy2(
                image["resolved_path"],
                temporary_folder / image["name"],
            )
        with temporary_trace.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(traceability, file, ensure_ascii=False, indent=2)
            file.write("\n")

        temporary_folder.replace(output_folder)
        final_folder_created = True
        temporary_folder = None
        temporary_trace.replace(trace_file)
    except (OSError, TypeError, ValueError) as error:
        if temporary_folder is not None and temporary_folder.exists():
            shutil.rmtree(temporary_folder)
        if temporary_trace.exists():
            temporary_trace.unlink()
        if final_folder_created and output_folder.exists():
            shutil.rmtree(output_folder)
        raise OutputGenerationError(
            "No se pudo generar la salida final de MAAT One."
        ) from error

    return {
        "images_copied": len(selected_images),
        "sets_complete": traceability["summary"]["sets_complete"],
        "sets_incomplete": traceability["summary"]["sets_incomplete"],
        "output_name": output_name,
        "trace_name": trace_name,
    }
