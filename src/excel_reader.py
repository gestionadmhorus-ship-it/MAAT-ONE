"""Lectura e interpretación básica del Excel de MAAT One."""

from pathlib import Path
import unicodedata
import re

from python_calamine import CalamineWorkbook
from src.project_files import input_files


HEADER_TERMS = {
    "criticidad",
    "elemento",
    "nombre de archivo",
    "archivo",
    "anomalia",
    "recomendacion",
    "estructura",
    "fecha",
    "hora",
}
STAGE_TERMS = {"fecha:", "linea:", "etapa:"}


def normalize(value: object) -> str:
    """Normaliza una celda para comparar encabezados."""
    text = unicodedata.normalize("NFD", str(value).strip().casefold())
    return "".join(character for character in text if not unicodedata.combining(character))


def _is_header(row: list[object]) -> bool:
    matches = sum(
        any(term in normalize(cell) for term in HEADER_TERMS)
        for cell in row
        if normalize(cell)
    )
    return matches >= 3


def _is_stage_row(row: list[object]) -> bool:
    values = [normalize(cell) for cell in row if normalize(cell)]
    return len(values) <= 3 and any(
        term in value for value in values for term in STAGE_TERMS
    )


def interpret_rows(rows: list[list[object]]) -> list[dict[str, object]]:
    """Extrae los registros mínimos de una matriz de celdas."""
    records: list[dict[str, object]] = []
    active_header: list[str] | None = None
    metadata = {"line": "", "stage": ""}

    for row_number, row in enumerate(rows, start=1):
        # La cabecera puede estar distribuida en varias celdas o bloques.
        for index, cell in enumerate(row):
            text = str(cell or "").strip()
            for label, key in ((r"L[ií]nea", "line"), ("Etapa", "stage")):
                match = re.search(label + r"\s*:\s*([^|]*?)(?=\s+(?:Etapa|Origen|Fecha)\s*:|$|\|)", text, re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    if not value and index + 1 < len(row): value = str(row[index + 1] or "").strip()
                    metadata[key] = value
        if _is_header(row):
            active_header = [normalize(cell) for cell in row]
            continue
        if active_header is None or _is_stage_row(row):
            continue

        original_data = {
            header: row[index] if index < len(row) else ""
            for index, header in enumerate(active_header)
            if header
        }
        # A y G son las referencias, independientemente de los encabezados.
        if not any(value is not None and str(value).strip() for value in row):
            continue
        first = normalize(row[0]) if row else ""
        if first.startswith(("fecha finalizada", "hora finalizada", "fecha:", "linea:", "etapa:")):
            continue
        file_reference = row[6] if len(row) > 6 else None
        records.append(
            {
                "row_number": row_number,
                "file_reference": file_reference,
                "date": row[0] if row else None,
                "original_data": original_data,
                "project_metadata": dict(metadata),
                "export_data": {
                    "element": row[2] if len(row) > 2 else "",
                    "anomaly": row[3] if len(row) > 3 else "",
                    "criticality": row[5] if len(row) > 5 else "",
                },
            }
        )

    return records


def find_project_excels(root_folder: Path) -> list[Path]:
    """Encuentra todos los Excel dentro de la raíz, sin restricciones de nombre."""
    return sorted(
        (entry for entry in input_files(root_folder)
         if entry.is_file() and not entry.name.startswith("~$") and entry.suffix.casefold() in {".xlsx", ".xls"}),
        key=lambda entry: str(entry).casefold(),
    )


def read_project_excel(root_folder: Path) -> list[dict[str, object]]:
    """Lee la primera hoja del único Excel contenido en el proyecto."""
    excel_files = find_project_excels(root_folder)
    if len(excel_files) != 1:
        raise ValueError("La carpeta seleccionada debe contener un único Excel.")

    workbook = CalamineWorkbook.from_path(excel_files[0])
    if not workbook.sheet_names:
        raise ValueError("El Excel no contiene hojas.")

    rows = workbook.get_sheet_by_name(workbook.sheet_names[0]).to_python(
        skip_empty_area=False
    )
    if not rows:
        raise ValueError("La primera hoja no contiene filas.")
    records = interpret_rows(rows)
    if not records:
        raise ValueError("No se encontraron registros debajo de los encabezados del Excel.")
    return records
