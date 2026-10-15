"""Recorrido de entradas excluyendo únicamente salidas identificadas de MAAT One."""
import json
import os
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree
from pathlib import Path

MARKER = ".maat-one-output.json"

def is_output(folder: Path) -> bool:
    try:
        data = json.loads((folder / MARKER).read_text(encoding="utf-8"))
        return data == {"app": "MAAT_ONE", "kind": "generated-output", "version": 1}
    except (OSError, ValueError):
        return False

def mark_output(folder: Path) -> None:
    (folder / MARKER).write_text(json.dumps({"app": "MAAT_ONE", "kind": "generated-output", "version": 1}), encoding="utf-8")

def generated_directory(path):
    """Identificación interna del Excel, sin archivos auxiliares ni filtro por nombre."""
    if path.suffix.lower() != ".xlsx": return None
    try:
        with ZipFile(path) as archive:
            core = ElementTree.fromstring(archive.read("docProps/core.xml"))
        identifier = core.findtext("{http://purl.org/dc/elements/1.1/}identifier", "")
        prefix = "MAAT_ONE_OUTPUT_V2|"
        if identifier.startswith(prefix):
            directory = identifier[len(prefix):]
            if directory in {"Anomalias", "Anomalias Separadas"}: return directory
    except (OSError, BadZipFile, KeyError, ElementTree.ParseError):
        pass
    return None

def input_files(root: Path):
    if is_output(root):
        return
    def fail(error):
        raise error
    for current, directories, files in os.walk(root, onerror=fail, followlinks=False):
        parent = Path(current)
        generated = {name: generated_directory(parent / name) for name in files if name.lower().endswith(".xlsx")}
        output_directories = {value for value in generated.values() if value}
        directories[:] = [name for name in directories if name not in output_directories and not is_output(parent / name)]
        for name in files:
            if not generated.get(name): yield parent / name
