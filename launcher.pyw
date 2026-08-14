"""Inicio portable de MAAT One en Windows."""

from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from threading import Thread
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from src.update_service import (
    migrate_flat_installation,
    process_updates_silently,
    read_current_state,
    record_executed_version,
    resolve_active_release,
    rollback_release,
)


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


def _validation_python(
    root: Path,
    finder: ExecutableFinder,
) -> PythonCommand | None:
    local_python = root / ".venv" / "Scripts" / "python.exe"
    if local_python.is_file():
        return [str(local_python)]
    for executable, arguments in (("py.exe", ["-3"]), ("python.exe", [])):
        resolved = finder(executable)
        if resolved:
            return [resolved, *arguments]
    return None


def _smoke_test_release(
    release_root: Path,
    python_command: PythonCommand | None,
) -> bool:
    if python_command is None:
        return False
    validation = (
        "from pathlib import Path; "
        "files=[Path('app.py'),*Path('src').rglob('*.py')]; "
        "[compile(p.read_bytes(),str(p),'exec') for p in files]; "
        "import json,tkinter,PIL,openpyxl,python_calamine,app; "
        "from src.ui.main_window import MainWindow; "
        "json.loads(Path('version.json').read_text(encoding='utf-8'))"
    )
    try:
        completed = subprocess.run(
            [*python_command, "-c", validation],
            cwd=release_root,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _application_survived_start(process: subprocess.Popen) -> bool:
    try:
        process.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        return True
    return False


def launch(
    root: Path | None = None,
    finder: ExecutableFinder = shutil.which,
    error_handler: Callable[[str], None] = show_error,
    update_processor: Callable[..., dict[str, object]] = (
        process_updates_silently
    ),
    loading_runner: Callable[[Callable[[], object]], object] = run_loading_task,
) -> bool:
    """Inicia app.py con el intérprete disponible y la raíz como directorio."""
    install_root = (root or installation_root()).resolve()
    python_command = resolve_python(install_root, finder)
    if python_command is None:
        error_handler(
            "No se encontró una instalación válida de Python. "
            "Instale Python 3 para Windows o prepare el entorno virtual .venv."
        )
        return False

    validation_python = _validation_python(install_root, finder)

    def prepare_and_check() -> dict[str, object]:
        active_root = migrate_flat_installation(install_root)
        state = read_current_state(install_root)
        result = update_processor(
            install_root,
            smoke_test=lambda release_root: _smoke_test_release(
                release_root, validation_python
            ),
        )
        return {
            "initial_active": state["active_version"],
            "active_root": str(active_root),
            "update": result,
        }

    try:
        loading_runner(prepare_and_check)
    except Exception:
        pass

    try:
        application_root = resolve_active_release(install_root)
        active_state = read_current_state(install_root)
    except Exception:
        error_handler(
            "No fue posible determinar una versión válida de MAAT One."
        )
        return False
    application = application_root / "app.py"

    try:
        process = subprocess.Popen(
            [*python_command, str(application)],
            cwd=application_root,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError:
        error_handler(
            "No fue posible iniciar MAAT One con el intérprete de Python encontrado."
        )
        return False
    if _application_survived_start(process):
        record_executed_version(
            install_root, str(active_state["active_version"])
        )
        return True

    try:
        loading_runner(lambda: rollback_release(install_root))
        application_root = resolve_active_release(install_root)
        active_state = read_current_state(install_root)
        subprocess.Popen(
            [*python_command, str(application_root / "app.py")],
            cwd=application_root,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        record_executed_version(
            install_root, str(active_state["active_version"])
        )
    except Exception:
        error_handler("No fue posible iniciar una versión válida de MAAT One.")
        return False
    return True


if __name__ == "__main__":
    launch()
