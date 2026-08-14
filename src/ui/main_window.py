"""Ventana principal de MAAT One."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageOps, ImageTk

from src.dji_parser import parse_dji_images
from src.excel_traceability import TraceabilityError, generate_traceability_excel
from src.excel_reader import read_project_excel
from src.geographic_export import (
    GeographicExportError,
    export_geographic_selection,
)
from src.geographic_search import (
    GeographicSearchError,
    find_nearest_gps_image,
    find_images_by_name,
    search_by_coordinates,
    search_by_reference_image,
    validate_coordinate_input,
)
from src.geographic_selection import GeographicSelection
from src.gps_metadata import GPSMetadataCache
from src.image_inventory import build_image_inventory, inventory_project_images
from src.output_copier import copy_planned_images
from src.output_planner import build_output_plan
from src.set_builder import build_sets
from src.zoom_matcher import decide_zoom, match_zoom_images


def validate_root_folder(root_folder: Path) -> str:
    """Valida únicamente los elementos hijos directos de la carpeta raíz."""
    entries = list(root_folder.iterdir())

    materials_found = any(
        entry.is_dir() and entry.name.casefold() == "materiales"
        for entry in entries
    )
    if not materials_found:
        return (
            "Proyecto no válido\n"
            "No se encontró la carpeta Materiales en la raíz."
        )

    excel_files = [
        entry
        for entry in entries
        if entry.is_file()
        and not entry.name.startswith("~$")
        and not entry.name.startswith("Trazabilidad MAAT One ")
        and entry.suffix.casefold() in {".xlsx", ".xls"}
    ]
    if not excel_files:
        return (
            "Proyecto no válido\n"
            "No se encontró un archivo Excel en la raíz."
        )
    if len(excel_files) > 1:
        return (
            "Proyecto no válido\n"
            "Se encontró más de un archivo Excel en la raíz."
        )

    return (
        "Proyecto válido\n"
        f"Carpeta: {root_folder.name}\n"
        f"Excel: {excel_files[0].name}\n"
        "Materiales: encontrada"
    )


class ZoomViewer(ttk.Label):
    """Visor único con infraestructura de pantalla completa, zoom y paneo."""

    MAX_ZOOM = 8.0
    ZOOM_STEP = 1.2

    def __init__(
        self,
        master: tk.Misc,
        normal_host: tk.Misc,
        on_view_changed,
    ) -> None:
        super().__init__(master, text="", anchor="center", style="Viewer.TLabel")
        self.normal_host = normal_host
        self.on_view_changed = on_view_changed
        self.fullscreen = False
        self.zoom_factor = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._content_width = 0
        self._content_height = 0
        self.source_path: Path | None = None
        self._drag_x: int | None = None
        self._drag_y: int | None = None
        self.bind("<Double-Button-1>", self._toggle_fullscreen)
        self.bind("<MouseWheel>", self._on_mouse_wheel)
        self.bind("<Button-4>", self._on_mouse_wheel)
        self.bind("<Button-5>", self._on_mouse_wheel)
        self.bind("<ButtonPress-1>", self._start_pan)
        self.bind("<B1-Motion>", self._pan)
        self.bind("<Configure>", self._on_configure)
        self.show_embedded()

    def show_embedded(self) -> None:
        self.fullscreen = False
        self.place(
            in_=self.normal_host,
            x=0,
            y=0,
            relwidth=1,
            relheight=1,
        )
        self.lift()
        self._notify_view_changed()

    def show_fullscreen(self) -> None:
        self.fullscreen = True
        self.place(in_=self.master, x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        self._notify_view_changed()

    def set_content_size(self, width: int, height: int) -> None:
        self._content_width = max(width, 0)
        self._content_height = max(height, 0)
        self._clamp_offsets()

    def reset_view(self) -> None:
        self.zoom_factor = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0

    def _toggle_fullscreen(self, _event: object = None) -> None:
        if self.fullscreen:
            self.reset_view()
            self.show_embedded()
        else:
            self.show_fullscreen()

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        direction = getattr(event, "delta", 0)
        if not direction:
            direction = 1 if getattr(event, "num", 0) == 4 else -1
        multiplier = self.ZOOM_STEP if direction > 0 else 1 / self.ZOOM_STEP
        previous_zoom = self.zoom_factor
        next_zoom = min(
            self.MAX_ZOOM,
            max(1.0, previous_zoom * multiplier),
        )
        if next_zoom == 1.0:
            self.zoom_factor = 1.0
            self.offset_x = 0.0
            self.offset_y = 0.0
        else:
            scale_change = next_zoom / previous_zoom
            cursor_x = event.x - self.winfo_width() / 2
            cursor_y = event.y - self.winfo_height() / 2
            self.offset_x = cursor_x - scale_change * (
                cursor_x - self.offset_x
            )
            self.offset_y = cursor_y - scale_change * (
                cursor_y - self.offset_y
            )
            self.zoom_factor = next_zoom
        self._clamp_offsets()
        self._notify_view_changed()

    def _start_pan(self, event: tk.Event) -> None:
        if self.zoom_factor == 1.0:
            self.offset_x = 0.0
            self.offset_y = 0.0
            self._drag_x = None
            self._drag_y = None
            return
        self._drag_x = event.x
        self._drag_y = event.y

    def _pan(self, event: tk.Event) -> None:
        if (
            self.zoom_factor == 1.0
            or self._drag_x is None
            or self._drag_y is None
        ):
            self.offset_x = 0.0
            self.offset_y = 0.0
            return
        self.offset_x += event.x - self._drag_x
        self.offset_y += event.y - self._drag_y
        self._drag_x = event.x
        self._drag_y = event.y
        self._clamp_offsets()
        self._notify_view_changed()

    def _clamp_offsets(self) -> None:
        scaled_width = self._content_width * self.zoom_factor
        scaled_height = self._content_height * self.zoom_factor
        limit_x = max((scaled_width - self.winfo_width()) / 2, 0.0)
        limit_y = max((scaled_height - self.winfo_height()) / 2, 0.0)
        self.offset_x = min(limit_x, max(-limit_x, self.offset_x))
        self.offset_y = min(limit_y, max(-limit_y, self.offset_y))

    def _notify_view_changed(self) -> None:
        if self.on_view_changed is not None:
            self.on_view_changed()

    def _on_configure(self, _event: object = None) -> None:
        self._clamp_offsets()
        self._notify_view_changed()


class MainWindow:
    """Interfaz inicial de la aplicación."""

    LIGHT_THEME = {
        "background": "#F5F7FA",
        "panel": "#F5F7FA",
        "text": "#172033",
        "muted": "#5E6878",
        "border": "#D1D5DB",
        "field": "#FFFFFF",
        "selection": "#2563EB",
        "selection_text": "#FFFFFF",
        "accent": "#2563EB",
        "accent_active": "#1D4ED8",
        "viewer": "#E5E7EB",
        "alternative": "#F3F4F6",
        "suggested": "#DBEAFE",
        "suggested_border": "#2563EB",
        "viewed_border": "#F59E0B",
    }
    DARK_THEME = {
        "background": "#181B20",
        "panel": "#22262D",
        "text": "#F1F5F9",
        "muted": "#B8C0CC",
        "border": "#454C57",
        "field": "#292E36",
        "selection": "#3B82F6",
        "selection_text": "#FFFFFF",
        "accent": "#3B82F6",
        "accent_active": "#2563EB",
        "viewer": "#111419",
        "alternative": "#2B3038",
        "suggested": "#17375E",
        "suggested_border": "#60A5FA",
        "viewed_border": "#FBBF24",
    }

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.dark_mode = False
        self._theme = self.LIGHT_THEME
        self.root.title("MAAT One")
        self.root.geometry("1280x800")
        self.root.minsize(1050, 680)
        self.root.configure(background=self._theme["background"])

        self.status = tk.StringVar(
            value="Seleccione la carpeta raíz del proyecto."
        )
        self.zoom_results: list[dict[str, object]] = []
        self.set_results: list[dict[str, object]] = []
        self.set_id_map: dict[tuple[object, str], str] = {}
        self.next_set_id = 1
        self.current_root: Path | None = None
        self.output_plan: dict[str, object] | None = None
        self.output_is_stale = False
        self.pending_reviews: list[dict[str, object]] = []
        self.review_items: list[dict[str, object]] = []
        self.review_index = 0
        self.review_active = False
        self.review_mode = tk.StringVar(value="Revisar pendientes")
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._set_preview_photos: dict[str, ImageTk.PhotoImage] = {}
        self._gps_metadata = GPSMetadataCache()
        self._viewed_zoom_candidate: dict[str, object] | None = None
        self._suggested_zoom: dict[str, object] | None = None
        self._original_suggested_zoom: dict[str, object] | None = None
        self._target_time_seconds: int | None = None
        self._zoom_neighbor_items: list[tuple[str, dict[str, object]]] = []
        self._zoom_thumbnail_photos: list[ImageTk.PhotoImage] = []
        self._alternative_click_job: str | None = None
        self._review_preview_rendering = False
        self._review_preview_transitioning = False
        self._geographic_view_built = False
        self._geographic_root: Path | None = None
        self._geographic_images: list[dict[str, str]] = []
        self._geographic_gps_metadata = GPSMetadataCache()
        self._geographic_name_matches: list[dict[str, str]] = []
        self._geographic_reference: dict[str, str] | None = None
        self._geographic_selection = GeographicSelection()
        self._geographic_preview_photo: ImageTk.PhotoImage | None = None
        self._geographic_preview_rendering = False
        self._geographic_without_gps_count = 0

        self._configure_styles()
        self._build_interface()

    def _distance_from_suggested(
        self,
        suggested_path: str | Path,
        candidate_path: str | Path,
    ) -> float | None:
        return self._gps_metadata.distance_from_suggested(
            suggested_path,
            candidate_path,
        )

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self._apply_theme()

    def _apply_theme(self) -> None:
        self._theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME
        palette = self._theme
        style = ttk.Style(self.root)
        self.root.configure(background=palette["background"])
        style.configure("TFrame", background=palette["panel"])
        style.configure("App.TFrame", background=palette["background"])
        style.configure(
            "Title.TLabel",
            background=palette["background"],
            foreground=palette["text"],
            font=("Segoe UI", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=palette["background"],
            foreground=palette["muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "TLabel",
            background=palette["panel"],
            foreground=palette["text"],
        )
        style.configure(
            "TLabelframe",
            background=palette["panel"],
            bordercolor=palette["border"],
        )
        style.configure(
            "TLabelframe.Label",
            background=palette["panel"],
            foreground=palette["text"],
        )
        style.configure(
            "TButton",
            background=palette["field"],
            foreground=palette["text"],
            bordercolor=palette["border"],
        )
        style.map(
            "TButton",
            background=[("active", palette["border"])],
            foreground=[("disabled", palette["muted"])],
        )
        style.configure(
            "Action.TButton",
            background=palette["accent"],
            foreground=palette["selection_text"],
            font=("Segoe UI", 10, "bold"),
            padding=(18, 7),
            borderwidth=0,
        )
        style.map(
            "Action.TButton",
            background=[("active", palette["accent_active"])],
            foreground=[("active", palette["selection_text"])],
        )
        style.configure(
            "Theme.TButton",
            background=palette["panel"],
            foreground=palette["muted"],
            bordercolor=palette["border"],
            padding=(8, 3),
        )
        style.configure(
            "Section.TLabel",
            background=palette["background"],
            foreground=palette["text"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=palette["background"],
            foreground=palette["muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "TCombobox",
            fieldbackground=palette["field"],
            background=palette["field"],
            foreground=palette["text"],
            arrowcolor=palette["text"],
            bordercolor=palette["border"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["field"])],
            foreground=[("readonly", palette["text"])],
            selectbackground=[("readonly", palette["selection"])],
            selectforeground=[("readonly", palette["selection_text"])],
        )
        style.configure(
            "TScrollbar",
            background=palette["field"],
            troughcolor=palette["panel"],
            bordercolor=palette["border"],
            arrowcolor=palette["text"],
        )
        style.configure("Viewer.TLabel", background=palette["viewer"])
        if hasattr(self, "result_text"):
            self.result_text.configure(
                background=palette["background"],
                foreground=palette["muted"],
                insertbackground=palette["text"],
            )
        if hasattr(self, "_geographic_matches_text"):
            self._geographic_matches_text.configure(
                background=palette["field"],
                foreground=palette["text"],
                highlightbackground=palette["border"],
                highlightcolor=palette["accent"],
            )
            self._geographic_matches_text.tag_configure(
                "Selected.Reference",
                background=palette["selection"],
                foreground=palette["selection_text"],
            )
        for name in ("set_list", "review_list"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(
                    background=palette["field"],
                    foreground=palette["text"],
                    selectbackground=palette["selection"],
                    selectforeground=palette["selection_text"],
                    highlightbackground=palette["border"],
                    highlightcolor=palette["accent"],
                )
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.configure(background=palette["background"])
        self._apply_zoom_alternatives_theme()

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()
        self.theme_button.configure(text="Claro" if self.dark_mode else "Oscuro")

    def _build_interface(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill="both", expand=True)

        view_selector = ttk.Frame(shell, style="App.TFrame", padding=(24, 10))
        view_selector.pack(fill="x")
        self.processing_view_button = ttk.Button(
            view_selector,
            text="Procesar proyecto",
            command=self._show_processing_view,
        )
        self.processing_view_button.pack(side="left")
        self.geographic_search_button = ttk.Button(
            view_selector,
            text="Buscar por ubicación",
            command=self._open_geographic_search,
        )
        self.geographic_search_button.pack(side="left", padx=(8, 0))

        self.theme_button = ttk.Button(
            view_selector,
            text="Oscuro",
            command=self._toggle_theme,
            style="Theme.TButton",
        )
        self.theme_button.pack(side="right")

        self._view_host = ttk.Frame(shell, style="App.TFrame")
        self._view_host.pack(fill="both", expand=True)
        self._processing_view = ttk.Frame(
            self._view_host,
            style="App.TFrame",
            padding=(24, 4, 24, 14),
        )
        self._processing_view.pack(fill="both", expand=True)
        self._geographic_view = ttk.Frame(
            self._view_host,
            style="App.TFrame",
        )
        content = self._processing_view

        ttk.Label(content, text="MAAT One", style="Title.TLabel").pack()
        ttk.Label(
            content,
            text="Preparación Inteligente de SET de Imágenes",
            style="Subtitle.TLabel",
        ).pack(pady=(2, 8))

        ttk.Button(
            content,
            text="Seleccionar carpeta raíz",
            command=self._select_root_folder,
            style="Action.TButton",
            cursor="hand2",
        ).pack()
        self.finalize_button = ttk.Button(
            content,
            text="Finalizar y generar",
            command=self._finalize_output,
            state="disabled",
        )
        self.finalize_button.pack(pady=(4, 0))
        self.output_plan_summary = tk.StringVar(value="")
        ttk.Label(
            content,
            textvariable=self.output_plan_summary,
            style="Status.TLabel",
            justify="left",
        ).pack(pady=(3, 0))

        ttk.Separator(content, orient="horizontal").pack(
            fill="x", pady=(12, 10)
        )
        workspace = ttk.Frame(content, style="App.TFrame")
        workspace.pack(fill="both", expand=True)
        workspace.columnconfigure(0, weight=9, uniform="workspace")
        workspace.columnconfigure(1, weight=11, uniform="workspace")
        workspace.rowconfigure(0, weight=1)
        result_area = ttk.Frame(workspace, style="App.TFrame")
        ttk.Label(result_area, text="Estado:", style="Section.TLabel").pack(
            anchor="w"
        )
        text_area = ttk.Frame(result_area, style="App.TFrame")
        self.result_text = tk.Text(
            text_area,
            background=self._theme["background"],
            foreground=self._theme["muted"],
            font=("Segoe UI", 10),
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            state="disabled",
            height=5,
        )
        scrollbar = ttk.Scrollbar(
            text_area,
            orient="vertical",
            command=self.result_text.yview,
        )
        self.result_text.configure(yscrollcommand=scrollbar.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        set_panel = ttk.LabelFrame(
            result_area,
            text="Verificación de SET",
            padding=(8, 6),
        )
        set_panel.pack(fill="both", expand=True, pady=(6, 0))
        self.set_list = tk.Listbox(
            set_panel,
            height=3,
            font=("Segoe UI", 9),
            exportselection=False,
        )
        self.set_list.pack(fill="x")
        self.set_list.bind("<<ListboxSelect>>", self._on_set_selected)
        self.set_details = ttk.Label(
            set_panel,
            text="Sin SET disponibles",
            justify="left",
            wraplength=520,
        )
        self.set_details.pack(anchor="w", fill="x", pady=(4, 3))
        set_previews = ttk.Frame(set_panel)
        set_previews.pack(fill="x")
        self.set_preview_labels: dict[str, ttk.Label] = {}
        for channel in ("Wide", "Zoom", "Thermal"):
            preview_area = ttk.Frame(set_previews)
            preview_area.pack(side="left", fill="both", expand=True, padx=2)
            ttk.Label(preview_area, text=channel).pack()
            preview_label = ttk.Label(
                preview_area,
                text="NO ENCONTRADA",
                anchor="center",
                width=18,
            )
            preview_label.pack(fill="both", expand=True)
            self.set_preview_labels[channel] = preview_label
        text_area.pack(fill="x", pady=(4, 0))

        review_panel = ttk.LabelFrame(
            workspace,
            text="Revisión de Zoom",
            padding=(10, 7),
        )
        result_area.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        review_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        review_panel.columnconfigure(0, weight=1)
        review_panel.rowconfigure(3, weight=1)
        mode_selector = ttk.Combobox(
            review_panel,
            textvariable=self.review_mode,
            values=("Revisar pendientes", "Ver todos los SET"),
            state="readonly",
        )
        mode_selector.grid(row=0, column=0, sticky="ew")
        mode_selector.bind("<<ComboboxSelected>>", self._on_review_mode_changed)
        self.review_progress = ttk.Label(
            review_panel,
            text="Sin revisiones de Zoom necesarias",
            justify="left",
        )
        self.review_progress.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.review_list = tk.Listbox(
            review_panel,
            height=2,
            width=44,
            font=("Segoe UI", 9),
            exportselection=False,
        )
        self.review_list.grid(
            row=2, column=0, sticky="ew", pady=(4, 2)
        )
        self.review_list.bind("<<ListboxSelect>>", self._on_review_selected)
        review_body = ttk.Frame(review_panel)
        review_body.grid(row=3, column=0, sticky="nsew", pady=(3, 0))
        review_body.columnconfigure(0, weight=1)
        review_body.columnconfigure(1, weight=5)
        review_body.rowconfigure(0, weight=1)
        self.review_details = ttk.Label(
            review_body,
            text="",
            justify="left",
            wraplength=165,
        )
        self.review_details.grid(
            row=0, column=0, sticky="nsew", padx=(0, 6)
        )
        self.review_preview_area = ttk.Frame(
            review_body,
        )
        self.review_preview_area.grid(row=0, column=1, sticky="nsew")
        self.zoom_viewer = ZoomViewer(
            self.root,
            self.review_preview_area,
            self._on_review_preview_resize,
        )
        self.preview_label = self.zoom_viewer
        self.review_preview_area.bind(
            "<Configure>", self._on_review_preview_resize
        )
        self.zoom_alternatives = ttk.Frame(review_panel)
        self.zoom_alternatives.grid(
            row=4, column=0, sticky="ew", pady=(4, 0)
        )
        decisions = ttk.Frame(review_panel)
        decisions.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        self.reject_button = ttk.Button(
            decisions,
            text="Rechazar",
            command=lambda: self._decide_current_zoom(False),
            state="disabled",
        )
        self.reject_button.pack(side="left", expand=True, padx=(0, 6))
        self.accept_button = ttk.Button(
            decisions,
            text="Aceptar",
            command=lambda: self._decide_current_zoom(True),
            state="disabled",
        )
        self.accept_button.pack(side="right", expand=True, padx=(6, 0))
        self.fullscreen_button = ttk.Button(
            decisions,
            text="Pantalla completa",
            command=self._open_fullscreen_review,
            state="disabled",
        )
        self.fullscreen_button.pack(side="left", expand=True, padx=6)
        self.use_zoom_button = ttk.Button(
            decisions,
            text="Usar esta Zoom",
            command=self._use_viewed_zoom,
            state="disabled",
        )
        self.use_zoom_button.pack(side="left", expand=True, padx=(6, 0))
        self.status.trace_add("write", self._update_status_display)
        self._update_status_display()
        self._apply_theme()

    def _update_status_display(self, *_args: object) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", self.status.get())
        self.result_text.configure(state="disabled")

    @staticmethod
    def _format_time(time_text: object) -> str:
        value = str(time_text)
        return f"{value[0:2]}:{value[2:4]}:{value[4:6]}"

    def _show_current_review(self) -> None:
        self._review_preview_transitioning = True
        try:
            if not self.review_items:
                previous_photo = self._preview_photo
                self.preview_label.configure(image="", text="")
                self._preview_photo = None
                self._defer_preview_photo_release(previous_photo)
                self.review_details.configure(text="")
                self._clear_zoom_alternatives()
                self._suggested_zoom = None
                self._original_suggested_zoom = None
                self._viewed_zoom_candidate = None
                self.use_zoom_button.configure(state="disabled")
                self.reject_button.configure(state="disabled")
                self.accept_button.configure(state="disabled")
                self.fullscreen_button.configure(state="disabled")
                return

            selection = self.review_list.curselection()
            if selection:
                self.review_index = selection[0]
            result = self.review_items[self.review_index]
            zoom = result["zoom"]
            self._original_suggested_zoom = result.get(
                "original_suggested_zoom",
                zoom,
            )
            self._suggested_zoom = zoom
            self._viewed_zoom_candidate = zoom
            self._target_time_seconds = (
                zoom["time_seconds"] - result["time_difference"]
            )
            self._zoom_neighbor_items = self._zoom_neighbors(zoom)
            self._show_review_candidate(zoom)
            self.fullscreen_button.configure(state="normal")
            reviewable = result["status"] in {
                "ZOOM_PENDING_REVIEW",
                "ZOOM_ACCEPTED",
                "ZOOM_REJECTED",
            }
            button_state = "normal" if reviewable else "disabled"
            self.reject_button.configure(state=button_state)
            self.accept_button.configure(state=button_state)
        finally:
            self._review_preview_transitioning = False

    def _zoom_neighbors(
        self,
        suggested: dict[str, object],
    ) -> list[tuple[str, dict[str, object]]]:
        zoom_images = sorted(
            (
                image
                for image in self._parsed_images
                if image.get("recognized")
                and image.get("channel") == "Z"
                and image.get("date") == suggested.get("date")
            ),
            key=lambda image: (
                image["date"],
                image["time_seconds"],
                str(image["sequence"]),
            ),
        )
        suggested_path = str(Path(suggested["path"]).resolve()).casefold()
        suggested_index = next(
            (
                index
                for index, image in enumerate(zoom_images)
                if str(Path(image["path"]).resolve()).casefold()
                == suggested_path
            ),
            None,
        )
        if suggested_index is None:
            return [("Sugerida", suggested)]
        labels = {
            -2: "Anterior 2",
            -1: "Anterior 1",
            0: "Sugerida",
            1: "Siguiente 1",
            2: "Siguiente 2",
        }
        start = max(suggested_index - 2, 0)
        end = min(suggested_index + 3, len(zoom_images))
        return [
            (labels[index - suggested_index], zoom_images[index])
            for index in range(start, end)
        ]

    def _show_review_candidate(
        self,
        candidate: dict[str, object],
    ) -> None:
        self._viewed_zoom_candidate = candidate
        self._review_source_path = Path(candidate["path"])
        result = self.review_items[self.review_index]
        suggested = self._suggested_zoom
        visual_difference = (
            candidate["time_seconds"] - self._target_time_seconds
        )
        identity = self._set_identity(result)
        set_id = self.set_id_map.get(identity, "Pendiente de asignación")
        self.review_details.configure(
            text=(
                f"SET: {set_id}\n"
                f"Fila: {result['row_number']}\n"
                f"Referencia: {result['file_reference']}\n"
                f"Hora esperada: {self._format_time(result['target_time'])}\n\n"
                f"Original: {self._original_suggested_zoom['name']}\n"
                f"Utilizada: {suggested['name']}\n"
                f"Visualizada: {candidate['name']}\n"
                f"Hora visualizada: {self._format_time(candidate['time'])}\n"
                f"Diferencia: {visual_difference:+d} segundos\n"
                f"Secuencia: {candidate['sequence']}\n\n"
                f"Ruta: {candidate['path']}"
            )
        )
        self._load_review_preview()
        self._refresh_fullscreen_preview()
        self._update_use_zoom_state()
        self._render_zoom_alternatives()

    def _update_use_zoom_state(self) -> None:
        if (
            self._suggested_zoom is None
            or self._viewed_zoom_candidate is None
        ):
            self.use_zoom_button.configure(state="disabled")
            return
        suggested_path = str(
            Path(self._suggested_zoom["path"]).resolve()
        ).casefold()
        viewed_path = str(
            Path(self._viewed_zoom_candidate["path"]).resolve()
        ).casefold()
        self.use_zoom_button.configure(
            state="disabled" if suggested_path == viewed_path else "normal"
        )

    def _center_dialog(self, dialog: tk.Toplevel) -> None:
        dialog.update_idletasks()
        position_x = (
            self.root.winfo_rootx()
            + (self.root.winfo_width() - dialog.winfo_width()) // 2
        )
        position_y = (
            self.root.winfo_rooty()
            + (self.root.winfo_height() - dialog.winfo_height()) // 2
        )
        dialog.geometry(f"+{max(position_x, 0)}+{max(position_y, 0)}")

    def _use_viewed_zoom(self) -> None:
        if (
            self._suggested_zoom is None
            or self._viewed_zoom_candidate is None
        ):
            return
        original_path = str(
            Path(self._suggested_zoom["path"]).resolve()
        ).casefold()
        selected_path = str(
            Path(self._viewed_zoom_candidate["path"]).resolve()
        ).casefold()
        if original_path == selected_path:
            return

        original_suggested_path = self._original_suggested_zoom["path"]
        selected_zoom = self._viewed_zoom_candidate
        distance = self._distance_from_suggested(
            original_suggested_path,
            selected_zoom["path"],
        )
        difference = (
            selected_zoom["time_seconds"] - self._target_time_seconds
        )
        dialog = tk.Toplevel(self.root)
        dialog.title("Cambiar Zoom del SET")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        content = ttk.Frame(dialog, padding=24)
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text="Cambiar Zoom del SET",
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 18))
        details = (
            ("Zoom sugerida originalmente", self._original_suggested_zoom["name"]),
            ("Zoom seleccionada", selected_zoom["name"]),
            ("Diferencia respecto del horario Excel", f"{difference:+d} segundos"),
            (
                "Distancia estimada respecto de la Zoom sugerida",
                "No disponible"
                if distance is None
                else f"{distance:.1f} m".replace(".", ","),
            ),
            ("Secuencia seleccionada", selected_zoom["sequence"]),
        )
        for row, (label, value) in enumerate(details, start=1):
            ttk.Label(content, text=f"{label}:").grid(
                row=row, column=0, sticky="nw", padx=(0, 18), pady=4
            )
            ttk.Label(content, text=str(value), wraplength=360).grid(
                row=row, column=1, sticky="nw", pady=4
            )

        ttk.Label(content, text="Motivo del cambio:").grid(
            row=6, column=0, sticky="w", padx=(0, 18), pady=(14, 4)
        )
        reason = tk.StringVar(value="Sin especificar")
        ttk.Combobox(
            content,
            textvariable=reason,
            values=(
                "Sin especificar",
                "Desenfoque",
                "Mejor encuadre",
                "Objeto oculto",
                "Imagen incorrecta",
                "Otra",
            ),
            state="readonly",
        ).grid(row=6, column=1, sticky="ew", pady=(14, 4))

        buttons = ttk.Frame(content)
        buttons.grid(row=7, column=0, columnspan=2, pady=(20, 0))
        ttk.Button(
            buttons,
            text="Cancelar",
            command=dialog.destroy,
        ).pack(side="left", padx=(0, 8))

        def confirm_change() -> None:
            selected_reason = reason.get()
            dialog.destroy()
            self._confirm_viewed_zoom(selected_reason, distance)

        ttk.Button(
            buttons,
            text="Confirmar cambio",
            command=confirm_change,
        ).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self._center_dialog(dialog)
        dialog.grab_set()
        dialog.wait_window()

    def _confirm_viewed_zoom(
        self,
        override_reason: str,
        override_distance_m: float | None,
    ) -> None:
        if (
            self._suggested_zoom is None
            or self._viewed_zoom_candidate is None
        ):
            return

        result = self.review_items[self.review_index]
        identity = self._set_identity(result)
        selected_zoom = self._viewed_zoom_candidate
        previous_result = dict(result)
        previous_sets = self.set_results
        previous_set_ids = dict(self.set_id_map)
        previous_next_set_id = self.next_set_id
        target_seconds = (
            self._suggested_zoom["time_seconds"]
            - result["time_difference"]
        )
        original_suggested = self._original_suggested_zoom
        result.update(
            {
                "original_suggested_zoom": original_suggested,
                "finally_used_zoom": selected_zoom,
                "zoom": selected_zoom,
                "zoom_time": selected_zoom["time"],
                "time_difference": (
                    selected_zoom["time_seconds"] - target_seconds
                ),
                "status": "ZOOM_ACCEPTED",
                "validation": "MANUAL",
                "approved": True,
                "override_reason": override_reason,
                "override_distance_m": override_distance_m,
            }
        )
        try:
            self._refresh_summary()
        except Exception:
            result.clear()
            result.update(previous_result)
            self.set_results = previous_sets
            self.set_id_map = previous_set_ids
            self.next_set_id = previous_next_set_id
            self._refresh_set_list()
            self._show_current_review()
            return

        self.output_plan = None
        self.output_plan_summary.set("")
        self.output_is_stale = self._previous_output_exists()
        if self.output_is_stale:
            self.output_plan_summary.set(
                "La salida generada anteriormente ya no corresponde al "
                "estado actual del proyecto.\n\n"
                "Retire o renombre la carpeta y el Excel anteriores antes "
                "de generar nuevamente."
            )
        self._suggested_zoom = selected_zoom
        self._viewed_zoom_candidate = selected_zoom
        self._zoom_neighbor_items = self._zoom_neighbors(selected_zoom)
        self._refresh_review_list(preferred_identity=identity)

    def _clear_zoom_alternatives(self) -> None:
        for child in self.zoom_alternatives.winfo_children():
            child.destroy()
        self._zoom_thumbnail_photos = []

    def _apply_zoom_alternatives_theme(self) -> None:
        if not hasattr(self, "zoom_alternatives"):
            return
        suggested_path = (
            str(self._suggested_zoom["path"])
            if self._suggested_zoom is not None
            else ""
        )
        viewed_path = (
            str(self._viewed_zoom_candidate["path"])
            if self._viewed_zoom_candidate is not None
            else ""
        )
        for card, (_, candidate) in zip(
            self.zoom_alternatives.winfo_children(),
            self._zoom_neighbor_items,
        ):
            is_suggested = str(candidate["path"]) == suggested_path
            is_viewed = str(candidate["path"]) == viewed_path
            background = self._theme[
                "suggested" if is_suggested else "alternative"
            ]
            border = self._theme[
                "viewed_border"
                if is_viewed
                else "suggested_border" if is_suggested else "border"
            ]
            card.configure(background=background, highlightbackground=border)
            for child in card.winfo_children():
                child.configure(
                    background=background,
                    foreground=self._theme["text"],
                )

    def _render_zoom_alternatives(self) -> None:
        self._clear_zoom_alternatives()
        if self._suggested_zoom is None:
            return
        suggested_path = str(self._suggested_zoom["path"])
        original_suggested_path = str(
            self._original_suggested_zoom["path"]
        )
        viewed_path = str(self._viewed_zoom_candidate["path"])
        for column, (position, candidate) in enumerate(
            self._zoom_neighbor_items
        ):
            is_suggested = str(candidate["path"]) == suggested_path
            is_viewed = str(candidate["path"]) == viewed_path
            background = self._theme[
                "suggested" if is_suggested else "alternative"
            ]
            border = self._theme["viewed_border"] if is_viewed else (
                self._theme["suggested_border"]
                if is_suggested
                else self._theme["border"]
            )
            card = tk.Frame(
                self.zoom_alternatives,
                background=background,
                highlightbackground=border,
                highlightthickness=3 if is_viewed else 1,
                cursor="hand2",
            )
            card.grid(row=0, column=column, padx=2, sticky="nsew")
            self.zoom_alternatives.columnconfigure(column, weight=1)
            thumbnail = tk.Label(
                card,
                text="",
                background=background,
                foreground=self._theme["text"],
                anchor="center",
            )
            thumbnail.pack(fill="x", padx=2, pady=(2, 1))
            try:
                with Image.open(candidate["path"]) as source:
                    preview = ImageOps.exif_transpose(source).copy()
                    preview.thumbnail((96, 62), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(preview)
                self._zoom_thumbnail_photos.append(photo)
                thumbnail.configure(image=photo)
            except (OSError, ValueError, tk.TclError):
                thumbnail.configure(text="No disponible")
            seconds = candidate["time_seconds"] - self._target_time_seconds
            seconds_text = "0 s" if seconds == 0 else f"{seconds:+d} s"
            distance = self._distance_from_suggested(
                original_suggested_path,
                candidate["path"],
            )
            distance_text = (
                "GPS no disponible"
                if distance is None
                else f"{distance:.1f} m".replace(".", ",")
            )
            caption = tk.Label(
                card,
                text=f"{position}\n{seconds_text}\n{distance_text}",
                background=background,
                foreground=self._theme["text"],
                justify="center",
                font=("Segoe UI", 8, "bold" if is_suggested else "normal"),
            )
            caption.pack(fill="x", padx=2, pady=(1, 2))
            select = lambda _event, item=candidate: (
                self._schedule_alternative_selection(item)
            )
            open_fullscreen = lambda _event, item=candidate: (
                self._open_alternative_fullscreen(item)
            )
            for widget in (card, thumbnail, caption):
                widget.bind("<Button-1>", select)
                widget.bind("<Double-Button-1>", open_fullscreen)

    def _schedule_alternative_selection(
        self,
        candidate: dict[str, object],
    ) -> None:
        if self._alternative_click_job is not None:
            self.root.after_cancel(self._alternative_click_job)
        self._alternative_click_job = self.root.after(
            200,
            lambda: self._select_alternative(candidate),
        )

    def _select_alternative(self, candidate: dict[str, object]) -> None:
        self._alternative_click_job = None
        self._show_review_candidate(candidate)

    def _open_alternative_fullscreen(
        self,
        candidate: dict[str, object],
    ) -> None:
        if self._alternative_click_job is not None:
            self.root.after_cancel(self._alternative_click_job)
            self._alternative_click_job = None
        self.zoom_viewer.reset_view()
        self._show_review_candidate(candidate)
        self.zoom_viewer.show_fullscreen()

    def _load_review_preview(self) -> None:
        if self._review_preview_rendering:
            return
        self._review_preview_rendering = True
        previous_transition_state = self._review_preview_transitioning
        self._review_preview_transitioning = True
        previous_photo = self._preview_photo
        try:
            area_width = self.zoom_viewer.winfo_width()
            area_height = self.zoom_viewer.winfo_height()
            width = area_width - 8 if area_width > 20 else 352
            height = area_height - 8 if area_height > 20 else 252
            if self.zoom_viewer.source_path != self._review_source_path:
                self.zoom_viewer.source_path = self._review_source_path
                self.zoom_viewer.reset_view()
            try:
                with Image.open(self._review_source_path) as source:
                    original = ImageOps.exif_transpose(source).copy()
                    fit_width, fit_height = original.size
                    fit_scale = min(width / fit_width, height / fit_height)
                    fit_size = (
                        max(round(fit_width * fit_scale), 1),
                        max(round(fit_height * fit_scale), 1),
                    )
                    self.zoom_viewer.set_content_size(*fit_size)
                    if self.zoom_viewer.zoom_factor == 1.0:
                        self.zoom_viewer.offset_x = 0.0
                        self.zoom_viewer.offset_y = 0.0
                        preview = original.resize(
                            fit_size,
                            Image.Resampling.LANCZOS,
                        )
                    else:
                        scaled_size = (
                            max(
                                round(
                                    fit_size[0] * self.zoom_viewer.zoom_factor
                                ),
                                1,
                            ),
                            max(
                                round(
                                    fit_size[1] * self.zoom_viewer.zoom_factor
                                ),
                                1,
                            ),
                        )
                        scaled = original.resize(
                            scaled_size,
                            Image.Resampling.LANCZOS,
                        )
                        crop_width = min(width, scaled_size[0])
                        crop_height = min(height, scaled_size[1])
                        left = round(
                            (scaled_size[0] - crop_width) / 2
                            - self.zoom_viewer.offset_x
                        )
                        top = round(
                            (scaled_size[1] - crop_height) / 2
                            - self.zoom_viewer.offset_y
                        )
                        left = min(max(left, 0), scaled_size[0] - crop_width)
                        top = min(max(top, 0), scaled_size[1] - crop_height)
                        preview = scaled.crop(
                            (left, top, left + crop_width, top + crop_height)
                        )
                    new_photo = ImageTk.PhotoImage(preview)
                self.preview_label.configure(
                    image=new_photo,
                    text="",
                )
                self._preview_photo = new_photo
            except (OSError, ValueError, tk.TclError):
                self.preview_label.configure(
                    image="",
                    text="No fue posible cargar la imagen Zoom.",
                )
        finally:
            self._review_preview_transitioning = previous_transition_state
            self._review_preview_rendering = False
            if previous_photo is not self._preview_photo:
                self._defer_preview_photo_release(previous_photo)

    def _defer_preview_photo_release(
        self,
        previous_photo: ImageTk.PhotoImage | None,
    ) -> None:
        if previous_photo is None:
            return

        def release() -> None:
            nonlocal previous_photo
            previous_transition_state = self._review_preview_transitioning
            self._review_preview_transitioning = True
            try:
                previous_photo = None
            finally:
                self._review_preview_transitioning = previous_transition_state

        self.root.after_idle(release)

    def _on_review_preview_resize(self, _event: object = None) -> None:
        if self._review_preview_transitioning:
            return
        if hasattr(self, "_review_source_path") and self.review_items:
            self._load_review_preview()

    def _open_fullscreen_review(self) -> None:
        if not self.review_items or not hasattr(self, "_review_source_path"):
            return
        self.zoom_viewer.show_fullscreen()

    def _on_fullscreen_resize(self, _event: object = None) -> None:
        self._on_review_preview_resize()

    def _refresh_fullscreen_preview(self) -> None:
        if self.zoom_viewer.fullscreen:
            self._load_review_preview()

    def _close_fullscreen_review(self) -> None:
        self.zoom_viewer.show_embedded()

    def _on_review_selected(self, _event: object = None) -> None:
        selection = self.review_list.curselection()
        if not selection:
            return
        self.review_index = selection[0]
        self._show_current_review()

    def _on_review_mode_changed(self, _event: object = None) -> None:
        self.review_index = 0
        self._refresh_review_list(select_first=True)

    def _all_review_items(self) -> list[dict[str, object]]:
        active_sets = {
            self._set_identity(result) for result in self.set_results
        }
        manual_items = {id(result) for result in self.pending_reviews}
        return [
            result
            for result in self.zoom_results
            if self._set_identity(result) in active_sets
            or id(result) in manual_items
        ]

    def _refresh_review_list(
        self,
        preferred_identity: tuple[object, str] | None = None,
        select_first: bool = False,
    ) -> None:
        self.review_items = (
            list(self.pending_reviews)
            if self.review_mode.get() == "Revisar pendientes"
            else self._all_review_items()
        )
        selected_index = 0 if select_first else min(
            self.review_index,
            max(len(self.review_items) - 1, 0),
        )
        if preferred_identity is not None:
            selected_index = next(
                (
                    index
                    for index, result in enumerate(self.review_items)
                    if self._set_identity(result) == preferred_identity
                ),
                selected_index,
            )
        visible_status = {
            "ZOOM_PENDING_REVIEW": "Pendiente",
            "ZOOM_ACCEPTED": "Aceptada",
            "ZOOM_REJECTED": "Rechazada",
        }
        sets_by_identity = {
            self._set_identity(result): result for result in self.set_results
        }
        self.review_list.delete(0, "end")
        for result in self.review_items:
            set_result = sets_by_identity.get(self._set_identity(result))
            status = visible_status.get(result["status"])
            if status is None and set_result is not None:
                status = (
                    "SET completo"
                    if set_result["status"] == "SET_COMPLETE"
                    else "SET incompleto"
                )
            self.review_list.insert(
                "end",
                f"Fila {result['row_number']}   "
                f"{result['time_difference']:+d} s   "
                f"{status}",
            )

        pending_count = sum(
            result["status"] == "ZOOM_PENDING_REVIEW"
            for result in self.pending_reviews
        )
        reviewed_count = len(self.pending_reviews) - pending_count
        if not self.pending_reviews:
            message = "Sin revisiones de Zoom necesarias"
        elif pending_count == 0:
            message = (
                "Revisión de Zoom completada\n"
                f"Pendientes: 0\n"
                f"Revisadas: {reviewed_count} de {len(self.pending_reviews)}"
            )
        else:
            message = (
                f"Pendientes: {pending_count}\n"
                f"Revisadas: {reviewed_count} de {len(self.pending_reviews)}"
            )
        self.review_progress.configure(text=message)
        if self.review_items:
            self.review_index = selected_index
            self.review_list.selection_set(selected_index)
            self.review_list.activate(selected_index)
        self._show_current_review()

    def _decide_current_zoom(self, approved: bool) -> None:
        selection = self.review_list.curselection()
        if not selection:
            return
        self.review_index = selection[0]
        result = self.review_items[self.review_index]
        current_identity = self._set_identity(result)
        pending_before = sum(
            item["status"] == "ZOOM_PENDING_REVIEW"
            for item in self.zoom_results
        )
        decide_zoom(result, approved)
        pending_after = any(
            item["status"] == "ZOOM_PENDING_REVIEW"
            for item in self.zoom_results
        )
        self._refresh_summary()
        preferred_identity = current_identity
        if self.review_mode.get() == "Revisar pendientes":
            pending_items_after = [
                item
                for item in self.pending_reviews
                if item["status"] == "ZOOM_PENDING_REVIEW"
            ]
            if pending_items_after:
                current_position = self.pending_reviews.index(result)
                next_pending = next(
                    (
                        item
                        for item in self.pending_reviews[current_position + 1 :]
                        if item["status"] == "ZOOM_PENDING_REVIEW"
                    ),
                    pending_items_after[0],
                )
                preferred_identity = self._set_identity(next_pending)
        self._refresh_review_list(preferred_identity=preferred_identity)
        if pending_before > 0 and not pending_after:
            messagebox.showinfo(
                "Revisión completada",
                "No quedan SET pendientes de revisión.\n\n"
                "Puede revisar nuevamente cualquier SET desde “Ver todos "
                "los SET” o continuar con “Finalizar y generar”.",
                parent=self.root,
            )

    def _refresh_summary(self) -> None:
        self.set_results = build_sets(self.zoom_results, self._parsed_images)
        self._assign_set_ids()
        statuses = [result["status"] for result in self.zoom_results]
        complete_sets = [
            result
            for result in self.set_results
            if result["status"] == "SET_COMPLETE"
        ]
        incomplete_sets = [
            result
            for result in self.set_results
            if result["status"] == "SET_INCOMPLETE"
        ]
        set_blocking_statuses = {
            "ZOOM_REJECTED",
            "ZOOM_NOT_FOUND",
            "ZOOM_AMBIGUOUS",
            "INVALID_REFERENCE",
        }
        summary_lines = [
            "Proyecto válido",
            "Excel leído correctamente",
            f"Registros con referencia: {len(self._records)}",
            f"Imágenes encontradas en Materiales: {len(self._images)}",
            "Nombres duplicados: 0",
            f"Nombres DJI reconocidos: {self._recognized_count}",
            f"Nombres DJI no reconocidos: {len(self._unrecognized)}",
            "",
            f"Registros Excel: {len(self._records)}",
            f"Zoom exactas: {statuses.count('ZOOM_EXACT')}",
            f"Zoom pendientes de revisión: {statuses.count('ZOOM_PENDING_REVIEW')}",
            f"Zoom aceptadas manualmente: {statuses.count('ZOOM_ACCEPTED')}",
            f"Zoom rechazadas: {statuses.count('ZOOM_REJECTED')}",
            f"Zoom no encontradas: {statuses.count('ZOOM_NOT_FOUND')}",
            f"Zoom ambiguas: {statuses.count('ZOOM_AMBIGUOUS')}",
            f"Referencias no interpretables: {statuses.count('INVALID_REFERENCE')}",
            "",
            f"SET completos: {len(complete_sets)}",
            f"SET incompletos: {len(incomplete_sets)}",
            "SET no creados por Zoom pendiente: "
            f"{statuses.count('ZOOM_PENDING_REVIEW')}",
            "SET no creados por Zoom rechazada/no encontrada/ambigua/inválida: "
            f"{sum(status in set_blocking_statuses for status in statuses)}",
        ]
        if self._unrecognized:
            summary_lines.extend(
                [
                    "",
                    "Archivos no reconocidos:",
                    "",
                    *(image["name"] for image in self._unrecognized),
                ]
            )
        problem_sections = (
            ("ZOOM_NOT_FOUND", "Zoom no encontradas:"),
            ("ZOOM_AMBIGUOUS", "Zoom ambiguas:"),
            ("INVALID_REFERENCE", "Referencias no interpretables:"),
        )
        for status, heading in problem_sections:
            problems = [
                result
                for result in self.zoom_results
                if result["status"] == status
            ]
            if not problems:
                continue
            summary_lines.extend(["", heading])
            for result in problems:
                summary_lines.append(
                    f"Fila {result['row_number']} — {result['file_reference']}"
                )
                if status == "ZOOM_AMBIGUOUS":
                    summary_lines.extend(
                        f"- {candidate['name']}"
                        for candidate in result["candidates"]
                    )
        if incomplete_sets:
            readable_channels = {"W": "Wide", "T": "Thermal"}
            summary_lines.extend(["", "SET incompletos:"])
            for result in incomplete_sets:
                missing = [
                    readable_channels[channel]
                    for channel in result["missing_channels"]
                ]
                label = "Falta" if len(missing) == 1 else "Faltan"
                summary_lines.extend(
                    [
                        "",
                        f"Fila {result['row_number']} — {result['file_reference']}",
                        f"{label}: {', '.join(missing)}",
                    ]
                )
        self._refresh_set_list()
        self._update_finalize_state()
        self.status.set("\n".join(summary_lines))

    def _update_finalize_state(self) -> None:
        pending = any(
            result["status"] == "ZOOM_PENDING_REVIEW"
            for result in self.zoom_results
        )
        state = "normal" if self.review_active and not pending else "disabled"
        self.finalize_button.configure(state=state)

    def _previous_output_exists(self) -> bool:
        if self.current_root is None:
            return False
        root_name = self.current_root.name
        output_folder = self.current_root / f"Anomalias One {root_name}"
        traceability_file = (
            self.current_root / f"Trazabilidad MAAT One {root_name}.xlsx"
        )
        return output_folder.exists() or traceability_file.exists()

    def _finalize_output(self) -> None:
        self._update_finalize_state()
        if self.finalize_button.cget("state") == "disabled":
            self.output_plan_summary.set(
                "Existen revisiones de Zoom pendientes."
            )
            return
        if self.output_is_stale:
            if self._previous_output_exists():
                self.output_plan_summary.set(
                    "La salida generada anteriormente ya no corresponde al "
                    "estado actual del proyecto.\n\n"
                    "Retire o renombre la carpeta y el Excel anteriores antes "
                    "de generar nuevamente."
                )
                return
            self.output_is_stale = False
        self.output_plan = build_output_plan(
            self.current_root,
            self.zoom_results,
            self.set_results,
        )
        summary = self.output_plan["summary"]
        lines = [
            "Plan de salida preparado",
            f"SET completos: {summary['complete_sets']}",
            f"SET incompletos: {summary['incomplete_sets']}",
            f"SET descartados: {summary['discarded_sets']}",
            f"Imágenes que se copiarían: {summary['images_to_copy']}",
            f"Imágenes faltantes: {summary['missing_images']}",
            f"Decisiones manuales: {summary['manual_decisions']}",
            f"Validaciones automáticas: {summary['automatic_validations']}",
            f"Destino previsto: {self.output_plan['destination_name']}",
        ]
        if self.output_plan["reason"]:
            lines.extend(["", self.output_plan["reason"]])
        output_folder = Path(self.output_plan["destination_folder"])
        try:
            output_folder.mkdir()
        except FileExistsError:
            folder_ready = output_folder.is_dir()
            folder_result = (
                "La carpeta ya existía."
                if folder_ready
                else "No fue posible crear la carpeta de salida."
            )
        except OSError:
            folder_ready = False
            folder_result = "No fue posible crear la carpeta de salida."
        else:
            folder_ready = True
            folder_result = "Carpeta creada correctamente"
        lines.extend(["", folder_result])
        self.output_plan_summary.set("\n".join(lines))
        if folder_ready:
            self._show_copy_confirmation()

    def _show_copy_confirmation(self) -> None:
        plan = self.output_plan
        summary = plan["summary"]
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirmación final")
        dialog.transient(self.root)
        dialog.resizable(False, False)

        content = ttk.Frame(dialog, padding=24)
        content.pack(fill="both", expand=True)
        details = (
            ("Nombre del proyecto", Path(plan["root_folder"]).name),
            ("Carpeta destino", plan["destination_folder"]),
            ("Cantidad de SET completos", summary["complete_sets"]),
            ("Cantidad de SET incompletos", summary["incomplete_sets"]),
            ("Cantidad de SET descartados", summary["discarded_sets"]),
            ("Cantidad total de imágenes que serán copiadas", summary["images_to_copy"]),
            ("Cantidad de imágenes faltantes", summary["missing_images"]),
            ("Cantidad de decisiones manuales", summary["manual_decisions"]),
            ("Cantidad de validaciones automáticas", summary["automatic_validations"]),
        )
        for row, (label, value) in enumerate(details):
            ttk.Label(content, text=f"{label}:").grid(
                row=row, column=0, sticky="w", padx=(0, 18), pady=4
            )
            ttk.Label(content, text=str(value)).grid(
                row=row, column=1, sticky="w", pady=4
            )

        buttons = ttk.Frame(content)
        buttons.grid(
            row=len(details), column=0, columnspan=2, pady=(20, 0)
        )
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(
            side="left", padx=(0, 8)
        )

        def begin_copy() -> None:
            dialog.destroy()
            copy_summary = copy_planned_images(self.output_plan)
            if copy_summary["source_missing"]:
                lines = [
                    "La copia no pudo completarse porque uno o más "
                    "archivos planificados ya no existen en Materiales.",
                    "Archivos fuente faltantes: "
                    f"{copy_summary['source_missing']}",
                ]
                if copy_summary["copy_errors"]:
                    lines.append(
                        f"Errores de copia: {copy_summary['copy_errors']}"
                    )
                self.output_plan_summary.set("\n".join(lines))
                return
            if copy_summary["copy_errors"]:
                self.output_plan_summary.set(
                    "La copia finalizó con errores.\n"
                    f"Errores de copia: {copy_summary['copy_errors']}\n"
                    "No se generó el Excel de trazabilidad."
                )
                return
            try:
                traceability_path = generate_traceability_excel(
                    self.output_plan
                )
            except TraceabilityError as error:
                self.output_plan_summary.set(str(error))
                return
            summary = self.output_plan["summary"]
            self.output_plan_summary.set(
                "Proceso finalizado\n"
                f"SET procesados: {copy_summary['sets_processed']}\n"
                f"Imágenes copiadas: {copy_summary['images_copied']}\n"
                f"Imágenes ya existentes: {copy_summary['images_existing']}\n"
                f"SET completos: {summary['complete_sets']}\n"
                f"SET incompletos: {summary['incomplete_sets']}\n\n"
                f"Carpeta:\n{self.output_plan['destination_name']}\n\n"
                f"Trazabilidad:\n{traceability_path.name}"
            )
            self._show_process_completed_dialog()

        ttk.Button(
            buttons,
            text="Comenzar copia",
            command=begin_copy,
        ).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self._center_dialog(dialog)
        dialog.grab_set()
        dialog.wait_window()

    def _show_process_completed_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Proceso finalizado")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(background=self._theme["background"])

        content = ttk.Frame(dialog, padding=24)
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text=(
                "El proyecto se procesó correctamente.\n\n"
                "Se generaron las imágenes de salida y el archivo de "
                "trazabilidad."
            ),
            justify="left",
        ).pack()
        buttons = ttk.Frame(content)
        buttons.pack(pady=(20, 0))

        def select_another_project() -> None:
            dialog.destroy()
            self._reset_for_new_project()
            self.root.after_idle(self._select_root_folder)

        def close_application() -> None:
            dialog.destroy()
            self.root.destroy()

        ttk.Button(
            buttons,
            text="Trabajar con otro proyecto",
            command=select_another_project,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            buttons,
            text="Cerrar MAAT One",
            command=close_application,
        ).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        self._center_dialog(dialog)
        dialog.grab_set()
        dialog.wait_window()

    def _show_processing_view(self) -> None:
        self._geographic_view.pack_forget()
        self._processing_view.pack(fill="both", expand=True)

    def _open_geographic_search(self) -> None:
        self._processing_view.pack_forget()
        self._geographic_view.pack(fill="both", expand=True)
        if self._geographic_view_built:
            return
        self._geographic_view_built = True

        self._geographic_query = tk.StringVar()
        self._geographic_latitude = tk.StringVar()
        self._geographic_longitude = tk.StringVar()
        self._geographic_radius = tk.StringVar(value="10")
        self._geographic_channels = {
            channel: tk.BooleanVar(value=True)
            for channel in ("Z", "W", "T")
        }
        self._geographic_reference_text = tk.StringVar(
            value="Busque y seleccione una fotografía de referencia."
        )
        self._geographic_summary = tk.StringVar(value="")
        self._geographic_root_text = tk.StringVar(
            value="No se ha seleccionado una carpeta de imágenes."
        )
        self._geographic_review_name = tk.StringVar(
            value="Sin imágenes para revisar"
        )
        self._geographic_review_detail = tk.StringVar(value="")
        self._geographic_name_matches = []
        self._geographic_reference = None
        self._geographic_root = None
        self._geographic_images = []
        self._geographic_gps_metadata = GPSMetadataCache()
        self._geographic_selection = GeographicSelection()
        self._geographic_preview_photo = None
        self._geographic_without_gps_count = 0

        content = ttk.Frame(self._geographic_view, padding=(24, 4, 24, 14))
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text="Buscar por ubicación",
            style="Section.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            content,
            text="Carpeta raíz de imágenes:",
        ).pack(anchor="w", pady=(2, 0))
        root_selector = ttk.Frame(content)
        root_selector.pack(fill="x", pady=(3, 10))
        ttk.Label(
            root_selector,
            textvariable=self._geographic_root_text,
            wraplength=790,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            root_selector,
            text="Seleccionar carpeta",
            command=self._select_geographic_root_folder,
        ).pack(side="right", padx=(10, 0))

        self._geographic_notebook = ttk.Notebook(content)
        self._geographic_notebook.pack(fill="x")
        name_tab = ttk.Frame(self._geographic_notebook, padding=10)
        coordinates_tab = ttk.Frame(self._geographic_notebook, padding=10)
        self._geographic_notebook.add(name_tab, text="Buscar por nombre")
        self._geographic_notebook.add(
            coordinates_tab,
            text="Buscar por coordenadas",
        )
        self._geographic_notebook.tab(0, state="disabled")
        self._geographic_notebook.tab(1, state="disabled")

        name_tab.columnconfigure(0, weight=1)
        ttk.Label(
            name_tab,
            text="Nombre completo o parcial:",
        ).grid(row=0, column=0, sticky="w")
        name_entry = ttk.Entry(
            name_tab,
            textvariable=self._geographic_query,
        )
        name_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        name_entry.bind(
            "<Return>",
            lambda _event: self._search_geographic_names(),
        )
        self._geographic_name_search_button = ttk.Button(
            name_tab,
            text="Buscar coincidencias",
            command=self._search_geographic_names,
            state="disabled",
        )
        self._geographic_name_search_button.grid(row=1, column=1)
        matches_frame = ttk.Frame(name_tab)
        matches_frame.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
        )
        self._geographic_matches_text = tk.Text(
            matches_frame,
            height=5,
            font=("Segoe UI", 9),
            wrap="none",
            cursor="hand2",
            state="disabled",
        )
        self._geographic_matches_text.tag_configure(
            "Match.Highlight",
            background="#FDE047",
            foreground="#111827",
        )
        matches_scrollbar = ttk.Scrollbar(
            matches_frame,
            orient="vertical",
            command=self._geographic_matches_text.yview,
        )
        self._geographic_matches_text.configure(
            yscrollcommand=matches_scrollbar.set
        )
        self._geographic_matches_text.pack(
            side="left", fill="both", expand=True
        )
        matches_scrollbar.pack(side="right", fill="y")
        self._geographic_matches_text.bind(
            "<ButtonRelease-1>",
            self._select_geographic_reference,
        )
        ttk.Label(
            name_tab,
            textvariable=self._geographic_reference_text,
            justify="left",
        ).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )

        coordinates_tab.columnconfigure(1, weight=1)
        ttk.Label(coordinates_tab, text="Latitud:").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=3
        )
        ttk.Entry(
            coordinates_tab,
            textvariable=self._geographic_latitude,
        ).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(coordinates_tab, text="Longitud:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=3
        )
        ttk.Entry(
            coordinates_tab,
            textvariable=self._geographic_longitude,
        ).grid(row=1, column=1, sticky="ew", pady=3)

        controls = ttk.Frame(content)
        controls.pack(fill="x", pady=(10, 8))
        ttk.Label(controls, text="Radio (m):").pack(side="left")
        ttk.Entry(
            controls,
            textvariable=self._geographic_radius,
            width=12,
        ).pack(side="left", padx=(6, 12))
        ttk.Label(controls, text="Canales:").pack(side="left")
        for channel in ("Z", "W", "T"):
            ttk.Checkbutton(
                controls,
                text=channel,
                variable=self._geographic_channels[channel],
            ).pack(side="left", padx=(5, 0))
        self._geographic_search_execute_button = ttk.Button(
            controls,
            text="Buscar imágenes cercanas",
            command=self._run_geographic_search,
            state="disabled",
        )
        self._geographic_search_execute_button.pack(side="left")

        ttk.Label(
            content,
            textvariable=self._geographic_summary,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        review_area = ttk.Panedwindow(content, orient="horizontal")
        review_area.pack(fill="both", expand=True)
        results_frame = ttk.LabelFrame(
            review_area,
            text="Resultados seleccionados",
            padding=6,
        )
        preview_panel = ttk.LabelFrame(
            review_area,
            text="Revisión visual",
            padding=8,
        )
        review_area.add(results_frame, weight=2)
        review_area.add(preview_panel, weight=3)
        self._geographic_results = ttk.Treeview(
            results_frame,
            columns=("name", "distance"),
            show="headings",
            selectmode="browse",
        )
        self._geographic_results.heading("name", text="Nombre")
        self._geographic_results.heading(
            "distance", text="Distancia (m)"
        )
        self._geographic_results.column("name", width=330, anchor="w")
        self._geographic_results.column(
            "distance", width=130, anchor="e"
        )
        results_scrollbar = ttk.Scrollbar(
            results_frame,
            orient="vertical",
            command=self._geographic_results.yview,
        )
        self._geographic_results.configure(
            yscrollcommand=results_scrollbar.set
        )
        self._geographic_results.pack(
            side="left", fill="both", expand=True
        )
        results_scrollbar.pack(side="right", fill="y")
        self._geographic_results.bind(
            "<<TreeviewSelect>>",
            self._select_geographic_result,
        )

        ttk.Label(
            preview_panel,
            textvariable=self._geographic_review_name,
            font=("Segoe UI", 10, "bold"),
            wraplength=520,
        ).pack(anchor="w")
        ttk.Label(
            preview_panel,
            textvariable=self._geographic_review_detail,
            justify="left",
        ).pack(anchor="w", pady=(3, 6))
        self._geographic_preview_area = ttk.Frame(preview_panel)
        self._geographic_preview_area.pack(fill="both", expand=True)
        self._geographic_viewer = ZoomViewer(
            self.root,
            self._geographic_preview_area,
            self._load_geographic_preview,
        )
        navigation = ttk.Frame(preview_panel)
        navigation.pack(fill="x", pady=(8, 0))
        self._geographic_previous_button = ttk.Button(
            navigation,
            text="Anterior",
            command=self._previous_geographic_image,
            state="disabled",
        )
        self._geographic_previous_button.pack(side="left")
        self._geographic_next_button = ttk.Button(
            navigation,
            text="Siguiente",
            command=self._next_geographic_image,
            state="disabled",
        )
        self._geographic_next_button.pack(side="left", padx=(6, 0))
        self._geographic_remove_button = ttk.Button(
            navigation,
            text="Quitar de la selección",
            command=self._remove_geographic_image,
            state="disabled",
        )
        self._geographic_remove_button.pack(side="left", padx=(14, 0))
        self._geographic_restore_button = ttk.Button(
            navigation,
            text="Restaurar selección",
            command=self._restore_geographic_selection,
            state="disabled",
        )
        self._geographic_restore_button.pack(side="right")
        self._geographic_save_button = ttk.Button(
            navigation,
            text="Guardar selección",
            command=self._save_geographic_selection,
            state="disabled",
        )
        self._geographic_save_button.pack(side="right", padx=(0, 8))
        name_entry.focus_set()

    def _select_geographic_root_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Seleccionar carpeta raíz de imágenes",
        )
        if not selected:
            return
        root_folder = Path(selected).resolve()
        try:
            images = build_image_inventory(root_folder)
        except OSError:
            messagebox.showerror(
                "Carpeta no disponible",
                "No fue posible inventariar las fotografías de la carpeta.",
                parent=self.root,
            )
            return

        self._geographic_root = root_folder
        self._geographic_images = images
        self._geographic_gps_metadata = GPSMetadataCache()
        self._geographic_name_matches = []
        self._geographic_reference = None
        self._geographic_query.set("")
        self._geographic_latitude.set("")
        self._geographic_longitude.set("")
        self._geographic_matches_text.configure(state="normal")
        self._geographic_matches_text.delete("1.0", "end")
        self._geographic_matches_text.configure(state="disabled")
        self._geographic_reference_text.set(
            "Busque y seleccione una fotografía de referencia."
        )
        self._geographic_selection.replace([])
        self._geographic_without_gps_count = 0
        self._geographic_viewer.reset_view()
        self._refresh_geographic_selection()
        self._geographic_root_text.set(
            f"{root_folder} — {len(images)} imágenes JPG encontradas"
        )
        state = "normal" if images else "disabled"
        self._geographic_notebook.tab(0, state=state)
        self._geographic_notebook.tab(1, state=state)
        self._geographic_name_search_button.configure(state=state)
        self._geographic_search_execute_button.configure(state=state)
        if images:
            self._geographic_notebook.select(0)
        else:
            self._geographic_summary.set(
                "La carpeta no contiene imágenes JPG.\n"
                "Encontradas: 0 | Seleccionadas: 0 | Sin GPS válido: 0"
            )

    def _search_geographic_names(self) -> None:
        try:
            search = find_images_by_name(
                self._geographic_images,
                self._geographic_query.get(),
            )
        except GeographicSearchError as error:
            self._geographic_name_matches = []
            self._geographic_reference = None
            self._geographic_matches_text.configure(state="normal")
            self._geographic_matches_text.delete("1.0", "end")
            self._geographic_matches_text.configure(state="disabled")
            self._geographic_reference_text.set(str(error))
            return

        parsed_matches = parse_dji_images(search["matches"])
        channel_order = {"W": 0, "Z": 1, "T": 2}
        self._geographic_name_matches = [
            image
            for _parsed, image in sorted(
                zip(parsed_matches, search["matches"]),
                key=lambda item: (
                    0,
                    item[0]["date"],
                    item[0]["time"],
                    item[0]["sequence"],
                    channel_order[item[0]["channel"]],
                )
                if item[0]["recognized"]
                else (1, str(item[0]["name"]).casefold(), "", "", 0),
            )
        ]
        self._geographic_reference = None
        self._geographic_matches_text.configure(state="normal")
        self._geographic_matches_text.delete("1.0", "end")
        query = self._geographic_query.get().strip()
        normalized_query = query.casefold()
        for line, image in enumerate(self._geographic_name_matches, start=1):
            name = image["name"]
            self._geographic_matches_text.insert("end", name + "\n")
            normalized_name = name.casefold()
            start = 0
            while normalized_query:
                match_at = normalized_name.find(normalized_query, start)
                if match_at < 0:
                    break
                self._geographic_matches_text.tag_add(
                    "Match.Highlight",
                    f"{line}.{match_at}",
                    f"{line}.{match_at + len(query)}",
                )
                start = match_at + len(query)
        self._geographic_matches_text.tag_raise("Match.Highlight")
        self._geographic_matches_text.configure(state="disabled")

        match_count = len(self._geographic_name_matches)
        if match_count == 0:
            self._geographic_reference_text.set(
                "No se encontraron fotografías con ese nombre."
            )
        elif match_count == 1:
            self._geographic_matches_text.configure(state="normal")
            self._geographic_matches_text.tag_add(
                "Selected.Reference", "1.0", "1.end"
            )
            self._geographic_matches_text.tag_raise("Match.Highlight")
            self._geographic_matches_text.configure(state="disabled")
            self._geographic_reference = self._geographic_name_matches[0]
            self._geographic_reference_text.set(
                "Referencia: " + self._geographic_reference["name"]
            )
        else:
            self._geographic_reference_text.set(
                f"{match_count} coincidencias. Seleccione una referencia."
            )

    def _select_geographic_reference(self, event: tk.Event) -> None:
        line = int(
            self._geographic_matches_text.index(
                f"@{event.x},{event.y}"
            ).split(".", 1)[0]
        )
        index = line - 1
        if not 0 <= index < len(self._geographic_name_matches):
            return
        self._geographic_reference = self._geographic_name_matches[index]
        self._geographic_matches_text.configure(state="normal")
        self._geographic_matches_text.tag_remove(
            "Selected.Reference", "1.0", "end"
        )
        self._geographic_matches_text.tag_add(
            "Selected.Reference", f"{line}.0", f"{line}.end"
        )
        self._geographic_matches_text.tag_raise("Match.Highlight")
        self._geographic_matches_text.configure(state="disabled")
        self._geographic_reference_text.set(
            "Referencia: " + self._geographic_reference["name"]
        )

    def _run_geographic_search(self) -> None:
        try:
            selected_channels = {
                channel
                for channel, selected in self._geographic_channels.items()
                if selected.get()
            }
            if not selected_channels:
                raise GeographicSearchError(
                    "Seleccione al menos un canal: Z, W o T."
                )
            filtered_images = [
                image
                for image in parse_dji_images(self._geographic_images)
                if image.get("recognized")
                and image.get("channel") in selected_channels
            ]
            if self._geographic_notebook.index("current") == 0:
                if self._geographic_reference is None:
                    raise GeographicSearchError(
                        "Seleccione una fotografía de referencia."
                    )
                search = search_by_reference_image(
                    filtered_images,
                    self._geographic_reference,
                    self._geographic_radius.get(),
                    self._geographic_gps_metadata,
                )
            else:
                requested_latitude, requested_longitude = (
                    validate_coordinate_input(
                        self._geographic_latitude.get(),
                        self._geographic_longitude.get(),
                    )
                )
                nearest = find_nearest_gps_image(
                    self._geographic_images,
                    requested_latitude,
                    requested_longitude,
                    self._geographic_gps_metadata,
                )
                if nearest["distance_m"] > 1.0:
                    approved = messagebox.askyesno(
                        "Confirmar fotografía de referencia",
                        (
                            "No se encontró una fotografía dentro de la "
                            "tolerancia de 1 m.\n\n"
                            f"Fotografía más próxima:\n{nearest['name']}\n\n"
                            "Distancia aproximada: "
                            f"{nearest['distance_m']:.3f} m\n\n"
                            "Coordenadas introducidas:\n"
                            f"{requested_latitude}, {requested_longitude}\n\n"
                            "Coordenadas GPS propuestas:\n"
                            f"{nearest['latitude']}, {nearest['longitude']}\n\n"
                            "¿Desea utilizar esta fotografía como referencia?"
                        ),
                        parent=self.root,
                    )
                    if not approved:
                        self._show_geographic_results(
                            None,
                            "Búsqueda por coordenadas cancelada.",
                        )
                        return
                self._geographic_latitude.set(
                    f"{nearest['latitude']:.9f}"
                )
                self._geographic_longitude.set(
                    f"{nearest['longitude']:.9f}"
                )
                search = search_by_coordinates(
                    filtered_images,
                    nearest["latitude"],
                    nearest["longitude"],
                    self._geographic_radius.get(),
                    self._geographic_gps_metadata,
                )
        except GeographicSearchError as error:
            self._show_geographic_results(None, str(error))
            return
        self._show_geographic_results(search)

    def _show_geographic_results(
        self,
        search: dict[str, object] | None,
        error: str | None = None,
    ) -> None:
        self._geographic_results.delete(
            *self._geographic_results.get_children()
        )
        if search is None:
            self._geographic_selection.replace([])
            self._geographic_without_gps_count = 0
            self._geographic_viewer.reset_view()
            self._refresh_geographic_selection()
            self._geographic_summary.set(
                (error or "Búsqueda no disponible.")
                + "\nEncontradas: 0 | Seleccionadas: 0 | "
                "Sin GPS válido: 0"
            )
            return
        parsed_results = parse_dji_images(
            [
                {"name": result["name"], "path": result["path"]}
                for result in search["results"]
            ]
        )
        channel_order = {"W": 0, "Z": 1, "T": 2}
        ordered_results = [
            result
            for _parsed, result in sorted(
                zip(parsed_results, search["results"]),
                key=lambda item: (
                    0,
                    item[0]["date"],
                    item[0]["time"],
                    item[0]["sequence"],
                    channel_order[item[0]["channel"]],
                )
                if item[0]["recognized"]
                else (
                    1,
                    str(item[0]["name"]).casefold(),
                    str(item[0]["path"]).casefold(),
                    "",
                    0,
                ),
            )
        ]
        self._geographic_selection.replace(ordered_results)
        self._geographic_without_gps_count = len(search["without_gps"])
        self._geographic_viewer.reset_view()
        self._refresh_geographic_selection()

    def _refresh_geographic_selection(self) -> None:
        self._geographic_results.delete(
            *self._geographic_results.get_children()
        )
        for index, image in enumerate(
            self._geographic_selection.selected_results
        ):
            self._geographic_results.insert(
                "",
                "end",
                iid=str(index),
                values=(image["name"], f"{image['distance_m']:.3f}"),
            )
        summary = (
            f"Encontradas: {self._geographic_selection.original_count} | "
            f"Seleccionadas: {self._geographic_selection.selected_count} | "
            "Sin GPS válido: "
            f"{self._geographic_without_gps_count}"
        )
        self._geographic_summary.set(summary)
        if self._geographic_selection.current is not None:
            current_id = str(self._geographic_selection.index)
            self._geographic_results.selection_set(current_id)
            self._geographic_results.focus(current_id)
            self._geographic_results.see(current_id)
        self._show_current_geographic_image()

    def _select_geographic_result(self, _event: object = None) -> None:
        selection = self._geographic_results.selection()
        if not selection:
            return
        index = int(selection[0])
        if index == self._geographic_selection.index:
            return
        self._geographic_selection.index = index
        self._geographic_viewer.reset_view()
        self._show_current_geographic_image()

    def _show_current_geographic_image(self) -> None:
        current = self._geographic_selection.current
        count = self._geographic_selection.selected_count
        if current is None:
            self._geographic_review_name.set("Sin imágenes seleccionadas")
            self._geographic_review_detail.set(
                "Use Restaurar selección para recuperar los resultados."
            )
            self._geographic_viewer.source_path = None
            self._geographic_viewer.configure(image="", text="")
            self._geographic_preview_photo = None
        else:
            self._geographic_review_name.set(str(current["name"]))
            self._geographic_review_detail.set(
                f"Distancia: {current['distance_m']:.3f} m | "
                f"{self._geographic_selection.index + 1} de {count}"
            )
            path = Path(current["path"])
            if self._geographic_viewer.source_path != path:
                self._geographic_viewer.source_path = path
                self._geographic_viewer.reset_view()
            self._load_geographic_preview()

        self._geographic_previous_button.configure(
            state=(
                "normal"
                if current is not None and self._geographic_selection.index > 0
                else "disabled"
            )
        )
        self._geographic_next_button.configure(
            state=(
                "normal"
                if current is not None
                and self._geographic_selection.index < count - 1
                else "disabled"
            )
        )
        self._geographic_remove_button.configure(
            state="normal" if current is not None else "disabled"
        )
        self._geographic_restore_button.configure(
            state=(
                "normal"
                if self._geographic_selection.selected_count
                < self._geographic_selection.original_count
                else "disabled"
            )
        )
        self._geographic_save_button.configure(
            state="normal" if count > 0 else "disabled"
        )

    def _load_geographic_preview(self) -> None:
        if (
            self._geographic_preview_rendering
            or not hasattr(self, "_geographic_viewer")
            or self._geographic_viewer.source_path is None
        ):
            return
        self._geographic_preview_rendering = True
        try:
            width = max(self._geographic_viewer.winfo_width() - 8, 320)
            height = max(self._geographic_viewer.winfo_height() - 8, 240)
            with Image.open(self._geographic_viewer.source_path) as source:
                original = ImageOps.exif_transpose(source).copy()
            fit_scale = min(width / original.width, height / original.height)
            fit_size = (
                max(round(original.width * fit_scale), 1),
                max(round(original.height * fit_scale), 1),
            )
            self._geographic_viewer.set_content_size(*fit_size)
            scaled_size = (
                max(
                    round(
                        fit_size[0] * self._geographic_viewer.zoom_factor
                    ),
                    1,
                ),
                max(
                    round(
                        fit_size[1] * self._geographic_viewer.zoom_factor
                    ),
                    1,
                ),
            )
            preview = original.resize(scaled_size, Image.Resampling.LANCZOS)
            if self._geographic_viewer.zoom_factor > 1.0:
                crop_width = min(width, scaled_size[0])
                crop_height = min(height, scaled_size[1])
                left = round(
                    (scaled_size[0] - crop_width) / 2
                    - self._geographic_viewer.offset_x
                )
                top = round(
                    (scaled_size[1] - crop_height) / 2
                    - self._geographic_viewer.offset_y
                )
                left = min(max(left, 0), scaled_size[0] - crop_width)
                top = min(max(top, 0), scaled_size[1] - crop_height)
                preview = preview.crop(
                    (left, top, left + crop_width, top + crop_height)
                )
            photo = ImageTk.PhotoImage(preview)
            self._geographic_preview_photo = photo
            self._geographic_viewer.configure(image=photo, text="")
        except (OSError, ValueError):
            self._geographic_preview_photo = None
            self._geographic_viewer.configure(
                image="", text="No se pudo previsualizar"
            )
        finally:
            self._geographic_preview_rendering = False

    def _previous_geographic_image(self) -> None:
        self._geographic_selection.previous()
        self._geographic_viewer.reset_view()
        self._refresh_geographic_selection()

    def _next_geographic_image(self) -> None:
        self._geographic_selection.next()
        self._geographic_viewer.reset_view()
        self._refresh_geographic_selection()

    def _remove_geographic_image(self) -> None:
        self._geographic_selection.remove_current()
        self._geographic_viewer.reset_view()
        self._refresh_geographic_selection()

    def _restore_geographic_selection(self) -> None:
        self._geographic_selection.restore()
        self._geographic_viewer.reset_view()
        self._refresh_geographic_selection()

    def _save_geographic_selection(self) -> None:
        if (
            self._geographic_root is None
            or self._geographic_selection.selected_count == 0
        ):
            return
        folder_name = simpledialog.askstring(
            "Guardar selección",
            "Nombre de la nueva subcarpeta:",
            parent=self.root,
        )
        if folder_name is None:
            return
        if not messagebox.askyesno(
            "Confirmar exportación",
            (
                f"Se exportarán "
                f"{self._geographic_selection.selected_count} fotografías "
                f"a la subcarpeta:\n\n{folder_name}\n\n"
                "También se generará un archivo KMZ autocontenido."
            ),
            parent=self.root,
        ):
            return
        try:
            result = export_geographic_selection(
                self._geographic_root,
                folder_name,
                self._geographic_selection.selected_results,
            )
        except GeographicExportError as error:
            messagebox.showerror(
                "No se pudo guardar la selección",
                str(error),
                parent=self.root,
            )
            return
        messagebox.showinfo(
            "Selección guardada",
            (
                f"Fotografías guardadas: {result['image_count']}\n\n"
                f"Subcarpeta:\n{result['destination_folder']}\n\n"
                f"KMZ:\n{Path(result['kmz_path']).name}"
            ),
            parent=self.root,
        )

    def _reset_for_new_project(self) -> None:
        if self._alternative_click_job is not None:
            self.root.after_cancel(self._alternative_click_job)
            self._alternative_click_job = None
        self.zoom_results = []
        self.set_results = []
        self.set_id_map = {}
        self.next_set_id = 1
        self.current_root = None
        self.output_plan = None
        self.output_is_stale = False
        self.pending_reviews = []
        self.review_items = []
        self.review_index = 0
        self.review_active = False
        self.review_mode.set("Revisar pendientes")
        self._records = []
        self._images = []
        self._recognized_count = 0
        self._unrecognized = []
        self._parsed_images = []
        self._gps_metadata = GPSMetadataCache()
        self._suggested_zoom = None
        self._original_suggested_zoom = None
        self._viewed_zoom_candidate = None
        self._target_time_seconds = None
        self._zoom_neighbor_items = []
        self.zoom_viewer.source_path = None
        self.zoom_viewer.reset_view()
        if hasattr(self, "_review_source_path"):
            del self._review_source_path
        self.output_plan_summary.set("")
        self.finalize_button.configure(state="disabled")
        self._refresh_set_list()
        self._refresh_review_list()
        self.status.set("Seleccione la carpeta raíz del proyecto.")

    @staticmethod
    def _set_identity(result: dict[str, object]) -> tuple[object, str]:
        return result["row_number"], str(result["file_reference"])

    def _assign_set_ids(self) -> None:
        for result in self.set_results:
            identity = self._set_identity(result)
            if identity not in self.set_id_map:
                self.set_id_map[identity] = f"SET-{self.next_set_id:04d}"
                self.next_set_id += 1
            result["set_id"] = self.set_id_map[identity]

    def _prepare_set_ids_for_root(self, root_folder: Path) -> None:
        resolved_root = root_folder.resolve()
        if self.current_root != resolved_root:
            self.current_root = resolved_root
            self.set_id_map = {}
            self.next_set_id = 1

    def _refresh_set_list(self) -> None:
        selection = self.set_list.curselection()
        selected_index = selection[0] if selection else 0
        self.set_list.delete(0, "end")
        for result in self.set_results:
            visible_status = (
                "SET completo"
                if result["status"] == "SET_COMPLETE"
                else "SET incompleto"
            )
            self.set_list.insert(
                "end",
                f"{result['set_id']}   Fila {result['row_number']}   "
                f"{visible_status}   "
                f"Sec. {result['sequence']}",
            )
        if not self.set_results:
            self._clear_set_details()
            return
        selected_index = min(selected_index, len(self.set_results) - 1)
        self.set_list.selection_set(selected_index)
        self.set_list.activate(selected_index)
        self._show_selected_set(selected_index)

    def _on_set_selected(self, _event: object = None) -> None:
        selection = self.set_list.curselection()
        if selection:
            self._show_selected_set(selection[0])

    def _clear_set_details(self) -> None:
        self._set_preview_photos.clear()
        self.set_details.configure(text="Sin SET disponibles")
        for label in self.set_preview_labels.values():
            label.configure(image="", text="NO ENCONTRADA")

    def _show_selected_set(self, index: int) -> None:
        result = self.set_results[index]
        zoom_result = next(
            (
                correlation
                for correlation in self.zoom_results
                if correlation.get("zoom") is result["zoom"]
                and correlation.get("row_number") == result["row_number"]
            ),
            None,
        )
        zoom_difference = (
            zoom_result.get("time_difference") if zoom_result else None
        )
        visible_status = (
            "Completo" if result["status"] == "SET_COMPLETE" else "Incompleto"
        )
        detail_lines = [
            f"ID: {result['set_id']}",
            f"Fila Excel: {result['row_number']}",
            f"Referencia Excel: {result['file_reference']}",
            f"Secuencia: {result['sequence']}",
            f"Estado: {visible_status}",
            "",
            "ZOOM",
            f"Nombre: {result['zoom']['name']}",
            f"Diferencia respecto del Excel: {zoom_difference:+d} segundos",
        ]
        for key, label in (("wide", "WIDE"), ("thermal", "THERMAL")):
            channel = "W" if key == "wide" else "T"
            detail_lines.extend(["", label])
            if channel in result["ambiguous_channels"]:
                detail_lines.append("AMBIGUA")
                detail_lines.extend(
                    f"- {candidate['name']}"
                    for candidate in result["ambiguous_channels"][channel]
                )
            elif result[key] is None:
                detail_lines.append("Nombre: NO ENCONTRADA")
            else:
                detail_lines.extend(
                    [
                        f"Nombre: {result[key]['name']}",
                        "Diferencia respecto de Zoom: "
                        f"{result['channel_differences'][channel]:+d} segundos",
                    ]
                )
        self.set_details.configure(text="\n".join(detail_lines))

        self._set_preview_photos.clear()
        self._show_set_preview("Zoom", result["zoom"], False)
        self._show_set_preview(
            "Wide", result["wide"], "W" in result["ambiguous_channels"]
        )
        self._show_set_preview(
            "Thermal", result["thermal"], "T" in result["ambiguous_channels"]
        )

    def _show_set_preview(
        self,
        channel: str,
        image: dict[str, object] | None,
        ambiguous: bool,
    ) -> None:
        label = self.set_preview_labels[channel]
        if ambiguous:
            label.configure(image="", text="AMBIGUA")
            return
        if image is None:
            label.configure(image="", text="NO ENCONTRADA")
            return
        try:
            with Image.open(image["path"]) as source:
                preview = ImageOps.exif_transpose(source)
                preview.thumbnail((150, 110), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(preview)
            self._set_preview_photos[channel] = photo
            label.configure(image=photo, text="")
        except (OSError, ValueError):
            label.configure(image="", text="No se pudo previsualizar")

    def _show_project_check_dialog(self, root_folder: Path) -> bool:
        entries = list(root_folder.iterdir())
        source_excel = next(
            entry
            for entry in entries
            if entry.is_file()
            and not entry.name.startswith("~$")
            and not entry.name.startswith("Trazabilidad MAAT One ")
            and entry.suffix.casefold() in {".xlsx", ".xls"}
        )
        traceability_names = sorted(
            entry.name
            for entry in entries
            if entry.is_file()
            and entry.name.startswith("Trazabilidad MAAT One ")
            and entry.suffix.casefold() in {".xlsx", ".xls"}
        )
        anomaly_names = sorted(
            entry.name
            for entry in entries
            if entry.is_dir() and entry.name.startswith("Anomalias One ")
        )
        expected_traceability = (
            f"Trazabilidad MAAT One {root_folder.name}.xlsx"
        )
        expected_anomalies = f"Anomalias One {root_folder.name}"
        matching_traceability = expected_traceability in traceability_names
        matching_anomalies = expected_anomalies in anomaly_names
        inconsistent_traceability = [
            name for name in traceability_names if name != expected_traceability
        ]
        inconsistent_anomalies = [
            name for name in anomaly_names if name != expected_anomalies
        ]
        has_outputs = bool(traceability_names or anomaly_names)
        has_inconsistencies = bool(
            inconsistent_traceability or inconsistent_anomalies
        )

        matching_lines: list[str] = []
        if matching_traceability:
            matching_lines.append(
                f"Trazabilidad coincidente: {expected_traceability}"
            )
        if matching_anomalies:
            matching_lines.append(
                f"Carpeta coincidente: {expected_anomalies}"
            )

        dialog = tk.Toplevel(self.root)
        dialog.title("Comprobación del proyecto")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(background=self._theme["background"])
        content = ttk.Frame(dialog, padding=24)
        content.pack(fill="both", expand=True)
        semantic_colors = (
            {
                "normal": "#4ADE80",
                "warning": "#FBBF24",
                "error": "#FB7185",
                "error_background": "#3A242A",
            }
            if self.dark_mode
            else {
                "normal": "#15803D",
                "warning": "#B45309",
                "error": "#C2410C",
                "error_background": "#FFF1F2",
            }
        )
        style = ttk.Style(self.root)
        state = (
            "normal"
            if not has_outputs
            else "error" if has_inconsistencies else "warning"
        )
        style.configure(
            "ProjectCheck.Heading.TLabel",
            background=self._theme["panel"],
            foreground=semantic_colors[state],
            font=("Segoe UI", 14, "bold"),
        )
        heading = {
            "normal": "PROYECTO LISTO PARA PROCESAR",
            "warning": "LÍNEA PROCESADA ANTERIORMENTE",
            "error": "INCONSISTENCIA DETECTADA",
        }[state]
        ttk.Label(
            content,
            text=heading,
            style="ProjectCheck.Heading.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            content,
            text=root_folder.name,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(6, 16))

        if state == "normal":
            ttk.Label(
                content,
                text=f"Excel:\n{source_excel.name}",
                justify="left",
            ).pack(anchor="w")
            ttk.Label(
                content,
                text="Todo listo para comenzar.",
                foreground=semantic_colors["normal"],
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w", pady=(16, 0))
        elif state == "warning":
            existing_lines = [f"✓ Excel para procesar:\n{source_excel.name}"]
            if matching_traceability:
                existing_lines.append(
                    f"✓ Trazabilidad:\n{expected_traceability}"
                )
            if matching_anomalies:
                existing_lines.append(
                    f"✓ Carpeta de anomalías:\n{expected_anomalies}"
                )
            ttk.Label(
                content,
                text="\n\n".join(existing_lines),
                justify="left",
                wraplength=650,
            ).pack(anchor="w")
            ttk.Label(
                content,
                text=(
                    "Las salidas encontradas corresponden con esta línea."
                ),
                foreground=semantic_colors["warning"],
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w", pady=(16, 8))
            ttk.Label(
                content,
                text=(
                    "Puede continuar para volver a procesarla.\n"
                    "Se mantendrán las protecciones existentes sobre "
                    "archivos ya generados."
                ),
                justify="left",
                wraplength=650,
            ).pack(anchor="w")
        else:
            for name in inconsistent_traceability:
                block = tk.Frame(
                    content,
                    background=semantic_colors["error_background"],
                    highlightbackground=semantic_colors["error"],
                    highlightthickness=1,
                )
                block.pack(fill="x", pady=(0, 10))
                tk.Label(
                    block,
                    text=(
                        "TRAZABILIDAD ENCONTRADA\n"
                        f"{name}\n\n"
                        "TRAZABILIDAD ESPERADA\n"
                        f"{expected_traceability}\n\n"
                        "⚠ Los nombres no coinciden."
                    ),
                    background=semantic_colors["error_background"],
                    foreground=semantic_colors["error"],
                    justify="left",
                    anchor="w",
                    padx=12,
                    pady=10,
                    font=("Segoe UI", 9, "bold"),
                ).pack(fill="x")
            for name in inconsistent_anomalies:
                block = tk.Frame(
                    content,
                    background=semantic_colors["error_background"],
                    highlightbackground=semantic_colors["error"],
                    highlightthickness=1,
                )
                block.pack(fill="x", pady=(0, 10))
                tk.Label(
                    block,
                    text=(
                        "CARPETA ENCONTRADA\n"
                        f"{name}\n\n"
                        "CARPETA ESPERADA\n"
                        f"{expected_anomalies}\n\n"
                        "⚠ Los nombres no coinciden."
                    ),
                    background=semantic_colors["error_background"],
                    foreground=semantic_colors["error"],
                    justify="left",
                    anchor="w",
                    padx=12,
                    pady=10,
                    font=("Segoe UI", 9, "bold"),
                ).pack(fill="x")
            if matching_lines:
                ttk.Label(
                    content,
                    text=(
                        "Coincidencias encontradas:\n"
                        + "\n".join(f"✓ {line}" for line in matching_lines)
                    ),
                    justify="left",
                    wraplength=650,
                ).pack(anchor="w", pady=(4, 12))
            ttk.Label(
                content,
                text="Revise las inconsistencias antes de continuar.",
                foreground=semantic_colors["error"],
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w", pady=(4, 8))
            ttk.Label(
                content,
                text=(
                    "Puede continuar para procesar, pero MAAT One mantendrá "
                    "todas las protecciones existentes sobre las salidas "
                    "anteriores."
                ),
                justify="left",
                wraplength=650,
            ).pack(anchor="w")

        decision = False

        def continue_processing() -> None:
            nonlocal decision
            decision = True
            dialog.destroy()

        buttons = ttk.Frame(content)
        buttons.pack(pady=(20, 0))
        ttk.Button(
            buttons,
            text="Continuar para procesar",
            command=continue_processing,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            buttons,
            text="Cancelar",
            command=dialog.destroy,
        ).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self._center_dialog(dialog)
        dialog.grab_set()
        dialog.wait_window()
        return decision

    def _select_root_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Seleccionar carpeta raíz",
        )
        if selected:
            self.finalize_button.configure(state="disabled")
            self.output_plan = None
            self.output_plan_summary.set("")
            self.zoom_results = []
            self.pending_reviews = []
            self.review_index = 0
            self.review_active = False
            self._refresh_review_list()
            root_folder = Path(selected)
            validation_result = validate_root_folder(root_folder)
            if not validation_result.startswith("Proyecto válido"):
                self.status.set(validation_result)
                return

            self.output_is_stale = False
            if not self._show_project_check_dialog(root_folder):
                return

            try:
                records = read_project_excel(root_folder)
            except Exception:
                self.status.set(
                    "Proyecto no válido\n"
                    "No se pudo leer el archivo Excel."
                )
                return

            try:
                images, duplicates = inventory_project_images(root_folder)
            except Exception:
                self.status.set(
                    "Proyecto detenido\n"
                    "No se pudo leer la carpeta Materiales."
                )
                return

            if duplicates:
                duplicate_lines = [
                    "Proyecto detenido",
                    "Se encontraron nombres de imágenes repetidos.",
                    "",
                    f"Duplicados: {len(duplicates)}",
                    "",
                ]
                for name, paths in duplicates.items():
                    duplicate_lines.append(name)
                    duplicate_lines.extend(f"- {path}" for path in paths)
                self.status.set("\n".join(duplicate_lines))
                return

            parsed_images = parse_dji_images(images)
            recognized_count = sum(
                image["recognized"] for image in parsed_images
            )
            unrecognized = [
                image for image in parsed_images if not image["recognized"]
            ]
            self._prepare_set_ids_for_root(root_folder)
            self._records = records
            self._images = images
            self._recognized_count = recognized_count
            self._unrecognized = unrecognized
            self._parsed_images = parsed_images
            self.zoom_results = match_zoom_images(records, parsed_images)
            self.pending_reviews = [
                result
                for result in self.zoom_results
                if result["status"] == "ZOOM_PENDING_REVIEW"
            ]
            self.review_index = 0
            self.review_active = True
            self._refresh_summary()
            self._refresh_review_list()

    def run(self) -> None:
        self.root.mainloop()
