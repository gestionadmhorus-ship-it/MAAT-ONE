"""Inicio portable de MAAT One en Windows."""

from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from threading import Thread
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from src.update_service import process_updates_silently


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


class LoadingWindow:
    """Pantalla única mostrada mientras se prepara el inicio de MAAT One."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("MAAT One")
        self.root.geometry("440x180")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        self.root.configure(background="#F5F7FA")

        content = tk.Frame(self.root, background="#F5F7FA", padx=28, pady=24)
        content.pack(fill="both", expand=True)
        tk.Label(
            content,
            text="MAAT One",
            font=("Segoe UI", 18, "bold"),
            background="#F5F7FA",
            foreground="#172033",
        ).pack(pady=(0, 12))
        tk.Label(
            content,
            text="Cargando MAAT One...",
            font=("Segoe UI", 11, "bold"),
            background="#F5F7FA",
            foreground="#172033",
        ).pack()
        tk.Label(
            content,
            text="Por favor, espere. No cierre ni apague el equipo.",
            font=("Segoe UI", 9),
            background="#F5F7FA",
            foreground="#5E6878",
        ).pack(pady=(7, 0))
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def run(self, task: Callable[[], object]) -> object:
        """Mantiene Tkinter en el hilo principal mientras ejecuta la tarea."""
        completed: Queue[tuple[bool, object]] = Queue(maxsize=1)

        def worker() -> None:
            try:
                completed.put((True, task()))
            except Exception as error:
                completed.put((False, error))

        Thread(target=worker, daemon=True).start()
        result: object = None
        failure: Exception | None = None

        def poll() -> None:
            nonlocal result, failure
            try:
                succeeded, value = completed.get_nowait()
            except Empty:
                self.root.after(50, poll)
                return
            if succeeded:
                result = value
            else:
                failure = value if isinstance(value, Exception) else RuntimeError()
            self.root.destroy()

        self.root.after(50, poll)
        self.root.mainloop()
        if failure is not None:
            raise failure
        return result


def run_loading_task(task: Callable[[], object]) -> object:
    """Ejecuta una tarea mostrando únicamente la pantalla de carga."""
    return LoadingWindow().run(task)


def launch(
    root: Path | None = None,
    finder: ExecutableFinder = shutil.which,
    error_handler: Callable[[str], None] = show_error,
    update_processor: Callable[[Path], dict[str, object]] = (
        process_updates_silently
    ),
    loading_runner: Callable[[Callable[[], object]], object] = run_loading_task,
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
        loading_runner(lambda: update_processor(install_root))
    except Exception:
        pass

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
