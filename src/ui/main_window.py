"""Ventana principal de MAAT One."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageOps, ImageTk

from src.dji_parser import parse_dji_images
from src.manual_sets import manual_record, link_zoom, record_details
from src.excel_traceability import TraceabilityError, generate_traceability_excel
from src.excel_reader import find_project_excels, read_project_excel
from src.gps_metadata import GPSMetadataCache
from src.safe_export import export_plan, UNIFIED, SEPARATED
from src.ui.progress_dialog import run_task
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
from src.image_inventory import build_image_inventory, inventory_project_images
from src.output_copier import copy_planned_images
from src.output_planner import build_output_plan
from src.set_builder import build_sets
from src.anomaly_view import describe_anomalies
from src.zoom_matcher import decide_zoom, match_zoom_images
from src.ui.coordinate_search_window import CoordinateSearchWindow


def validate_root_folder(root_folder: Path) -> str:
    """Valida que el proyecto contenga exactamente un Excel."""
    excel_files = find_project_excels(root_folder)
    if not excel_files:
        return (
            "Proyecto no válido\n"
            "No se encontró un archivo Excel en la carpeta seleccionada ni en sus subcarpetas."
        )
    if len(excel_files) > 1:
        return (
            "Proyecto no válido\n"
            "Se encontró más de un archivo Excel. Deje solo uno dentro de la carpeta seleccionada y sus subcarpetas."
        )

    return (
        "Proyecto válido\n"
        f"Carpeta: {root_folder.name}\n"
        f"Excel: {excel_files[0].name}\n"
        "Imágenes: se buscarán en la raíz y todas sus subcarpetas"
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

        self._excluded_history = []
        self._left_images = {}
        self._configure_styles()
        self._build_interface()
        self.zoom_viewer.bind("<Double-Button-1>", lambda e: self._open_fullscreen_review())
        self._wrap_buttons(self.root)

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
        self._view_host = ttk.Frame(self.root, style="App.TFrame")
        self._view_host.pack(fill="both", expand=True)
        self._processing_view = ttk.Frame(self._view_host, style="App.TFrame", padding=(10, 8))
        self._processing_view.pack(fill="both", expand=True)
        self._geographic_view = ttk.Frame(self._view_host, style="App.TFrame")
        content = self._processing_view

        workspace = ttk.Panedwindow(content, orient="horizontal")
        workspace.pack(fill="both", expand=True)
        self.workspace_panes = workspace
        left_column = ttk.Frame(workspace, width=360)
        left_column.pack_propagate(False)
        sidebar = ttk.Frame(left_column, padding=(6, 0, 6, 4))
        sidebar.pack(fill="x")
        self.theme_button = ttk.Button(
            sidebar,
            text="Oscuro",
            command=self._toggle_theme,
            style="Theme.TButton",
        )
        self.theme_button.pack(side="right", anchor="n")

        ttk.Label(sidebar, text="MAAT One", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            sidebar,
            text="Revisión de imágenes",
            style="Subtitle.TLabel",
        ).pack(pady=(2, 8))

        ttk.Button(
            sidebar,
            text="Seleccionar carpeta raíz",
            command=self._select_root_folder,
            style="Action.TButton",
            cursor="hand2",
        ).pack()
        ttk.Button(
            sidebar,
            text="Buscador por coordenadas",
            command=self._open_coordinate_search,
            cursor="hand2",
        ).pack(pady=(4, 0))
        ttk.Button(sidebar, text="Buscar por ubicación / imagen", command=self._open_geographic_search).pack(pady=(4, 0))
        self.finalize_button = ttk.Button(
            sidebar,
            text="Finalizar y generar",
            command=self._finalize_output,
            state="disabled",
        )
        self.finalize_button.pack(pady=(4, 0))
        self.output_plan_summary = tk.StringVar(value="")
        ttk.Label(
            sidebar,
            textvariable=self.output_plan_summary,
            style="Status.TLabel",
            wraplength=180,
            justify="left",
        ).pack(pady=(3, 0))

        result_area = ttk.Frame(left_column, style="App.TFrame")
        result_area.pack(fill="both", expand=True)
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
            height=3,
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
            text="Anomalías del Excel y agregadas",
            padding=(8, 6),
        )
        set_panel.pack(fill="both", expand=True, pady=(6, 0))
        list_frame = ttk.Frame(set_panel)
        list_frame.pack(fill="x")
        self.set_list = tk.Listbox(
            list_frame,
            height=6,
            font=("Segoe UI", 9),
            exportselection=False,
        )
        self.set_list.pack(side="left", fill="x", expand=True)
        self.set_scrollbar = ttk.Scrollbar(list_frame, command=self.set_list.yview)
        self.set_scrollbar.pack(side="right", fill="y")
        self.set_list.configure(yscrollcommand=self.set_scrollbar.set)
        def scroll_sets(event):
            direction = -1 if getattr(event, "num", 0) == 4 or getattr(event, "delta", 0) > 0 else 1
            self.set_list.yview_scroll(direction * 3, "units")
            return "break"
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.set_list.bind(event, scroll_sets)
        manual_buttons = ttk.Frame(set_panel)
        manual_buttons.pack(fill="x", pady=3)
        ttk.Button(manual_buttons, text="Vincular Zoom manualmente", command=self._link_manual_zoom).pack(fill="x")
        ttk.Button(manual_buttons, text="Agregar SET manual", command=self._add_manual_set).pack(fill="x")
        ttk.Button(manual_buttons, text="Quitar SET seleccionado", command=self._exclude_set).pack(fill="x")
        ttk.Button(manual_buttons, text="Deshacer última exclusión", command=self._undo_exclude).pack(fill="x")
        self.set_list.bind("<<ListboxSelect>>", self._on_set_selected)
        self.set_details = ttk.Label(
            set_panel,
            text="Sin SET disponibles",
            justify="left",
            wraplength=320,
        )
        self.set_details.pack(anchor="w", fill="x", pady=(4, 3))
        set_panel.bind("<Configure>", lambda event: self.set_details.configure(wraplength=max(180, event.width - 24)))
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
                width=9,
            )
            preview_label.pack(fill="both", expand=True)
            self.set_preview_labels[channel] = preview_label
            preview_label.bind("<Double-Button-1>", lambda e, key=channel: self._open_image_popup(self._left_images.get(key)))
        text_area.pack(fill="x", pady=(4, 0))

        review_panel = ttk.LabelFrame(
            workspace,
            text="Revisión de Zoom",
            padding=(10, 7),
        )
        workspace.add(left_column, weight=1)
        workspace.add(review_panel, weight=3)
        self.root.after(150, lambda: workspace.sashpos(0, max(300, int(workspace.winfo_width() * 0.25))))
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
        review_body.columnconfigure(0, weight=1, uniform="review_columns")
        review_body.columnconfigure(1, weight=3, uniform="review_columns")
        review_body.rowconfigure(0, weight=1)
        details_panel = ttk.Frame(review_body, width=240)
        details_panel.grid_propagate(False)
        details_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        details_panel.rowconfigure(2, weight=1)
        self.excel_highlights = {}
        highlight_panel = ttk.Frame(details_panel)
        highlight_panel.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for key in ("elemento", "anomalia", "criticidad"):
            label = tk.Label(highlight_panel, text="", font=("Segoe UI", 12, "bold"),
                             anchor="w", justify="left", wraplength=245,
                             background="#E8F0FE", foreground="#123E82", padx=7, pady=5)
            label.pack(fill="x", pady=2)
            self.excel_highlights[key] = label
        details_panel.columnconfigure(0, weight=1)
        details_canvas = tk.Canvas(details_panel, width=250, height=150,
                                   highlightthickness=0, background=self._theme["background"])
        details_scroll = ttk.Scrollbar(details_panel, orient="vertical", command=details_canvas.yview)
        details_canvas.configure(yscrollcommand=details_scroll.set)
        details_canvas.grid(row=2, column=0, sticky="nsew")
        details_scroll.grid(row=2, column=1, sticky="ns")
        self.review_details = ttk.Label(details_canvas, text="", justify="left", anchor="nw", wraplength=240)
        details_window = details_canvas.create_window((0, 0), window=self.review_details, anchor="nw")
        self.review_details.bind("<Configure>", lambda event: details_canvas.configure(scrollregion=details_canvas.bbox("all")))
        def resize_details(event):
            details_canvas.itemconfigure(details_window, width=event.width)
            self.review_details.configure(wraplength=max(100, event.width - 10))
        details_canvas.bind("<Configure>", resize_details)
        self.review_details_canvas = details_canvas
        details_canvas.grid_remove()
        details_scroll.grid_remove()
        details_open = tk.BooleanVar(value=False)
        def toggle_details():
            if details_open.get():
                details_canvas.grid(); details_scroll.grid()
            else:
                details_canvas.grid_remove(); details_scroll.grid_remove()
        ttk.Checkbutton(details_panel, text="Detalles técnicos", variable=details_open, command=toggle_details).grid(row=1, column=0, columnspan=2, sticky="w")
        def wrap_highlights(event):
            for label in self.excel_highlights.values():
                label.configure(wraplength=max(120, event.width-20))
        details_panel.bind("<Configure>", wrap_highlights)
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
        navigation = ttk.Frame(review_panel)
        navigation.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(navigation, text="← SET anterior", command=lambda: self._navigate_set(-1)).pack(side="left")
        ttk.Button(navigation, text="SET siguiente →", command=lambda: self._navigate_set(1)).pack(side="right")
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
                if hasattr(self, "_review_source_path"):
                    del self._review_source_path
                self.zoom_viewer.source_path = None
                previous_photo = self._preview_photo
                self.preview_label.configure(image="", text="")
                self._preview_photo = None
                self._defer_preview_photo_release(previous_photo)
                self.review_details.configure(text="")
                for label in self.excel_highlights.values(): label.configure(text="")
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
            self._sync_left(self._set_identity(result))
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
                "ZOOM_EXACT",
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
        record = next((row for row in self._records if self._set_identity(row) == identity), {})
        excel_data = record.get("original_data", {})
        def excel_value(key):
            value = excel_data.get(key)
            return str(value).strip() if value is not None and str(value).strip() else "Sin dato en el Excel"
        for key, title in (("elemento", "Elemento"), ("anomalia", "Anomalía"), ("criticidad", "Criticidad")):
            self.excel_highlights[key].configure(text=f"{title}: {excel_value(key)}")
        colors = {"baja": ("#DCFCE7", "#166534"), "media": ("#FEF3C7", "#854D0E"),
                  "alta": ("#FFEDD5", "#9A3412"), "urgente": ("#FEE2E2", "#B91C1C")}
        bg, fg = colors.get(excel_value("criticidad").casefold(), ("#E8F0FE", "#123E82"))
        self.excel_highlights["criticidad"].configure(background=bg, foreground=fg)
        self.review_details_canvas.yview_moveto(0)
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
        preview = build_sets([{**result, "zoom": candidate, "status": "ZOOM_ACCEPTED"}], self._parsed_images)[0]
        for title, key, channel in (("Zoom", "zoom", "Z"), ("Wide", "wide", "W"), ("Thermal", "thermal", "T")):
            self._show_set_preview(title, preview[key], channel in preview["ambiguous_channels"])
        self.set_details.configure(text=record_details(record, result) + "\nVista actual: " + candidate["name"] + "\nAceptar confirma esta Zoom y sus pares.")
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
        self._wrap_buttons(dialog)
        dialog.grab_set()
        dialog.wait_window()

    def _confirm_viewed_zoom(
        self,
        override_reason: str,
        override_distance_m: float | None,
    ) -> bool:
        if (
            self._suggested_zoom is None
            or self._viewed_zoom_candidate is None
        ):
            return False

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
        except Exception as error:
            result.clear()
            result.update(previous_result)
            self.set_results = previous_sets
            self.set_id_map = previous_set_ids
            self.next_set_id = previous_next_set_id
            self._refresh_set_list()
            self._show_current_review()
            messagebox.showerror("No se confirmó la imagen", str(error), parent=self.root)
            return False

        self.output_plan = None
        self.output_plan_summary.set("")
        self.output_is_stale = False
        self._suggested_zoom = selected_zoom
        self._viewed_zoom_candidate = selected_zoom
        self._zoom_neighbor_items = self._zoom_neighbors(selected_zoom)
        self._refresh_review_list(preferred_identity=identity)
        return True

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
                    foreground=self._time_color(child._time_seconds) if hasattr(child, "_time_seconds") else self._theme["text"],
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
                foreground=self._time_color(seconds),
                justify="center",
                font=("Segoe UI", 8, "bold" if is_suggested else "normal"),
            )
            caption._time_seconds = seconds
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
        self._open_image_popup(self._viewed_zoom_candidate)

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
        self._open_image_popup(self._viewed_zoom_candidate)

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
            if result["status"] != "EXCLUDED" and (self._set_identity(result) in active_sets
            or id(result) in manual_items)
        ]

    def _refresh_review_list(
        self,
        preferred_identity: tuple[object, str] | None = None,
        select_first: bool = False,
    ) -> None:
        self.review_items = (
            [r for r in self.pending_reviews if r["status"] != "EXCLUDED"]
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

        statuses = [row["status"] for row in self.zoom_results if row["status"] != "EXCLUDED"]
        total = len(statuses)
        automatic = statuses.count("ZOOM_EXACT")
        manual = statuses.count("ZOOM_ACCEPTED")
        pending = statuses.count("ZOOM_PENDING_REVIEW")
        rejected = statuses.count("ZOOM_REJECTED")
        unresolved = total - automatic - manual - pending - rejected
        complete = sum(row["status"] == "SET_COMPLETE" for row in self.set_results)
        incomplete = len(self.set_results) - complete
        images = sum(row.get(channel) is not None for row in self.set_results for channel in ("wide", "zoom", "thermal"))
        message = (
            f"Total: {total} anomalías · {total * 3} imágenes esperadas\n"
            f"Automáticas: {automatic} · Confirmadas manualmente: {manual} · Pendientes: {pending}\n"
            f"Sin resolver: {unresolved} · Rechazadas: {rejected}\n"
            f"Listas para exportar: {complete} SET completos + {incomplete} incompletos · {images} imágenes asociadas"
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
        # Una miniatura pendiente de renderizar no debe cambiar lo aceptado.
        if self._alternative_click_job is not None:
            self.root.after_cancel(self._alternative_click_job)
            self._alternative_click_job = None
        if approved:
            if self._viewed_zoom_candidate is None:
                return
            if not self._confirm_viewed_zoom(
                "Imagen visible confirmada con Aceptar", None
            ):
                return
        else:
            decide_zoom(result, False)
        self.output_plan = None
        self.output_plan_summary.set("")
        pending_after = any(
            item["status"] == "ZOOM_PENDING_REVIEW"
            for item in self.zoom_results
        )
        self._refresh_summary()
        preferred_identity = current_identity
        position = self.zoom_results.index(result)
        ordered = self.zoom_results[position + 1:] + self.zoom_results[:position]
        next_pending = next((item for item in ordered if item["status"] == "ZOOM_PENDING_REVIEW"), None)
        if next_pending is not None:
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
            f"Anomalías activas: {sum(r['status'] != 'EXCLUDED' for r in self.zoom_results)} · Excluidas: {sum(r['status'] == 'EXCLUDED' for r in self.zoom_results)}",
            f"Imágenes encontradas en el proyecto: {len(self._images)}",
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
            f"Fecha en A u hora en G inválida: {statuses.count('INVALID_REFERENCE')}",
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
            ("INVALID_REFERENCE", "Fecha en A u hora en G inválida:"),
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
            result["status"] in {"ZOOM_PENDING_REVIEW", "INVALID_REFERENCE"}
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
                "Existen revisiones pendientes o fechas/horas inválidas en A/G."
            )
            return
        self.output_plan = build_output_plan(
            self.current_root,
            self.zoom_results,
            self.set_results,
        )
        records = {self._set_identity(row): row for row in self._records}
        for item in self.output_plan["sets"]:
            item["source_record"] = records[self._set_identity(item)]
            item["anomaly_name"] = item["source_record"].get("original_data", {}).get("anomalia", "")
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
            "Destino: carpeta raíz del proyecto (según el modo elegido)",
        ]
        if self.output_plan["reason"]:
            lines.extend(["", self.output_plan["reason"]])
        lines.append("Las imágenes y los dos Excel se guardarán directamente en la raíz, sin carpeta contenedora.")
        self.output_plan_summary.set("\n".join(lines))
        if not self.output_plan["reason"]:
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
            ("Ubicación", str(Path(plan["root_folder"]))),
            ("Cantidad de SET completos", summary["complete_sets"]),
            ("Cantidad de SET incompletos", summary["incomplete_sets"]),
            ("Cantidad de SET descartados", summary["discarded_sets"]),
            ("Imágenes únicas seleccionadas", summary["images_to_copy"]),
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

        export_mode = tk.StringVar(value=UNIFIED)
        ttk.Label(content, text="Modo de salida:").grid(row=len(details), column=0, sticky="w", pady=8)
        ttk.Combobox(content, textvariable=export_mode, values=(UNIFIED, SEPARATED), state="readonly", width=28).grid(row=len(details), column=1, sticky="w", pady=8)
        buttons = ttk.Frame(content)
        buttons.grid(
            row=len(details) + 1, column=0, columnspan=2, pady=(20, 0)
        )
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(
            side="left", padx=(0, 8)
        )

        def begin_copy() -> None:
            selected_mode = export_mode.get()
            dialog.destroy()
            try:
                copy_summary, result_folder, traceability_path = run_task(self.root, "Exportando " + selected_mode, lambda report: export_plan(self.output_plan, selected_mode, report))
            except Exception as error:
                self.output_plan_summary.set(f"No se publicó el resultado: {error}")
                return
            summary = self.output_plan["summary"]
            self.output_plan_summary.set(
                "Proceso finalizado\n"
                f"SET procesados: {copy_summary['sets_processed']}\n"
                f"Imágenes copiadas: {copy_summary['images_copied']}\n"
                f"Imágenes ya existentes: {copy_summary['images_existing']}\n"
                f"SET completos: {summary['complete_sets']}\n"
                f"SET incompletos: {summary['incomplete_sets']}\n\n"
                f"Modo: {selected_mode}\nCarpeta:\n{result_folder}\n\n"
                f"Trazabilidad:\n{traceability_path.name}\n\nSegundo Excel:\nAnomalias.xlsx"
            )
            self._show_process_completed_dialog()

        ttk.Button(
            buttons,
            text="Comenzar copia",
            command=begin_copy,
        ).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self._center_dialog(dialog)
        self._wrap_buttons(dialog)
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
                "trazabilidad y el segundo Excel Anomalias.xlsx."
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
        self._wrap_buttons(dialog)
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
        ttk.Button(self._geographic_view, text="Volver a revisión de SET", command=self._show_processing_view).pack(anchor="w", padx=24, pady=4)
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
        self._excluded_history = []
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
        for result in self.zoom_results + self.set_results:
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
        self.anomaly_rows = describe_anomalies([row for row in self.zoom_results if row["status"] != "EXCLUDED"], getattr(self, "_parsed_images", []))
        for row in self.anomaly_rows:
            result = row["correlation"]
            self.set_list.insert("end", f"{result.get('set_id', 'SET')} · {'Agregado manualmente · ' if result.get('manual_added') else ''}{row['label']} · {row['available']}/3")
        for index, row in enumerate(self.anomaly_rows):
            status = row["correlation"]["status"]
            color = {"ZOOM_ACCEPTED": "#166534", "ZOOM_EXACT": "#1D4ED8", "ZOOM_PENDING_REVIEW": "#854D0E"}.get(status, "#B91C1C")
            self.set_list.itemconfigure(index, foreground=color)
        if not self.anomaly_rows:
            self._clear_set_details()
            return
        selected_index = min(selected_index, len(self.anomaly_rows) - 1)
        self.set_list.selection_set(selected_index)
        self.set_list.activate(selected_index)
        self._show_selected_set(selected_index)

    def _sync_left(self, identity):
        for index, row in enumerate(self.anomaly_rows):
            if self._set_identity(row["correlation"]) == identity:
                self.set_list.selection_clear(0, "end")
                self.set_list.selection_set(index)
                self.set_list.see(index)
                self._show_selected_set(index)
                return

    def _navigate_set(self, direction):
        if not self.anomaly_rows: return
        selected = self.set_list.curselection()
        index = min(max((selected[0] if selected else 0) + direction, 0), len(self.anomaly_rows)-1)
        self.set_list.selection_clear(0, "end")
        self.set_list.selection_set(index)
        self.set_list.see(index)
        self._on_set_selected()

    def _on_set_selected(self, _event=None):
        selection = self.set_list.curselection()
        if not selection: return
        index = selection[0]
        self._show_selected_set(index)
        result = self.anomaly_rows[index]["correlation"]
        if result.get("zoom"):
            self.review_mode.set("Ver todos los SET")
            self._refresh_review_list(preferred_identity=self._set_identity(result))
        else:
            self.review_items = []
            self.review_list.delete(0, "end")
            self._show_current_review()
            record = self._record_for(result)
            details = record_details(record, result) + "\n\n" + self.anomaly_rows[index]["reason"]
            self.preview_label.configure(text=details, wraplength=500, justify="left")
            for key, title in (("elemento", "Elemento"), ("anomalia", "Anomalía"), ("criticidad", "Criticidad")):
                self.excel_highlights[key].configure(text=f"{title}: {record.get('original_data', {}).get(key) or 'Sin dato'}")
            self.review_details.configure(text=details)

    def _clear_set_details(self) -> None:
        self._set_preview_photos.clear()
        self._left_images.clear()
        self.set_details.configure(text="Sin SET disponibles")
        for label in self.set_preview_labels.values():
            label.configure(image="", text="NO ENCONTRADA")

    def _show_selected_set(self, index: int) -> None:
        row = self.anomaly_rows[index]
        correlation = row["correlation"]
        preview = row["preview"]
        self.set_details.configure(text=(
            f"{len(self.anomaly_rows)} anomalías activas · {len(self.anomaly_rows) * 3} imágenes esperadas\n"
            f"{correlation.get('set_id', 'SET')}\n{record_details(self._record_for(correlation), correlation)}\n"
            f"{row['label']} · {row['available']}/3 imágenes candidatas\n{row['reason']}"
        ))
        self._set_preview_photos.clear()
        for title, key, channel in (("Zoom", "zoom", "Z"), ("Wide", "wide", "W"), ("Thermal", "thermal", "T")):
            if preview:
                self._show_set_preview(title, preview[key], channel in preview["ambiguous_channels"])
            else:
                text = "SIN ZOOM VÁLIDA" if channel == "Z" else "SIN REFERENCIA ZOOM"
                self._left_images[title] = None
                self.set_preview_labels[title].configure(image="", text=text)

    def _show_set_preview(
        self,
        channel: str,
        image: dict[str, object] | None,
        ambiguous: bool,
    ) -> None:
        label = self.set_preview_labels[channel]
        self._left_images[channel] = None if ambiguous else image
        if ambiguous:
            label.configure(image="", text="AMBIGUA")
            return
        if image is None:
            label.configure(image="", text="NO ENCONTRADA")
            return
        try:
            with Image.open(image["path"]) as source:
                preview = ImageOps.exif_transpose(source)
                preview.thumbnail((95, 85), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(preview)
            self._set_preview_photos[channel] = photo
            label.configure(image=photo, text="")
        except (OSError, ValueError):
            label.configure(image="", text="No se pudo previsualizar")

    def _select_root_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Seleccionar carpeta raíz",
        )
        if selected:
            self._excluded_history = []
            self.finalize_button.configure(state="disabled")
            self.output_plan = None
            self.output_plan_summary.set("")
            self.zoom_results = []
            self.set_results = []
            self._refresh_set_list()
            self.pending_reviews = []
            self.review_index = 0
            self.review_active = False
            self._refresh_review_list()
            root_folder = Path(selected)
            validation_result = run_task(self.root, "Verificando carpeta", lambda report: validate_root_folder(root_folder))
            if not validation_result.startswith("Proyecto válido"):
                self.status.set(validation_result)
                return

            self.output_is_stale = False
            self._prepare_set_ids_for_root(root_folder)

            try:
                records = run_task(self.root, "Leyendo Excel", lambda report: read_project_excel(root_folder))
            except Exception:
                self.status.set(
                    "Proyecto no válido\n"
                    "No se pudo leer el Excel o no contiene registros bajo sus encabezados."
                )
                return

            try:
                images, duplicates = run_task(self.root, "Buscando imágenes", lambda report: inventory_project_images(root_folder))
            except Exception:
                self.status.set(
                    "Proyecto detenido\n"
                    "No se pudieron leer las imágenes de la carpeta seleccionada."
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

            parsed_images = run_task(self.root, "Interpretando imágenes", lambda report: parse_dji_images(images))
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
            self.zoom_results = run_task(self.root, "Asociando anomalías con imágenes", lambda report: match_zoom_images(records, parsed_images))
            self.pending_reviews = [
                result
                for result in self.zoom_results
                if result["status"] == "ZOOM_PENDING_REVIEW"
            ]
            self.review_index = 0
            self.review_active = True
            self._refresh_summary()
            self._refresh_review_list()

    @staticmethod
    def _time_color(seconds):
        return "#15803D" if abs(seconds) <= 5 else "#C06000" if abs(seconds) <= 10 else "#DC2626"

    def _wrap_buttons(self, parent):
        style = ttk.Style(self.root)
        style.configure("Busy.TButton", background="#FBBF24", foreground="#172033")
        style.map("Busy.TButton", background=[("disabled", "#FBBF24")], foreground=[("disabled", "#172033")])
        for widget in parent.winfo_children():
            if isinstance(widget, ttk.Button) and widget.cget("command"):
                command = widget.cget("command")
                def invoke(w=widget, cmd=command):
                    if getattr(w, "_busy_wrapped_running", False): return
                    w._busy_wrapped_running = True
                    old_text, old_style = w.cget("text"), w.cget("style")
                    w.configure(text="Procesando…", style="Busy.TButton", state="disabled")
                    self.root.update_idletasks()
                    def execute():
                        try: self.root.tk.call(cmd)
                        finally:
                            try:
                                exists = bool(w.winfo_exists())
                            except tk.TclError:
                                exists = False
                            if exists:
                                w.configure(text=old_text, style=old_style, state="normal")
                                w._busy_wrapped_running = False
                                self._update_finalize_state()
                                self._update_use_zoom_state()
                                if w in (self.accept_button, self.reject_button) and not self.review_items:
                                    w.configure(state="disabled")
                    self.root.after(30, execute)
                widget.configure(command=invoke)
            self._wrap_buttons_children(widget)

    def _wrap_buttons_children(self, widget):
        if widget.winfo_children(): self._wrap_buttons(widget)

    def _open_image_popup(self, image):
        if not image: return
        try:
            with Image.open(image["path"]) as file:
                source = ImageOps.exif_transpose(file).copy()
        except (OSError, ValueError) as error:
            messagebox.showerror("Imagen no disponible", str(error), parent=self.root)
            return
        popup = tk.Toplevel(self.root)
        popup.title(image["name"] + " · Rueda: zoom · Arrastrar: mover · Doble clic / Esc: cerrar")
        popup.state("zoomed")
        canvas = tk.Canvas(popup, background="#111827", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        view = {"zoom": 1.0, "x": 0, "y": 0, "drag": None}
        def render(event=None):
            width, height = max(1,canvas.winfo_width()), max(1,canvas.winfo_height())
            scale = min(width/source.width, height/source.height)*view["zoom"]
            # Recortar antes de ampliar evita crear bitmaps enormes al hacer zoom.
            left = width/2 + view["x"] - source.width*scale/2
            top = height/2 + view["y"] - source.height*scale/2
            photo = source.transform((width,height), Image.Transform.AFFINE,
                (1/scale,0,-left/scale,0,1/scale,-top/scale), Image.Resampling.BICUBIC)
            canvas._photo = ImageTk.PhotoImage(photo)
            canvas.delete("all");canvas.create_image(0,0,anchor="nw",image=canvas._photo)
        def wheel(event):
            old=view["zoom"]
            view["zoom"]=min(16,max(1,old*(1.2 if event.delta>0 else 1/1.2)))
            ratio=view["zoom"]/old
            view["x"]=(event.x-canvas.winfo_width()/2)*(1-ratio)+view["x"]*ratio
            view["y"]=(event.y-canvas.winfo_height()/2)*(1-ratio)+view["y"]*ratio
            if view["zoom"]==1: view["x"]=view["y"]=0
            render()
        def drag(event):
            if view["drag"]:
                x,y=view["drag"];view["x"]+=event.x-x;view["y"]+=event.y-y
                view["drag"]=(event.x,event.y);render()
        canvas.bind("<Configure>",render)
        canvas.bind("<MouseWheel>",wheel)
        canvas.bind("<ButtonPress-1>",lambda e:view.update(drag=(e.x,e.y)))
        canvas.bind("<B1-Motion>",drag)
        canvas.bind("<ButtonRelease-1>",lambda e:view.update(drag=None))
        canvas.bind("<Double-Button-1>",lambda e:popup.destroy())
        popup.bind("<Escape>",lambda e:popup.destroy())
        popup.focus_set()

    def _exclude_set(self):
        selection=self.set_list.curselection()
        if not selection: return
        result=self.anomaly_rows[selection[0]]["correlation"]
        self._excluded_history.append((result, result["status"]))
        result["status"]="EXCLUDED"
        self.output_plan=None; self.output_plan_summary.set("")
        self._refresh_summary(); self._refresh_review_list()
        if self.anomaly_rows: self._on_set_selected()

    def _undo_exclude(self):
        if not self._excluded_history: return
        result,status=self._excluded_history.pop();result["status"]=status
        self._refresh_summary()
        self._sync_left(self._set_identity(result));self._on_set_selected()
        self.output_plan=None;self.output_plan_summary.set("")

    def _record_for(self, result):
        return next((row for row in self._records if self._set_identity(row) == self._set_identity(result)), {})

    def _pick_project_zoom(self):
        if not self.review_active:
            messagebox.showinfo("Cargar proyecto", "Seleccione primero una carpeta raíz válida.", parent=self.root)
            return None
        selected = filedialog.askopenfilename(parent=self.root, title="Seleccionar imagen Zoom (_Z.JPG)", initialdir=str(self.current_root), filetypes=[("Imagen JPG", "*.jpg *.JPG")])
        if not selected:
            return None
        path = Path(selected).resolve()
        image = next((item for item in self._parsed_images if Path(item["path"]).resolve() == path), None)
        if not image or not image.get("recognized") or image.get("channel") != "Z" or not path.is_file():
            messagebox.showerror("Zoom no válida", "Seleccione una Zoom DJI del proyecto cargado. Si acaba de agregar archivos a la carpeta, vuelva a cargarla antes de iniciar la revisión.", parent=self.root)
            return None
        return image

    def _manual_changed(self, result):
        self.zoom_results.sort(key=lambda row: (row.get("normalized_date") or "99999999", row.get("target_time") or "999999"))
        self.pending_reviews = [row for row in self.zoom_results if row.get("zoom") and row["status"] != "ZOOM_EXACT"]
        self.output_plan = None
        self.output_plan_summary.set("")
        self._refresh_summary()
        self.review_mode.set("Ver todos los SET")
        self._refresh_review_list(preferred_identity=self._set_identity(result))

    def _link_manual_zoom(self):
        selection = self.set_list.curselection()
        if not selection:
            messagebox.showinfo("Seleccionar SET", "Seleccione primero el registro de la lista izquierda.", parent=self.root)
            return
        result = self.anomaly_rows[selection[0]]["correlation"]
        image = self._pick_project_zoom()
        if image is None:
            return
        try:
            link_zoom(result, image)
        except ValueError as error:
            messagebox.showerror("Referencia inválida", str(error), parent=self.root)
            return
        self._manual_changed(result)

    def _add_manual_set(self):
        image = self._pick_project_zoom()
        if image is None:
            return
        if any(row.get("zoom", {}).get("path") == image["path"] for row in self.zoom_results):
            messagebox.showinfo("SET existente", "Esta Zoom ya está vinculada a un registro. Selecciónelo en la lista para revisarlo.", parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Agregar SET manual")
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=image["name"]).grid(row=0, columnspan=2, pady=8)
        values = {}
        for index, (key, title) in enumerate((("element", "Elemento"), ("anomaly", "Anomalía"), ("criticality", "Criticidad"), ("line", "Línea"), ("stage", "Etapa")), 1):
            ttk.Label(body, text=title).grid(row=index, column=0, sticky="w", padx=8, pady=5)
            values[key] = tk.StringVar()
            if key == "criticality":
                field = ttk.Combobox(body, textvariable=values[key], values=("Baja", "Media", "Alta", "Urgente"), state="readonly")
            else:
                field = ttk.Entry(body, textvariable=values[key], width=44)
            field.grid(row=index, column=1, sticky="ew")
        def add():
            data = {key: value.get().strip() for key, value in values.items()}
            if not all(data.values()):
                messagebox.showerror("Datos incompletos", "Complete los cinco campos para identificar la anomalía.", parent=dialog)
                return
            number = 1 + sum(bool(row.get("manual_added")) for row in self._records)
            record = manual_record(image, data, number)
            result = match_zoom_images([record], [image])[0]
            result["manual_added"] = True
            link_zoom(result, image)
            self._records.append(record)
            self.zoom_results.append(result)
            dialog.destroy()
            self._manual_changed(result)
        ttk.Button(body, text="Agregar y revisar", command=add).grid(row=6, column=1, pady=12)
        ttk.Button(body, text="Cancelar", command=dialog.destroy).grid(row=6, column=0)
        self._center_dialog(dialog)
        self._wrap_buttons(dialog)
        dialog.grab_set()

    def _open_coordinate_search(self) -> None:
        """Abre el flujo GPS autónomo sin modificar el proyecto en curso."""
        CoordinateSearchWindow(self.root)

    def run(self) -> None:
        self.root.mainloop()
