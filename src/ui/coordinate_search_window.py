"""Ventana independiente para buscar imágenes por coordenadas GPS."""

from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk
from src.ui.progress_dialog import run_task

from src.coordinate_search import (
    DEFAULT_RADIUS_METERS,
    CoordinateSearchError,
    save_coordinate_search,
    search_images_by_coordinates,
    validate_search_parameters,
)


class CoordinateSearchWindow:
    """Flujo autónomo: buscar, revisar la selección y exportar a KMZ."""

    def __init__(self, parent: tk.Tk) -> None:
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("MAAT One · Buscador por coordenadas")
        self.window.geometry("1180x720")
        self.window.minsize(930, 600)
        self.window.transient(parent)
        self.root_folder: Path | None = None
        self.target: dict[str, float] | None = None
        self.radius_meters: float | None = None
        self.results: list[dict[str, object]] = []
        self.included_paths: set[Path] = set()
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.worker_queue: Queue[tuple[str, object]] = Queue()
        self.search_running = False

        self.folder_text = tk.StringVar(value="No se seleccionó una carpeta.")
        self.latitude_text = tk.StringVar()
        self.longitude_text = tk.StringVar()
        self.radius_text = tk.StringVar(value=str(DEFAULT_RADIUS_METERS))
        self.status_text = tk.StringVar(
            value="Seleccione una carpeta, indique coordenadas y radio en metros."
        )
        self._build()
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)

    def _build(self) -> None:
        content = ttk.Frame(self.window, padding=18)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)
        ttk.Label(
            content,
            text="Buscador de imágenes por coordenadas",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            content,
            text=(
                "El radio se mide desde la coordenada exacta ingresada. "
                "El valor inicial de 1 m es un radio de búsqueda."
            ),
            wraplength=900,
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        controls = ttk.LabelFrame(content, text="Ubicación de búsqueda", padding=10)
        controls.grid(row=2, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        ttk.Button(
            controls, text="Seleccionar carpeta raíz", command=self._select_root
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(controls, textvariable=self.folder_text, wraplength=760).grid(
            row=0, column=1, columnspan=5, sticky="w"
        )
        ttk.Label(controls, text="Latitud decimal:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.latitude_text, width=19).grid(
            row=1, column=1, sticky="w", pady=(10, 0)
        )
        ttk.Label(controls, text="Longitud decimal:").grid(row=1, column=2, sticky="w", padx=(14, 4), pady=(10, 0))
        ttk.Entry(controls, textvariable=self.longitude_text, width=19).grid(
            row=1, column=3, sticky="w", pady=(10, 0)
        )
        ttk.Label(controls, text="Radio (m):").grid(row=1, column=4, sticky="w", padx=(14, 4), pady=(10, 0))
        ttk.Entry(controls, textvariable=self.radius_text, width=10).grid(
            row=1, column=5, sticky="w", pady=(10, 0)
        )
        self.search_button = ttk.Button(
            controls, text="Buscar imágenes", command=self._start_search
        )
        self.search_button.grid(row=1, column=6, sticky="e", padx=(14, 0), pady=(10, 0))
        # Enter tiene el mismo efecto que el botón. Así no queda una búsqueda
        # pendiente si la persona termina de cargar un campo y pulsa Enter.
        for variable in (self.latitude_text, self.longitude_text, self.radius_text):
            variable.trace_add("write", self._enable_search_button)
        self.window.bind("<Return>", self._start_search_from_keyboard)

        workspace = ttk.Frame(content)
        workspace.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        workspace.columnconfigure(0, weight=3)
        workspace.columnconfigure(1, weight=2)
        workspace.rowconfigure(0, weight=1)
        result_box = ttk.LabelFrame(workspace, text="Imágenes encontradas", padding=8)
        result_box.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        result_box.columnconfigure(0, weight=1)
        result_box.rowconfigure(0, weight=1)
        columns = ("included", "name", "distance", "coordinates", "source")
        self.results_tree = ttk.Treeview(result_box, columns=columns, show="headings", selectmode="browse")
        headings = {
            "included": ("Incluir", 65), "name": ("Archivo", 235),
            "distance": ("Distancia", 85), "coordinates": ("Coordenadas", 175),
            "source": ("GPS", 55),
        }
        for column, (label, width) in headings.items():
            self.results_tree.heading(column, text=label)
            self.results_tree.column(column, width=width, anchor="w", stretch=column == "name")
        scrollbar = ttk.Scrollbar(result_box, orient="vertical", command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=scrollbar.set)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.results_tree.bind("<<TreeviewSelect>>", self._show_preview)
        self.results_tree.bind("<Double-Button-1>", self._toggle_selected)
        actions = ttk.Frame(result_box)
        actions.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(actions, text="Quitar de la selección", command=self._exclude_selected).pack(side="left")
        ttk.Button(actions, text="Volver a incluir", command=self._include_selected).pack(side="left", padx=(8, 0))

        preview_box = ttk.LabelFrame(workspace, text="Vista previa", padding=8)
        preview_box.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        preview_box.columnconfigure(0, weight=1)
        preview_box.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(preview_box, text="Seleccione una imagen", anchor="center")
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.details_label = ttk.Label(preview_box, text="", justify="left", wraplength=350)
        self.details_label.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        footer = ttk.Frame(content)
        footer.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, wraplength=810).grid(row=0, column=0, sticky="w")
        self.save_button = ttk.Button(
            footer, text="Guardar selección y KMZ", command=self._save, state="disabled"
        )
        self.save_button.grid(row=0, column=1, sticky="e")

    def _select_root(self) -> None:
        selected = filedialog.askdirectory(parent=self.window, title="Carpeta raíz para buscar imágenes")
        if selected:
            self.root_folder = Path(selected)
            self.folder_text.set(str(self.root_folder))

    def _start_search(self) -> None:
        if self.search_running:
            return
        if self.root_folder is None:
            messagebox.showwarning("Falta carpeta", "Seleccione la carpeta raíz donde buscar.", parent=self.window)
            return
        try:
            target, radius = validate_search_parameters(
                self.latitude_text.get(), self.longitude_text.get(), self.radius_text.get()
            )
        except CoordinateSearchError as error:
            messagebox.showwarning("Datos inválidos", str(error), parent=self.window)
            return
        # Conservar una copia local evita que un cambio de carpeta mientras el
        # hilo trabaja pueda alterar el origen de la búsqueda en curso.
        root_folder = self.root_folder
        self.target, self.radius_meters = target, radius
        self.search_running = True
        self.search_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.status_text.set("Preparando la búsqueda y localizando imágenes…")
        while not self.worker_queue.empty():
            self.worker_queue.get_nowait()

        def progress(current: int, total: int) -> None:
            self.worker_queue.put(("progress", (current, total)))

        try:
            result = run_task(self.window, "Buscando por coordenadas", lambda report: search_images_by_coordinates(
                root_folder, target, radius,
                lambda current, total: report(f"Leyendo GPS: {current} de {total} imágenes", current, total)))
        except Exception as error:
            self._finish_search()
            messagebox.showerror("No se pudo buscar", str(error), parent=self.window)
            return
        self._finish_search()
        self._use_search_result(result)

    def _start_search_from_keyboard(self, _event: object = None) -> str:
        """Ejecuta la búsqueda solo si el foco está en esta ventana."""
        if self.window.focus_displayof() is not None:
            self._start_search()
        return "break"

    def _enable_search_button(self, *_args: object) -> None:
        """Mantiene el botón disponible al corregir un dato tras un error."""
        if not self.search_running:
            self.search_button.configure(state="normal")

    def _poll_search(self) -> None:
        try:
            while True:
                event, payload = self.worker_queue.get_nowait()
                if event == "progress":
                    current, total = payload
                    self.status_text.set(f"Leyendo GPS: {current} de {total} imágenes…")
                elif event == "error":
                    self._finish_search()
                    messagebox.showerror("No se pudo buscar", str(payload), parent=self.window)
                    return
                else:
                    self._finish_search()
                    self._use_search_result(payload)
                    return
        except Empty:
            pass
        if self.search_running and self.window.winfo_exists():
            self.window.after(80, self._poll_search)

    def _finish_search(self) -> None:
        self.search_running = False
        self.search_button.configure(state="normal")

    def _use_search_result(self, result: dict[str, object]) -> None:
        matches = result["matches"]
        if not matches and result["nearest"] is not None:
            nearest = result["nearest"]
            accepted = messagebox.askyesno(
                "Sin imágenes en el radio",
                (
                    f"No se encontraron imágenes dentro de {result['radius_meters']:.2f} m.\n\n"
                    f"La más cercana es {nearest['name']} a {nearest['distance_meters']:.2f} m.\n"
                    "¿Usar su posición GPS como nueva referencia y buscar con el mismo radio?"
                ),
                parent=self.window,
            )
            if accepted:
                coordinates = nearest["coordinates"]
                self.latitude_text.set(f"{coordinates['latitude']:.8f}")
                self.longitude_text.set(f"{coordinates['longitude']:.8f}")
                self._start_search()
                return
        self.results = list(matches)
        self.included_paths = {Path(item["path"]) for item in self.results}
        self._render_results()
        scanned = result["images_scanned"]
        missing = result["metadata_missing"]
        self.status_text.set(
            f"{len(self.results)} imagen(es) dentro de {result['radius_meters']:.2f} m. "
            f"Analizadas: {scanned}; sin GPS: {missing}."
        )
        self.save_button.configure(state="normal" if self.results else "disabled")

    def _render_results(self) -> None:
        for item_id in self.results_tree.get_children():
            self.results_tree.delete(item_id)
        for index, item in enumerate(self.results):
            coordinates = item["coordinates"]
            path = Path(item["path"])
            self.results_tree.insert("", "end", iid=str(index), values=(
                "Sí" if path in self.included_paths else "No", item["name"],
                f"{item['distance_meters']:.2f} m",
                f"{coordinates['latitude']:.7f}, {coordinates['longitude']:.7f}",
                coordinates["source"],
            ))
        self.preview_label.configure(image="", text="Seleccione una imagen")
        self.preview_photo = None
        self.details_label.configure(text="")

    def _current_item(self) -> dict[str, object] | None:
        selected = self.results_tree.selection()
        return self.results[int(selected[0])] if selected else None

    def _toggle_selected(self, _event: object = None) -> None:
        item = self._current_item()
        if item is None:
            return
        path = Path(item["path"])
        if path in self.included_paths:
            self.included_paths.remove(path)
        else:
            self.included_paths.add(path)
        self._render_results()
        self.save_button.configure(state="normal" if self.included_paths else "disabled")

    def _exclude_selected(self) -> None:
        item = self._current_item()
        if item is not None:
            self.included_paths.discard(Path(item["path"]))
            self._render_results()
            self.save_button.configure(state="normal" if self.included_paths else "disabled")

    def _include_selected(self) -> None:
        item = self._current_item()
        if item is not None:
            self.included_paths.add(Path(item["path"]))
            self._render_results()
            self.save_button.configure(state="normal")

    def _show_preview(self, _event: object = None) -> None:
        item = self._current_item()
        if item is None:
            return
        path = Path(item["path"])
        coordinates = item["coordinates"]
        try:
            with Image.open(path) as source:
                preview = ImageOps.exif_transpose(source).copy()
                preview.thumbnail((420, 420), Image.Resampling.LANCZOS)
            self.preview_photo = ImageTk.PhotoImage(preview)
            self.preview_label.configure(image=self.preview_photo, text="")
        except (OSError, ValueError, tk.TclError):
            self.preview_photo = None
            self.preview_label.configure(image="", text="No se pudo previsualizar")
        self.details_label.configure(text=(
            f"{path.name}\nDistancia: {item['distance_meters']:.2f} m\n"
            f"GPS: {coordinates['latitude']:.8f}, {coordinates['longitude']:.8f}\n"
            f"Origen: {coordinates['source']}\n\n{path}"
        ))

    def _save(self) -> None:
        if self.root_folder is None or self.target is None or self.radius_meters is None:
            return
        selected = [item for item in self.results if Path(item["path"]) in self.included_paths]
        try:
            output = run_task(self.window, "Guardando imágenes y KMZ", lambda report: save_coordinate_search(self.root_folder, self.target, self.radius_meters, selected))
        except CoordinateSearchError as error:
            messagebox.showerror("No se pudo guardar", str(error), parent=self.window)
            return
        self.status_text.set(f"Guardadas {output['images_saved']} imágenes en: {output['destination']}")
        messagebox.showinfo(
            "Búsqueda guardada",
            f"Se guardaron {output['images_saved']} imágenes y el KMZ.\n\n{output['destination']}",
            parent=self.window,
        )
