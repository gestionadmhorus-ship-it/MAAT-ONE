"""Inicio portable de MAAT One en Windows."""

from pathlib import Path
import shutil
import subprocess
from tkinter import messagebox
from typing import Callable


PythonCommand = list[str]
ExecutableFinder = Callable[[str], str | None]


def installation_root() -> Path:
    """Devuelve la carpeta que contiene este launcher."""
    return Path(__file__).resolve().parent


def resolve_python(
    root: Path,
    finder: ExecutableFinder = shutil.which,
) -> PythonCommand | None:
    """Localiza Python, dando prioridad absoluta al entorno virtual local."""
    local_pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    if local_pythonw.is_file():
        return [str(local_pythonw)]

    for executable, arguments in (
        ("pyw.exe", ["-3"]),
        ("pythonw.exe", []),
        ("py.exe", ["-3"]),
        ("python.exe", []),
    ):
        resolved = finder(executable)
        if resolved:
            return [resolved, *arguments]
    return None


def show_error(message: str) -> None:
    """Muestra un error comprensible sin requerir una consola."""
    messagebox.showerror("MAAT One", message)


def launch(
    root: Path | None = None,
    finder: ExecutableFinder = shutil.which,
    error_handler: Callable[[str], None] = show_error,
) -> bool:
    """Inicia app.py con el intérprete disponible y la raíz como directorio."""
    install_root = (root or installation_root()).resolve()
    application = install_root / "app.py"
    if not application.is_file():
        error_handler(
            "No se encontró app.py en la carpeta de instalación de MAAT One."
        )
        return False

    python_command = resolve_python(install_root, finder)
    if python_command is None:
        error_handler(
            "No se encontró una instalación válida de Python. "
            "Instale Python 3 para Windows o prepare el entorno virtual .venv."
        )
        return False

    try:
        subprocess.Popen(
            [*python_command, str(application)],
            cwd=install_root,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError:
        error_handler(
            "No fue posible iniciar MAAT One con el intérprete de Python encontrado."
        )
        return False
    return True


if __name__ == "__main__":
    launch()
