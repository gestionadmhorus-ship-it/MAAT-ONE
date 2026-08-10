"""Lectura e interpretación básica del Excel de MAAT One."""

from pathlib import Path
import unicodedata

from python_calamine import CalamineWorkbook


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

    for row_number, row in enumerate(rows, start=1):
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
        file_reference = original_data.get("archivo")
        if not normalize(file_reference or ""):
            file_reference = original_data.get("nombre de archivo")
        if not normalize(file_reference or ""):
            continue

        records.append(
            {
                "row_number": row_number,
                "file_reference": file_reference,
                "date": original_data.get("fecha"),
                "original_data": original_data,
            }
        )

    return records


def read_project_excel(root_folder: Path) -> list[dict[str, object]]:
    """Lee la primera hoja del único Excel válido situado en la raíz."""
    excel_files = [
        entry
        for entry in root_folder.iterdir()
        if entry.is_file()
        and not entry.name.startswith("~$")
        and not entry.name.startswith("Trazabilidad MAAT One ")
        and entry.suffix.casefold() in {".xlsx", ".xls"}
    ]
    if len(excel_files) != 1:
        raise ValueError("La raíz no contiene un único Excel válido.")

    workbook = CalamineWorkbook.from_path(excel_files[0])
    if not workbook.sheet_names:
        raise ValueError("El Excel no contiene hojas.")

    rows = workbook.get_sheet_by_name(workbook.sheet_names[0]).to_python(
        skip_empty_area=False
    )
    if not rows:
        raise ValueError("La primera hoja no contiene filas.")
    return interpret_rows(rows)
