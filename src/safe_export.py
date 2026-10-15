"""Publicación de imágenes y trazabilidad como un único resultado completo."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shutil
import re
import tempfile
from uuid import uuid4

from src.excel_reader import find_project_excels, read_project_excel
from src.anomalies_excel import generate_anomalies_excel
from src.excel_traceability import generate_traceability_excel
from src.output_copier import copy_planned_images
from src.project_files import mark_output

UNIFIED = "Anomalias Unificadas"
SEPARATED = "Anomalias Separadas"

def folder_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value or '')).strip().rstrip('. ')[:100].rstrip('. ')
    if not name: name = "Sin anomalía registrada"
    if name.split('.')[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}:
        name = "_" + name
    return name

def export_plan(plan, mode=UNIFIED, progress=None):
    if mode not in (UNIFIED, SEPARATED): raise ValueError("Modo de salida desconocido.")
    progress = progress or (lambda *args: None)
    progress("Verificando archivos de entrada…")
    root = Path(plan["root_folder"]).resolve()
    if len(find_project_excels(root)) != 1:
        raise ValueError("Debe haber un único Excel de entrada.")
    if not plan["sets"]:
        raise ValueError("No hay SET aptos para exportar.")
    if any(item["status"] in {"INVALID_REFERENCE", "ZOOM_PENDING_REVIEW"} for item in plan["records"]):
        raise ValueError("Corrija las fechas/horas inválidas o complete las revisiones pendientes.")
    plan = deepcopy(plan)
    if any("source_record" not in item for item in plan["sets"]):
        source_rows = {(row["row_number"], str(row["file_reference"])): row for row in read_project_excel(root)}
        for item in plan["sets"]:
            if "source_record" not in item:
                item["source_record"] = source_rows.get((item["row_number"], str(item["file_reference"])))
    for item in plan["sets"]:
        for value in item["absolute_paths"].values():
            if value is not None:
                source = Path(value).resolve(strict=True)
                if not source.is_file() or not source.is_relative_to(root):
                    raise ValueError("Una imagen de origen no es válida o está fuera del proyecto.")
    image_directory = "Anomalias" if mode == UNIFIED else SEPARATED
    targets = [root / image_directory, root / "Anomalias.xlsx", root / f"Trazabilidad MAAT One {root.name}.xlsx"]
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise ValueError("Ya existen estos destinos: " + ", ".join(existing) + ". Mueva la salida anterior fuera de la raíz antes de generar nuevamente; no se sobrescribió nada.")
    published = []
    staging = Path(tempfile.mkdtemp(prefix=".maat-one-export-", dir=root))
    try:
        mark_output(staging)
        images = staging / ("Anomalias" if mode == UNIFIED else SEPARATED)
        images.mkdir()
        temporary_plan = deepcopy(plan)
        temporary_plan["destination_folder"] = str(images)
        temporary_plan["export_mode"] = mode
        temporary_plan["output_identifier"] = "MAAT_ONE_OUTPUT_V2|" + image_directory
        temporary_plan["export_folders"] = []
        if mode == UNIFIED:
            summary = copy_planned_images(temporary_plan, progress)
            for item in plan["sets"]:
                temporary_plan["export_folders"].append((item["set_id"], item.get("anomaly_name", ""), "Anomalias"))
        else:
            summary = {"sets_processed": 0, "images_copied": 0, "images_existing": 0, "source_missing": 0, "copy_errors": 0}
            names = [folder_name(item.get("anomaly_name")) for item in plan["sets"]]
            used = set()
            total = sum(value is not None for item in plan["sets"] for value in item["absolute_paths"].values())
            completed = 0
            for index, (item, name) in enumerate(zip(plan["sets"], names), 1):
                if sum(other.casefold() == name.casefold() for other in names) > 1:
                    name += " - " + folder_name(item["set_id"])
                base = name; suffix = 2
                while name.casefold() in used:
                    name = f"{base} ({suffix})"; suffix += 1
                used.add(name.casefold())
                target = images / name; target.mkdir()
                single = {**temporary_plan, "sets": [item], "destination_folder": str(target)}
                offset = completed
                def report(message, done, count):
                    progress(f"SET {index} de {len(names)} · {message}", offset + done, total)
                part = copy_planned_images(single, report)
                for key in summary: summary[key] += part[key]
                completed += sum(value is not None for value in item["absolute_paths"].values())
                temporary_plan["export_folders"].append((item["set_id"], item.get("anomaly_name", ""), str(Path(SEPARATED) / name)))
        if summary["source_missing"] or summary["copy_errors"]:
            raise ValueError("No se pudo completar la copia de todas las imágenes.")
        progress("Generando Excel de trazabilidad…")
        trace = generate_traceability_excel(temporary_plan, staging / f"Trazabilidad MAAT One {root.name}.xlsx")
        progress("Generando segundo Excel de anomalías…")
        generate_anomalies_excel(temporary_plan, staging / "Anomalias.xlsx")
        progress("Guardando el resultado completo…")
        for source, target in zip((images, staging / "Anomalias.xlsx", trace), targets):
            if target.exists(): raise FileExistsError(f"El destino ya existe: {target.name}")
            source.rename(target)
            published.append((source, target))
        return summary, root, root / trace.name
    except Exception:
        # Revertir solo los archivos publicados por este intento dentro de la raíz verificada.
        for source, target in reversed(published):
            if target.parent == root and target.exists() and source.parent == staging:
                target.rename(source)
        raise
    finally:
        # Solo se elimina la carpeta temporal creada por este intento y dentro de la raíz.
        if staging.exists() and staging.resolve().parent == root:
            shutil.rmtree(staging)
