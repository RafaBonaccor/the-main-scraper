import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import quote_plus

from .browser_runtime import (
    browser_mode_requires_custom_dir,
    browser_mode_uses_profile,
    default_chrome_user_data_dir,
    inspect_persistent_profile,
    normalize_browser_mode,
)
from .contact_history import annotate_rows_with_contact_history, summarize_contact_status
from .date_filter import describe_age_days, to_datetime_for_sorting
from .job_profiles import (
    is_builtin_job_profile,
    load_job_profiles,
    normalize_job_keywords,
    parse_job_keywords,
    save_custom_job_profile,
    delete_custom_job_profile,
)
from .runtime_controls import (
    clear_runtime_control_requests,
    request_skip_current_item,
    request_stop_after_current_item,
    request_vinted_login_confirmed,
)
from .utils import build_google_maps_search_url, build_subito_search_url
from .vinted_browser_session import get_active_vinted_browser_session
from .vinted_database import load_vinted_rows


APP_BG = "#f4f6f8"
PANEL_BG = "#ffffff"
ACCENT = "#0f172a"
ACCENT_SOFT = "#dbe4ee"
BUTTON_BG = "#2563eb"
BUTTON_FG = "#ffffff"
TEXT = "#111827"
MUTED = "#667085"
DECISION_ORDER = {"accepted": 0, "maybe": 1, "rejected": 2}
CONTACT_STATUS_LABELS = {
    "new": "nuovo",
    "prepared": "preparato",
    "submitted": "inviato",
    "failed": "fallito",
}
CONTACT_STATUS_SORT_ORDER = {
    "new": 0,
    "prepared": 1,
    "failed": 2,
    "submitted": 3,
}
SCREENING_DECISION_LABELS = {
    "candida": "candida",
    "valuta": "valuta",
    "no": "no",
}
SCREENING_DECISION_ORDER = {
    "candida": 0,
    "valuta": 1,
    "no": 2,
}
LEAD_PRIORITY_ORDER = {"alta": 0, "media": 1, "bassa": 2}
RESULT_SORT_OPTIONS = (
    "Prezzo Vinted",
    "Ricerca Vinted",
    "Preferiti Vinted",
    "Valutazione Vinted",
    "Score opportunita",
    "Priorita lead",
    "Nome attivita",
    "Categoria Maps",
    "Valutazione Maps",
    "Numero recensioni",
    "Priorita consigliata",
    "Score candidatura",
    "Contatto",
    "Decisione geo",
    "Data annuncio",
    "Data estrazione",
    "Distanza",
    "Luogo",
    "Titolo",
    "Azienda",
    "Orario",
)
RESULT_SORT_COLUMN_MAP = {
    "price_value": "Prezzo Vinted",
    "search_term": "Ricerca Vinted",
    "favorite_count": "Preferiti Vinted",
    "evaluation_label": "Valutazione Vinted",
    "opportunity_score": "Score opportunita",
    "lead_priority": "Priorita lead",
    "name": "Nome attivita",
    "category": "Categoria Maps",
    "rating": "Valutazione Maps",
    "reviews_count": "Numero recensioni",
    "screening_decision": "Priorita consigliata",
    "screening_score": "Score candidatura",
    "contact_status": "Contatto",
    "decision": "Decisione geo",
    "published_at": "Data annuncio",
    "extracted_at": "Data estrazione",
    "distance_km": "Distanza",
    "location": "Luogo",
    "title": "Titolo",
    "company": "Azienda",
    "schedule": "Orario",
}
RESULT_SORT_DEFAULT_DESC = {
    "Prezzo Vinted": False,
    "Ricerca Vinted": False,
    "Preferiti Vinted": False,
    "Valutazione Vinted": False,
    "Score opportunita": True,
    "Priorita lead": False,
    "Nome attivita": False,
    "Categoria Maps": False,
    "Valutazione Maps": True,
    "Numero recensioni": True,
    "Priorita consigliata": False,
    "Score candidatura": True,
    "Contatto": False,
    "Decisione geo": False,
    "Data annuncio": True,
    "Data estrazione": True,
    "Distanza": False,
    "Luogo": False,
    "Titolo": False,
    "Azienda": False,
    "Orario": False,
}
RESULT_SORT_MODE_DEFAULT_COLUMN = {
    "Prezzo Vinted": "price_value",
    "Ricerca Vinted": "search_term",
    "Preferiti Vinted": "favorite_count",
    "Valutazione Vinted": "evaluation_label",
    "Score opportunita": "opportunity_score",
    "Priorita lead": "lead_priority",
    "Nome attivita": "name",
    "Categoria Maps": "category",
    "Valutazione Maps": "rating",
    "Numero recensioni": "reviews_count",
    "Priorita consigliata": "screening_decision",
    "Score candidatura": "screening_score",
    "Contatto": "contact_status",
    "Decisione geo": "decision",
    "Data annuncio": "published_at",
    "Data estrazione": "extracted_at",
    "Distanza": "distance_km",
    "Luogo": "location",
    "Titolo": "title",
    "Azienda": "company",
    "Orario": "schedule",
}
SUBITO_JOB_OPTIONS = (
    ("pulizie", "Pulizie"),
    ("colf", "Colf"),
    ("badante", "Badante"),
    ("assistente familiare", "Assistente familiare"),
    ("domestica", "Domestica"),
    ("baby sitter", "Baby sitter"),
    ("governante", "Governante"),
)
SUBITO_NEARBY_CITY_OPTIONS = (
    "roma",
    "morlupo",
    "fiano romano",
    "rignano flaminio",
    "monterotondo",
    "capena",
    "riano",
    "castelnuovo di porto",
    "sacrofano",
    "formello",
    "campagnano di roma",
)


class VerticalScrolledFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, background: str) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self)
        self.window_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        for widget in (self, self.canvas, self.body):
            widget.bind("<Enter>", self._bind_mousewheel, add="+")
            widget.bind("<Leave>", self._unbind_mousewheel, add="+")

    def _on_body_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


def detect_default_attachment_path(project_root: Path) -> str:
    attachment_dir = project_root / "allegato"
    if not attachment_dir.exists():
        return ""

    candidates = [
        path
        for path in attachment_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".doc", ".docx"}
    ]
    if not candidates:
        return ""

    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return str(latest.resolve())


class ScraperApp:
    def __init__(self, root: tk.Tk, script_path: Path) -> None:
        self.root = root
        self.script_path = script_path
        self.process: subprocess.Popen[str] | None = None
        self.process_kind = ""
        self.process_should_load_results = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.current_run_source = ""
        self.result_rows: list[dict] = []
        self.result_row_lookup: dict[str, dict] = {}
        self.ui_result_json_path = (script_path.parent / "output" / "_ui_last_result.json").resolve()
        self.ui_result_json_mtime = 0.0

        self.google_search_var = tk.StringVar(value="avvocati")
        self.google_city_var = tk.StringVar()
        self.google_province_var = tk.StringVar()
        self.google_country_var = tk.StringVar(value="Italia")
        self.google_max_results_var = tk.StringVar(value="25")
        self.google_exclude_sponsored_var = tk.BooleanVar(value=True)
        self.google_include_details_var = tk.BooleanVar(value=True)
        self.google_audit_websites_var = tk.BooleanVar(value=True)
        self.google_website_timeout_var = tk.StringVar(value="10")

        self.vinted_search_var = tk.StringVar(value="macbook")
        self.vinted_max_results_var = tk.StringVar(value="100")
        self.vinted_db_path_var = tk.StringVar(value=str((script_path.parent / "data" / "scraper.db").resolve()))
        self.vinted_db_filter_var = tk.StringVar()
        self.vinted_db_limit_var = tk.StringVar(value="500")
        self.vinted_signal_filter_var = tk.StringVar(value="tutti")
        self.vinted_keep_browser_open_var = tk.BooleanVar(value=True)
        self.vinted_keep_open_seconds_var = tk.StringVar(value="0")
        self.vinted_refresh_browser_profile_var = tk.BooleanVar(value=False)
        self.vinted_search_preview_var = tk.StringVar()
        self.vinted_status_var = tk.StringVar(value="Pronto per una nuova ricerca Vinted.")
        self.vinted_profile_session_var = tk.StringVar(value="Controllo in corso...")
        self.vinted_profile_cookies_var = tk.StringVar(value="-")
        self.vinted_profile_last_import_var = tk.StringVar(value="-")
        self.vinted_profile_access_var = tk.StringVar(value="Non ancora verificato sul sito.")

        self.subito_query_var = tk.StringVar()
        self.subito_region_var = tk.StringVar(value="lazio")
        self.subito_city_var = tk.StringVar(value="roma")
        self.subito_category_var = tk.StringVar(value="offerte-lavoro")
        self.subito_anchor_place_var = tk.StringVar(value="Morlupo")
        self.subito_max_distance_var = tk.StringVar(value="30")
        self.subito_max_age_hours_var = tk.StringVar(value="")
        self.subito_max_age_days_var = tk.StringVar(value="14")
        self.subito_exact_age_days_var = tk.StringVar(value="")
        self.subito_auto_interval_hours_var = tk.StringVar(value="6")
        self.subito_nearby_only_var = tk.BooleanVar(value=False)
        self.subito_include_details_var = tk.BooleanVar(value=False)
        self.subito_llm_screening_var = tk.BooleanVar(value=False)
        self.subito_openai_model_var = tk.StringVar(value="gpt-5.5")
        self.subito_max_results_var = tk.StringVar(value="25")
        self.subito_custom_job_keywords_var = tk.StringVar()
        self.subito_profile_name_var = tk.StringVar()
        self.subito_profiles_info_var = tk.StringVar(value="Keyword attive: nessuna. Seleziona lavori, profili o keyword extra.")
        self.subito_job_profiles: dict[str, list[str]] = {}

        self.custom_url_var = tk.StringVar()
        self.custom_item_selector_var = tk.StringVar(value="article")
        self.custom_name_selector_var = tk.StringVar()
        self.custom_phone_selector_var = tk.StringVar()
        self.custom_link_selector_var = tk.StringVar(value="a[href]")
        self.custom_cookie_texts_var = tk.StringVar(value="Continua senza accettare,Rifiuta tutto,Rifiuta")

        self.browser_mode_var = tk.StringVar(value="chrome_normale")
        self.browser_user_data_dir_var = tk.StringVar(value=default_chrome_user_data_dir())
        self.browser_profile_directory_var = tk.StringVar(value="Default")
        self.slow_mode_var = tk.BooleanVar(value=True)
        self.action_delay_seconds_var = tk.StringVar(value="2.5")
        self.page_settle_seconds_var = tk.StringVar(value="4.0")

        self.output_format_var = tk.StringVar(value="all")
        self.output_dir_var = tk.StringVar(value=str((script_path.parent / "output").resolve()))
        self.filename_var = tk.StringVar()
        self.attachment_path_var = tk.StringVar(value=detect_default_attachment_path(script_path.parent))
        self.contact_message_var = tk.StringVar(value="Buongiorno, allego il mio curriculum per la posizione. Grazie per l'attenzione.")
        self.contact_submit_var = tk.BooleanVar(value=False)
        self.contact_keep_open_seconds_var = tk.StringVar(value="120")
        self.status_var = tk.StringVar(value="Idle")
        self.hint_var = tk.StringVar()
        self.active_source_var = tk.StringVar(value="Google Maps")
        self.auto_monitor_status_var = tk.StringVar(value="Monitor automatico disattivato")
        self.result_sort_var = tk.StringVar(value="Score opportunita")
        self.result_total_var = tk.StringVar(value="0 risultati")
        self.result_counts_var = tk.StringVar(value="accepted 0 | maybe 0 | rejected 0")
        self.result_meta_var = tk.StringVar(value="Nessun output caricato")
        self.detail_title_var = tk.StringVar(value="Nessun lead selezionato")
        self.detail_source_var = tk.StringVar(value="-")
        self.detail_screening_var = tk.StringVar(value="-")
        self.detail_contact_var = tk.StringVar(value="-")
        self.detail_decision_var = tk.StringVar(value="-")
        self.detail_date_var = tk.StringVar(value="-")
        self.detail_distance_var = tk.StringVar(value="-")
        self.detail_location_var = tk.StringVar(value="-")
        self.detail_company_var = tk.StringVar(value="-")
        self.detail_sector_var = tk.StringVar(value="-")
        self.detail_role_var = tk.StringVar(value="-")
        self.detail_schedule_var = tk.StringVar(value="-")
        self.detail_price_var = tk.StringVar(value="-")
        self.detail_link_var = tk.StringVar(value="")
        self.detail_website_var = tk.StringVar(value="")
        self.auto_monitor_enabled = False
        self.auto_monitor_after_id: str | None = None
        self.auto_monitor_command: list[str] | None = None
        self.auto_monitor_interval_ms = 0
        self.current_results_generated_at = ""
        self.current_result_meta: dict[str, object] = {}
        self.vinted_access_warning_shown_for_process = False
        self.vinted_login_prompt_open = False
        self.vinted_last_access_marker_present: bool | None = None
        self.result_sort_reverse = RESULT_SORT_DEFAULT_DESC.get("Score opportunita", True)
        self.result_sort_active_column = RESULT_SORT_MODE_DEFAULT_COLUMN.get("Score opportunita", "")
        self._updating_result_sort_var = False
        self.results_column_labels: dict[str, str] = {}
        self.current_result_source = "google_maps"

        self.root.title("The Main Scraper")
        self.root.geometry("1360x920")
        self.root.minsize(1040, 780)
        self.root.configure(bg=APP_BG)

        self._configure_styles()
        self._build()
        self._refresh_subito_saved_profiles()
        self._update_subito_job_keywords_preview()
        self._update_hint()
        self._update_browser_mode()
        self._update_result_actions()
        self._update_vinted_profile_status()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._sync_active_source_from_notebook())
        self.browser_mode_var.trace_add("write", lambda *_: self._update_browser_mode())
        self.browser_mode_var.trace_add("write", lambda *_: self._update_result_actions())
        self.browser_mode_var.trace_add("write", lambda *_: self._update_vinted_profile_status())
        self.browser_user_data_dir_var.trace_add("write", lambda *_: self._update_vinted_profile_status())
        self.browser_profile_directory_var.trace_add("write", lambda *_: self._update_vinted_profile_status())
        self.vinted_refresh_browser_profile_var.trace_add("write", lambda *_: self._update_vinted_profile_status())
        self.subito_custom_job_keywords_var.trace_add("write", lambda *_: self._update_subito_job_keywords_preview())
        self.result_sort_var.trace_add("write", lambda *_: self._handle_result_sort_change())
        self.vinted_search_var.trace_add("write", lambda *_: self._update_vinted_search_preview())
        self._update_vinted_search_preview()
        self.root.after(150, self._drain_logs)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=APP_BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("App.TFrame", background=APP_BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Card.TLabelframe", background=PANEL_BG, bordercolor=ACCENT_SOFT, borderwidth=1, relief="solid")
        style.configure("Card.TLabelframe.Label", background=PANEL_BG, foreground=ACCENT, font=("Segoe UI Semibold", 10))
        style.configure("Header.TLabel", background=APP_BG, foreground=ACCENT, font=("Segoe UI Semibold", 21))
        style.configure("Subtitle.TLabel", background=APP_BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Hint.TLabel", background=PANEL_BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#e7f0ff", foreground="#1d4ed8", font=("Segoe UI Semibold", 10), padding=(12, 7))
        style.configure("Run.TButton", background=BUTTON_BG, foreground=BUTTON_FG, padding=(16, 11), font=("Segoe UI Semibold", 10))
        style.map("Run.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])
        style.configure("Accent.TButton", background="#0f766e", foreground="#ffffff", padding=(14, 10), font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#115e59"), ("pressed", "#134e4a")])
        style.configure("Secondary.TButton", background="#eef2f7", foreground=TEXT, padding=(12, 9), font=("Segoe UI", 10))
        style.map("Secondary.TButton", background=[("active", "#e2e8f0"), ("pressed", "#cbd5e1")])
        style.configure("TNotebook", background=APP_BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), background="#e9eef5", foreground=TEXT, font=("Segoe UI Semibold", 10))
        style.map("TNotebook.Tab", background=[("selected", PANEL_BG)], foreground=[("selected", ACCENT)])
        style.configure("Treeview", rowheight=30, background="#ffffff", fieldbackground="#ffffff", bordercolor=ACCENT_SOFT)
        style.configure("Treeview.Heading", background="#eef2f7", foreground=ACCENT, font=("Segoe UI Semibold", 9))
        style.map("Treeview.Heading", background=[("active", "#e2e8f0")])
        style.configure("Metric.TLabel", background=PANEL_BG, foreground=ACCENT, font=("Segoe UI Semibold", 11))
        style.configure("Muted.TLabel", background=PANEL_BG, foreground=MUTED, font=("Segoe UI", 9))

    def _build(self) -> None:
        container = ttk.Frame(self.root, style="App.TFrame", padding=20)
        container.pack(fill="both", expand=True)
        header = ttk.Frame(container, style="App.TFrame")
        header.pack(fill="x")
        left = ttk.Frame(header, style="App.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="The Main Scraper", style="Header.TLabel").pack(anchor="w")
        ttk.Label(left, text="Google Maps, Vinted e Subito in una sola interfaccia.", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side="right", anchor="ne")

        self.page_scroll = VerticalScrolledFrame(container, background=APP_BG)
        self.page_scroll.pack(fill="both", expand=True, pady=(14, 0))
        config_content = self.page_scroll.body
        config_content.configure(style="App.TFrame")

        hint = ttk.Frame(config_content, style="Panel.TFrame", padding=12)
        hint.pack(fill="x", pady=(0, 12))
        tk.Label(hint, textvariable=self.hint_var, bg=PANEL_BG, fg=TEXT, wraplength=1120, justify="left", font=("Segoe UI", 10)).pack(anchor="w")

        self.notebook = ttk.Notebook(config_content)
        self.notebook.pack(fill="x", pady=(0, 12))
        self.google_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=16)
        self.vinted_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=16)
        self.subito_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=16)
        self.custom_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=16)
        self.notebook.add(self.google_tab, text="Google Maps")
        self.notebook.add(self.vinted_tab, text="Vinted")
        self.notebook.add(self.subito_tab, text="Subito Jobs")
        self.notebook.add(self.custom_tab, text="Custom Site")
        self._build_google_tab()
        self._build_vinted_tab()
        self._build_subito_tab()
        self._build_custom_tab()
        self._sync_active_source_from_notebook()

        controls = ttk.Frame(config_content, style="App.TFrame")
        controls.pack(fill="x", pady=(12, 0))
        browser_shell = ttk.Frame(controls, style="App.TFrame")
        export_shell = ttk.Frame(controls, style="App.TFrame")
        action_shell = ttk.Frame(controls, style="App.TFrame")
        browser_shell.pack(side="left", fill="both", expand=True, padx=(0, 8))
        export_shell.pack(side="left", fill="both", expand=True, padx=8)
        action_shell.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._build_browser_card(browser_shell)
        self._build_export_card(export_shell)
        self._build_action_card(action_shell)

        self.results_tab = ttk.Frame(config_content, style="App.TFrame")
        self.results_tab.pack(fill="x", pady=(12, 12))
        self.log_tab = ttk.Frame(config_content, style="App.TFrame")
        self.log_tab.pack(fill="x")
        self._build_results_tab()
        self._build_log_tab()

    def _build_google_tab(self) -> None:
        self.google_scroll = VerticalScrolledFrame(self.google_tab, background=APP_BG)
        self.google_scroll.pack(fill="both", expand=True)
        self.google_scroll.body.configure(style="Panel.TFrame")

        card = self._card(self.google_scroll.body, "Google Maps")
        card.pack(fill="x")
        self._row(card, 0, "Categorie o URL", self.google_search_var)
        self._row(card, 1, "Citta o zone", self.google_city_var, 30)
        self._row(card, 2, "Provincia", self.google_province_var, 30)
        self._row(card, 3, "Paese", self.google_country_var, 30)
        self._row(card, 4, "Max per ricerca", self.google_max_results_var, 12)
        ttk.Checkbutton(
            card,
            text="Escludi risultati sponsorizzati, spesso fuori dalla zona richiesta",
            variable=self.google_exclude_sponsored_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            card,
            text="Apri ogni attivita per estrarre sito, categoria, telefono, rating e recensioni",
            variable=self.google_include_details_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            card,
            text="Analizza i siti trovati per email pubbliche, social e qualita tecnica di base",
            variable=self.google_audit_websites_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(card, text="Timeout audit sito (sec)").grid(row=8, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.google_website_timeout_var, width=12).grid(row=8, column=1, sticky="w", padx=(10, 0))
        ttk.Label(
            card,
            text="Separa categorie e citta con virgole. Esempio: ristoranti, dentisti / Roma, Monterotondo.",
            style="Hint.TLabel",
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_vinted_tab(self) -> None:
        self.vinted_scroll = VerticalScrolledFrame(self.vinted_tab, background=APP_BG)
        self.vinted_scroll.pack(fill="both", expand=True)
        self.vinted_scroll.body.configure(style="Panel.TFrame")

        card = self._card(self.vinted_scroll.body, "Vinted")
        card.pack(fill="x")
        self._row(card, 0, "Ricerca o URL Vinted", self.vinted_search_var)
        self._row(card, 1, "Max risultati (0 = tutti)", self.vinted_max_results_var, 12)

        ttk.Label(card, text="URL che verra aperto", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(2, 8))
        ttk.Label(
            card,
            textvariable=self.vinted_search_preview_var,
            style="Hint.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=2, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(2, 8))

        search_actions = ttk.Frame(card, style="Panel.TFrame")
        search_actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(2, 12))
        self.vinted_run_button = ttk.Button(
            search_actions,
            text="Avvia ricerca Vinted",
            style="Run.TButton",
            command=self._start_vinted_search,
        )
        self.vinted_run_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            search_actions,
            text="Apri ricerca nel browser",
            style="Secondary.TButton",
            command=self._start_vinted_browser,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        keep_open_controls = ttk.Frame(search_actions, style="Panel.TFrame")
        keep_open_controls.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            keep_open_controls,
            text="Tieni browser aperto dopo la ricerca",
            variable=self.vinted_keep_browser_open_var,
        ).grid(row=0, column=0, sticky="w")
        ttk.Entry(keep_open_controls, textvariable=self.vinted_keep_open_seconds_var, width=8).grid(row=0, column=1, sticky="w", padx=(10, 4))
        ttk.Label(keep_open_controls, text="secondi (0 = non chiudere)", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(
            keep_open_controls,
            text="Ricarica login dal Chrome reale",
            variable=self.vinted_refresh_browser_profile_var,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.vinted_extract_button = ttk.Button(
            search_actions,
            text="Estrai descrizioni selezionati",
            style="Accent.TButton",
            command=self._start_vinted_description_extraction_selected,
        )
        self.vinted_extract_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.vinted_extract_button.configure(state="disabled")
        search_actions.columnconfigure(0, weight=2)
        search_actions.columnconfigure(1, weight=1)

        ttk.Separator(card, orient="horizontal").grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(card, text="Archivio database", style="Metric.TLabel").grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._row(card, 6, "Database SQLite", self.vinted_db_path_var)
        ttk.Button(
            card,
            text="Scegli DB",
            style="Secondary.TButton",
            command=self._choose_vinted_db_path,
        ).grid(row=6, column=2, sticky="e", padx=(8, 0), pady=(0, 10))
        self._row(card, 7, "Filtro ricerca nel DB", self.vinted_db_filter_var, 30)
        ttk.Label(card, text="Filtro rapido risultati").grid(row=8, column=0, sticky="w")
        vinted_signal_filter_box = ttk.Combobox(
            card,
            textvariable=self.vinted_signal_filter_var,
            values=("tutti", "ricercato", "da valutare", "da valutare assolutamente"),
            state="readonly",
            width=28,
        )
        vinted_signal_filter_box.grid(row=8, column=1, sticky="w", padx=(10, 8), pady=(0, 8))
        vinted_signal_filter_box.bind("<<ComboboxSelected>>", lambda _event: self._handle_vinted_signal_filter_change())
        ttk.Button(
            card,
            text="Applica",
            style="Secondary.TButton",
            command=self._handle_vinted_signal_filter_change,
        ).grid(row=8, column=2, sticky="e", pady=(0, 8))
        self._row(card, 9, "Limite righe DB (0 = tutte)", self.vinted_db_limit_var, 12)

        db_actions = ttk.Frame(card, style="Panel.TFrame")
        db_actions.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        ttk.Button(
            db_actions,
            text="Mostra database nei risultati",
            style="Accent.TButton",
            command=self._load_vinted_database_results,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 8))
        ttk.Button(
            db_actions,
            text="Apri cartella database",
            style="Secondary.TButton",
            command=self._open_vinted_db_folder,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 8))
        ttk.Button(
            db_actions,
            text="Ripristina campi",
            style="Secondary.TButton",
            command=self._reset_vinted_form,
        ).grid(row=1, column=0, columnspan=2, sticky="ew")
        db_actions.columnconfigure(0, weight=1)
        db_actions.columnconfigure(1, weight=1)

        ttk.Label(
            card,
            textvariable=self.vinted_status_var,
            style="Metric.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Label(
            card,
            text="I risultati vengono mostrati automaticamente nella sezione Risultati, esportati con le impostazioni globali e salvati nel database senza duplicare gli articoli.",
            style="Hint.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=12, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Separator(card, orient="horizontal").grid(row=13, column=0, columnspan=3, sticky="ew", pady=(14, 10))
        ttk.Label(card, text="Browser e export Vinted", style="Metric.TLabel").grid(row=14, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(card, text="Sessione browser").grid(row=15, column=0, sticky="w", pady=(0, 8))
        ttk.Combobox(
            card,
            textvariable=self.browser_mode_var,
            values=("sessione_persistente", "chrome_normale", "profilo_personalizzato", "isolated"),
            state="readonly",
            width=24,
        ).grid(row=15, column=1, sticky="w", padx=(10, 8), pady=(0, 8))
        ttk.Checkbutton(card, text="Connessione lenta", variable=self.slow_mode_var).grid(row=15, column=2, sticky="w", pady=(0, 8))

        ttk.Label(card, text="Chrome User Data").grid(row=16, column=0, sticky="w", pady=(0, 8))
        self.vinted_browser_user_data_entry = ttk.Entry(card, textvariable=self.browser_user_data_dir_var, width=54)
        self.vinted_browser_user_data_entry.grid(row=16, column=1, sticky="ew", padx=(10, 8), pady=(0, 8))
        self.vinted_browser_browse_button = ttk.Button(card, text="Sfoglia", style="Secondary.TButton", command=self._choose_browser_user_data_dir)
        self.vinted_browser_browse_button.grid(row=16, column=2, sticky="e", pady=(0, 8))

        ttk.Label(card, text="Profile Directory").grid(row=17, column=0, sticky="w", pady=(0, 8))
        self.vinted_browser_profile_dir_entry = ttk.Entry(card, textvariable=self.browser_profile_directory_var, width=18)
        self.vinted_browser_profile_dir_entry.grid(row=17, column=1, sticky="w", padx=(10, 8), pady=(0, 8))
        timing_frame = ttk.Frame(card, style="Panel.TFrame")
        timing_frame.grid(row=17, column=2, sticky="ew", pady=(0, 8))
        ttk.Label(timing_frame, text="Pausa").grid(row=0, column=0, sticky="w")
        ttk.Entry(timing_frame, textvariable=self.action_delay_seconds_var, width=6).grid(row=0, column=1, sticky="w", padx=(4, 8))
        ttk.Label(timing_frame, text="Attesa").grid(row=0, column=2, sticky="w")
        ttk.Entry(timing_frame, textvariable=self.page_settle_seconds_var, width=6).grid(row=0, column=3, sticky="w", padx=(4, 0))

        ttk.Label(card, text="Stato sessione").grid(row=18, column=0, sticky="nw", pady=(0, 8))
        profile_status_frame = ttk.Frame(card, style="Panel.TFrame")
        profile_status_frame.grid(row=18, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=(0, 8))
        profile_status_frame.columnconfigure(1, weight=1)
        ttk.Label(profile_status_frame, text="Sessione").grid(row=0, column=0, sticky="w")
        ttk.Label(
            profile_status_frame,
            textvariable=self.vinted_profile_session_var,
            style="Metric.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(profile_status_frame, text="Cookie").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            profile_status_frame,
            textvariable=self.vinted_profile_cookies_var,
            wraplength=520,
            justify="left",
        ).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(4, 0))
        ttk.Label(profile_status_frame, text="Ultima importazione").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            profile_status_frame,
            textvariable=self.vinted_profile_last_import_var,
            wraplength=520,
            justify="left",
        ).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(4, 0))
        ttk.Label(profile_status_frame, text="Accesso Vinted").grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            profile_status_frame,
            textvariable=self.vinted_profile_access_var,
            wraplength=520,
            justify="left",
        ).grid(row=3, column=1, sticky="w", padx=(10, 0), pady=(4, 0))
        ttk.Button(
            profile_status_frame,
            text="Aggiorna stato",
            style="Secondary.TButton",
            command=self._update_vinted_profile_status,
        ).grid(row=0, column=2, rowspan=4, sticky="ne", padx=(12, 0))

        ttk.Label(card, text="Export").grid(row=19, column=0, sticky="w", pady=(0, 8))
        export_frame = ttk.Frame(card, style="Panel.TFrame")
        export_frame.grid(row=19, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=(0, 8))
        ttk.Combobox(export_frame, textvariable=self.output_format_var, values=("json", "csv", "xlsx", "all"), state="readonly", width=10).grid(row=0, column=0, sticky="w")
        ttk.Label(export_frame, text="Nome").grid(row=0, column=1, sticky="w", padx=(12, 4))
        ttk.Entry(export_frame, textvariable=self.filename_var, width=24).grid(row=0, column=2, sticky="ew")
        export_frame.columnconfigure(2, weight=1)

        ttk.Label(card, text="Cartella output").grid(row=20, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(card, textvariable=self.output_dir_var, width=54).grid(row=20, column=1, sticky="ew", padx=(10, 8), pady=(0, 8))
        ttk.Button(card, text="Sfoglia", style="Secondary.TButton", command=self._choose_output_dir).grid(row=20, column=2, sticky="e", pady=(0, 8))
        ttk.Label(
            card,
            text="Per Vinted usa sessione_persistente se vuoi mantenere login/cookie tra una ricerca e l altra. Attiva 'Ricarica login dal Chrome reale' solo quando vuoi importare di nuovo il profilo del browser principale; nei run normali lascialo spento per non sovrascrivere la sessione persistente dello scraper. Il formato export qui sopra e quello usato anche da Avvia ricerca Vinted.",
            style="Hint.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=21, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _build_subito_tab(self) -> None:
        self.subito_scroll = VerticalScrolledFrame(self.subito_tab, background=APP_BG)
        self.subito_scroll.pack(fill="both", expand=True)
        self.subito_scroll.body.configure(style="Panel.TFrame")

        card = self._card(self.subito_scroll.body, "Subito Jobs")
        card.pack(fill="x")
        self._row(card, 0, "Keyword o URL", self.subito_query_var)
        ttk.Label(card, text="Profili lavoro").grid(row=1, column=0, sticky="nw", pady=(0, 10))
        jobs_frame = ttk.Frame(card, style="Panel.TFrame")
        jobs_frame.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(0, 10))
        self.subito_jobs_listbox = tk.Listbox(
            jobs_frame,
            selectmode="multiple",
            exportselection=False,
            height=6,
            bg="#ffffff",
            fg=TEXT,
            relief="solid",
            borderwidth=1,
        )
        jobs_scroll = ttk.Scrollbar(jobs_frame, orient="vertical", command=self.subito_jobs_listbox.yview)
        self.subito_jobs_listbox.configure(yscrollcommand=jobs_scroll.set)
        self.subito_jobs_listbox.grid(row=0, column=0, sticky="nsew")
        jobs_scroll.grid(row=0, column=1, sticky="ns")
        jobs_frame.columnconfigure(0, weight=1)
        for index, (_keyword, label) in enumerate(SUBITO_JOB_OPTIONS):
            self.subito_jobs_listbox.insert("end", label)
        ttk.Label(
            jobs_frame,
            text="Seleziona uno o piu lavori. Ogni keyword viene cercata e poi i risultati vengono uniti.",
            style="Hint.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(jobs_frame, text="Keyword extra (virgole)").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 4))
        ttk.Entry(jobs_frame, textvariable=self.subito_custom_job_keywords_var).grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Label(jobs_frame, text="Profili salvati").grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 4))
        saved_profiles_frame = ttk.Frame(jobs_frame, style="Panel.TFrame")
        saved_profiles_frame.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.subito_saved_profiles_listbox = tk.Listbox(
            saved_profiles_frame,
            selectmode="multiple",
            exportselection=False,
            height=5,
            bg="#ffffff",
            fg=TEXT,
            relief="solid",
            borderwidth=1,
        )
        saved_profiles_scroll = ttk.Scrollbar(saved_profiles_frame, orient="vertical", command=self.subito_saved_profiles_listbox.yview)
        self.subito_saved_profiles_listbox.configure(yscrollcommand=saved_profiles_scroll.set)
        self.subito_saved_profiles_listbox.grid(row=0, column=0, sticky="nsew")
        saved_profiles_scroll.grid(row=0, column=1, sticky="ns")
        saved_profiles_frame.columnconfigure(0, weight=1)
        ttk.Label(jobs_frame, text="Nome profilo da salvare").grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 4))
        profile_actions = ttk.Frame(jobs_frame, style="Panel.TFrame")
        profile_actions.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Entry(profile_actions, textvariable=self.subito_profile_name_var, width=24).grid(row=0, column=0, sticky="ew")
        ttk.Button(profile_actions, text="Salva profilo", style="Secondary.TButton", command=self._save_subito_profile).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(profile_actions, text="Elimina selezionati", style="Secondary.TButton", command=self._delete_selected_subito_profiles).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(profile_actions, text="Pulisci profili", style="Secondary.TButton", command=self._clear_saved_profile_selection).grid(row=0, column=3, padx=(8, 0))
        profile_actions.columnconfigure(0, weight=1)
        ttk.Label(
            jobs_frame,
            textvariable=self.subito_profiles_info_var,
            style="Hint.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.subito_jobs_listbox.bind("<<ListboxSelect>>", lambda _e: self._update_subito_job_keywords_preview())
        self.subito_saved_profiles_listbox.bind("<<ListboxSelect>>", lambda _e: self._update_subito_job_keywords_preview())
        self._row(card, 2, "Regione", self.subito_region_var, 24)
        self._row(card, 3, "Citta / zone (virgole)", self.subito_city_var, 24)
        ttk.Label(card, text="Paesini vicini salvati").grid(row=4, column=0, sticky="nw", pady=(0, 10))
        nearby_frame = ttk.Frame(card, style="Panel.TFrame")
        nearby_frame.grid(row=4, column=1, sticky="ew", padx=(10, 0), pady=(0, 10))
        self.subito_nearby_cities_listbox = tk.Listbox(
            nearby_frame,
            selectmode="multiple",
            exportselection=False,
            height=6,
            bg="#ffffff",
            fg=TEXT,
            relief="solid",
            borderwidth=1,
        )
        nearby_scroll = ttk.Scrollbar(nearby_frame, orient="vertical", command=self.subito_nearby_cities_listbox.yview)
        self.subito_nearby_cities_listbox.configure(yscrollcommand=nearby_scroll.set)
        self.subito_nearby_cities_listbox.grid(row=0, column=0, sticky="nsew")
        nearby_scroll.grid(row=0, column=1, sticky="ns")
        nearby_actions = ttk.Frame(nearby_frame, style="Panel.TFrame")
        nearby_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(nearby_actions, text="Usa selezionati", style="Secondary.TButton", command=self._apply_nearby_city_selection).grid(row=0, column=0, sticky="w")
        ttk.Button(nearby_actions, text="Pulisci zone", style="Secondary.TButton", command=self._clear_nearby_city_selection).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            nearby_frame,
            text="Seleziona uno o piu paesi: il campo citta sopra viene aggiornato automaticamente.",
            style="Hint.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        nearby_frame.columnconfigure(0, weight=1)
        for index, city_name in enumerate(SUBITO_NEARBY_CITY_OPTIONS):
            self.subito_nearby_cities_listbox.insert("end", city_name)
            if city_name == "roma":
                self.subito_nearby_cities_listbox.selection_set(index)
        self.subito_nearby_cities_listbox.bind("<<ListboxSelect>>", lambda _e: self._apply_nearby_city_selection())
        self._apply_nearby_city_selection()
        self._row(card, 5, "Categoria", self.subito_category_var, 24)
        self._row(card, 6, "Punto riferimento", self.subito_anchor_place_var, 24)
        self._row(card, 7, "Distanza max km", self.subito_max_distance_var, 12)
        self._row(card, 8, "Max risultati", self.subito_max_results_var, 12)
        self._row(card, 9, "Ultime ore", self.subito_max_age_hours_var, 12)
        self._row(card, 10, "Max eta annuncio (giorni)", self.subito_max_age_days_var, 12)
        self._row(card, 11, "Solo giorno (giorni fa)", self.subito_exact_age_days_var, 12)
        self._row(card, 12, "Monitor ogni (ore)", self.subito_auto_interval_hours_var, 12)
        ttk.Checkbutton(card, text="Tieni solo annunci accettati", variable=self.subito_nearby_only_var).grid(row=13, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(card, text="Apri ogni annuncio ed estrai descrizione completa", variable=self.subito_include_details_var).grid(row=14, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(card, text="Analizza descrizioni con OpenAI", variable=self.subito_llm_screening_var).grid(row=15, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._row(card, 16, "Modello OpenAI", self.subito_openai_model_var, 24)
        ttk.Label(
            card,
            text="Richiede OPENAI_API_KEY nell ambiente. Puoi indicare piu citta separate da virgola, per esempio: monterotondo, fiano romano, roma, capena. Se compili 'Ultime ore', quel filtro ha priorita sugli altri filtri data e vengono tenuti solo gli annunci con orario disponibile. Se compili 'Solo giorno', quel filtro ha priorita sulla soglia in giorni. Quando lo screening e attivo, la descrizione completa viene recuperata automaticamente e i risultati vengono smistati in candida / valuta / no.",
            style="Hint.TLabel",
            wraplength=460,
            justify="left",
        ).grid(row=17, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_custom_tab(self) -> None:
        self.custom_scroll = VerticalScrolledFrame(self.custom_tab, background=APP_BG)
        self.custom_scroll.pack(fill="both", expand=True)
        self.custom_scroll.body.configure(style="Panel.TFrame")

        card = self._card(self.custom_scroll.body, "Custom Site")
        card.pack(fill="x")
        self._row(card, 0, "URL del sito", self.custom_url_var)
        self._row(card, 1, "Selector item", self.custom_item_selector_var)
        self._row(card, 2, "Selector nome", self.custom_name_selector_var)
        self._row(card, 3, "Selector telefono", self.custom_phone_selector_var)
        self._row(card, 4, "Selector link", self.custom_link_selector_var)
        self._row(card, 5, "Testi cookie", self.custom_cookie_texts_var)

    def _build_browser_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Browser")
        card.pack(fill="x")
        ttk.Label(card, text="Modalita").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.browser_mode_box = ttk.Combobox(
            card,
            textvariable=self.browser_mode_var,
            values=("sessione_persistente", "chrome_normale", "profilo_personalizzato", "isolated"),
            state="readonly",
            width=24,
        )
        self.browser_mode_box.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 10))
        ttk.Label(card, text="Chrome User Data").grid(row=1, column=0, sticky="w", pady=(0, 10))
        self.browser_user_data_entry = ttk.Entry(card, textvariable=self.browser_user_data_dir_var, width=54)
        self.browser_user_data_entry.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(0, 10))
        self.browser_browse_button = ttk.Button(card, text="Sfoglia", style="Secondary.TButton", command=self._choose_browser_user_data_dir)
        self.browser_browse_button.grid(row=1, column=2, sticky="e", pady=(0, 10))
        ttk.Label(card, text="Profile Directory").grid(row=2, column=0, sticky="w")
        self.browser_profile_dir_entry = ttk.Entry(card, textvariable=self.browser_profile_directory_var, width=18)
        self.browser_profile_dir_entry.grid(row=2, column=1, sticky="w", padx=(10, 0))
        ttk.Checkbutton(card, text="Modalita lenta per connessione instabile", variable=self.slow_mode_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(card, text="Pausa azioni (sec)").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(card, textvariable=self.action_delay_seconds_var, width=10).grid(row=4, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Label(card, text="Attesa post-caricamento (sec)").grid(row=5, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(card, textvariable=self.page_settle_seconds_var, width=10).grid(row=5, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Label(
            card,
            text="Usa sessione_persistente per mantenere login e cookie. La modalita lenta aggiunge pause piu generose.",
            style="Hint.TLabel",
            wraplength=360,
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        card.columnconfigure(1, weight=1)

    def _build_export_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Export")
        card.pack(fill="x")
        ttk.Label(card, text="Formato").grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Combobox(card, textvariable=self.output_format_var, values=("json", "csv", "xlsx", "all"), state="readonly", width=12).grid(row=0, column=1, sticky="w", padx=(10, 18), pady=(0, 10))
        ttk.Label(card, text="Nome file").grid(row=0, column=2, sticky="w", pady=(0, 10))
        ttk.Entry(card, textvariable=self.filename_var, width=24).grid(row=0, column=3, sticky="ew", padx=(10, 0), pady=(0, 10))
        ttk.Label(card, text="Cartella output").grid(row=1, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.output_dir_var, width=54).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(10, 8))
        ttk.Button(card, text="Sfoglia", style="Secondary.TButton", command=self._choose_output_dir).grid(row=1, column=4, sticky="e")
        card.columnconfigure(3, weight=1)

    def _build_action_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Azioni")
        card.pack(fill="x")
        self.run_button = ttk.Button(card, text="Avvia scraping", style="Run.TButton", command=self._start_scrape)
        self.run_button.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(card, text="Apri output", style="Secondary.TButton", command=self._open_output_dir).grid(row=1, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))
        ttk.Button(card, text="Ricarica risultati", style="Secondary.TButton", command=self._load_results).grid(row=1, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))
        ttk.Button(card, text="Pulisci log", style="Secondary.TButton", command=self._clear_log).grid(row=2, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))
        ttk.Button(card, text="Apri lead su Maps", style="Secondary.TButton", command=self._open_selected_link).grid(row=2, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))
        self.stop_process_button = ttk.Button(card, text="Ferma processo", style="Secondary.TButton", command=self._stop_current_process)
        self.stop_process_button.grid(row=3, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))
        self.stop_after_item_button = ttk.Button(card, text="Ferma dopo attivita", style="Secondary.TButton", command=self._request_stop_after_current_item)
        self.stop_after_item_button.grid(row=3, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))
        self.skip_item_button = ttk.Button(card, text="Salta attivita corrente", style="Secondary.TButton", command=self._request_skip_current_item)
        self.skip_item_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.auto_monitor_button = ttk.Button(card, text="Avvia monitor automatico", style="Accent.TButton", command=self._start_auto_monitor)
        self.auto_monitor_button.grid(row=5, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))
        self.stop_auto_monitor_button = ttk.Button(card, text="Ferma monitor automatico", style="Secondary.TButton", command=self._stop_auto_monitor)
        self.stop_auto_monitor_button.grid(row=5, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))
        ttk.Label(card, textvariable=self.auto_monitor_status_var, style="Hint.TLabel", wraplength=360, justify="left").grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 0),
        )
        self.open_browser_button = ttk.Button(
            card,
            text="Apri browser",
            style="Accent.TButton",
            command=self._start_browser,
        )
        self.open_browser_button.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

    def _build_results_tab(self) -> None:
        summary = self._card(self.results_tab, "Riepilogo")
        summary.pack(fill="x")
        ttk.Label(summary, textvariable=self.result_total_var, style="Metric.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.result_counts_var, style="Metric.TLabel").grid(row=0, column=1, sticky="w", padx=(18, 0))
        ttk.Label(summary, text="Ordina per").grid(row=0, column=2, sticky="e", padx=(18, 6))
        self.result_sort_box = ttk.Combobox(
            summary,
            textvariable=self.result_sort_var,
            values=RESULT_SORT_OPTIONS,
            state="readonly",
            width=22,
        )
        self.result_sort_box.grid(row=0, column=3, sticky="w")
        ttk.Label(summary, textvariable=self.result_meta_var).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(summary, text="Puoi riordinare la tabella dalla tendina oppure cliccando sulle intestazioni delle colonne.", style="Muted.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        card = self._card(self.results_tab, "Tabella risultati")
        card.pack(fill="x", pady=(12, 0))
        columns = ("lead_priority", "opportunity_score", "name", "category", "city", "phone", "email", "website_status", "rating", "reviews_count")
        self.results_tree = ttk.Treeview(card, columns=columns, show="headings", selectmode="extended", height=16)
        self.results_column_labels = {
            "lead_priority": "Priorita",
            "opportunity_score": "Score",
            "name": "Attivita",
            "category": "Categoria",
            "city": "Citta",
            "phone": "Telefono",
            "email": "Email",
            "website_status": "Stato sito",
            "rating": "Rating",
            "reviews_count": "Recensioni",
        }
        for key, label, width in (
            ("lead_priority", "Priorita", 90),
            ("opportunity_score", "Score", 70),
            ("name", "Attivita", 260),
            ("category", "Categoria", 150),
            ("city", "Citta", 120),
            ("phone", "Telefono", 145),
            ("email", "Email", 210),
            ("website_status", "Stato sito", 110),
            ("rating", "Rating", 75),
            ("reviews_count", "Recensioni", 90),
        ):
            self.results_tree.heading(key, text=label, command=lambda current_key=key: self._on_results_heading_click(current_key))
            self.results_tree.column(key, width=width, anchor="center" if key in {"lead_priority", "opportunity_score", "website_status", "rating", "reviews_count"} else "w")
        self.results_tree.tag_configure("alta", background="#fff0e7")
        self.results_tree.tag_configure("media", background="#fff8df")
        self.results_tree.tag_configure("bassa", background="#edf7f0")
        self.results_tree.tag_configure("accepted", background="#edf7f0")
        self.results_tree.tag_configure("maybe", background="#fff7e8")
        self.results_tree.tag_configure("rejected", background="#fdeeee")
        self.results_tree.tag_configure("vinted_hot", background="#fee2e2")
        self.results_tree.tag_configure("vinted_review", background="#fef3c7")
        self.results_tree.tag_configure("vinted_badge", background="#ecfccb")
        y_scroll = ttk.Scrollbar(card, orient="vertical", command=self.results_tree.yview)
        x_scroll = ttk.Scrollbar(card, orient="horizontal", command=self.results_tree.xview)
        self.results_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)
        self._bind_treeview_scroll(self.results_tree)
        self.results_tree.bind("<Double-1>", lambda _e: self._open_selected_link())
        self.results_tree.bind("<<TreeviewSelect>>", lambda _e: self._handle_result_selection())
        self._update_result_heading_labels()

        detail_card = self._card(self.results_tab, "Dettaglio selezione")
        self.detail_card = detail_card
        detail_card.pack(fill="x", pady=(12, 0))
        tk.Label(
            detail_card,
            textvariable=self.detail_title_var,
            bg=PANEL_BG,
            fg=ACCENT,
            font=("Segoe UI", 12, "bold"),
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        self._detail_row(detail_card, 1, "Sorgente", self.detail_source_var)
        self._detail_row(detail_card, 1, "Priorita", self.detail_screening_var, column_offset=2)
        self._detail_row(detail_card, 2, "Stato lead", self.detail_contact_var)
        self._detail_row(detail_card, 2, "Stato sito", self.detail_decision_var, column_offset=2)
        self._detail_row(detail_card, 3, "Estratto", self.detail_date_var)
        self._detail_row(detail_card, 3, "Rating", self.detail_distance_var, column_offset=2)
        self._detail_row(detail_card, 4, "Indirizzo", self.detail_location_var)
        self._detail_row(detail_card, 4, "Email", self.detail_company_var, column_offset=2)
        self._detail_row(detail_card, 5, "Categoria", self.detail_sector_var)
        self._detail_row(detail_card, 5, "Telefono", self.detail_role_var, column_offset=2)
        self._detail_row(detail_card, 6, "Recensioni", self.detail_schedule_var)
        self._detail_row(detail_card, 6, "Risposta sito", self.detail_price_var, column_offset=2)
        ttk.Label(detail_card, text="Google Maps").grid(row=7, column=0, sticky="w", pady=(12, 6))
        self.detail_link_entry = ttk.Entry(detail_card, textvariable=self.detail_link_var)
        self.detail_link_entry.grid(row=7, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=(12, 6))
        ttk.Label(detail_card, text="Sito web").grid(row=8, column=0, sticky="w", pady=(6, 6))
        ttk.Entry(detail_card, textvariable=self.detail_website_var).grid(row=8, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=(6, 6))
        ttk.Label(detail_card, text="Analisi lead").grid(row=9, column=0, sticky="nw", pady=(12, 6))
        self.detail_raw_text = ScrolledText(
            detail_card,
            wrap="word",
            height=10,
            state="disabled",
            bg="#f8fafc",
            fg=TEXT,
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=10,
            font=("Segoe UI", 10),
        )
        self.detail_raw_text.grid(row=9, column=1, columnspan=3, sticky="nsew", padx=(10, 0), pady=(12, 6))
        detail_card.columnconfigure(1, weight=1)
        detail_card.columnconfigure(3, weight=1)
        detail_card.rowconfigure(9, weight=1)

        lead_card = self._card(self.results_tab, "Azioni lead")
        self.lead_card = lead_card
        lead_card.pack(fill="x", pady=(12, 0))
        self.lead_action_hint_label = ttk.Label(
            lead_card,
            text="Apri la scheda Maps per verificare i dati oppure visita il sito pubblico prima di preparare la demo commerciale.",
            style="Hint.TLabel",
            wraplength=430,
            justify="left",
        )
        self.lead_action_hint_label.grid(row=0, column=0, columnspan=2, sticky="w")
        self.open_selected_button = ttk.Button(lead_card, text="Apri Google Maps", style="Accent.TButton", command=self._open_selected_link)
        self.open_selected_button.grid(row=1, column=0, sticky="ew", pady=(12, 0), padx=(0, 6))
        self.open_website_button = ttk.Button(lead_card, text="Apri sito web", style="Secondary.TButton", command=self._open_selected_website)
        self.open_website_button.grid(row=1, column=1, sticky="ew", pady=(12, 0), padx=(6, 0))
        lead_card.columnconfigure(0, weight=1)
        lead_card.columnconfigure(1, weight=1)

        contact_card = self._card(self.results_tab, "Contatto Subito")
        self.contact_card = contact_card
        contact_card.pack(fill="x", pady=(12, 0))
        ttk.Label(
            contact_card,
            text="Richiede un annuncio Subito selezionato. Con sessione_persistente fai il login una volta sola e i run successivi lo riusano. Se al primo contatto Subito chiede accesso, il flusso aspetta che tu faccia login e poi continua. Gli annunci gia inviati vengono marcati nella tabella e i bottoni batch li saltano automaticamente. Se hai attivato lo screening OpenAI, il batch usa prima gli annunci consigliati.",
            style="Hint.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(contact_card, text="Allegato").grid(row=1, column=0, sticky="w", pady=(10, 8))
        ttk.Entry(contact_card, textvariable=self.attachment_path_var).grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(10, 8))
        ttk.Button(contact_card, text="Sfoglia", style="Secondary.TButton", command=self._choose_attachment_file).grid(row=1, column=2, sticky="e", pady=(10, 8))
        ttk.Label(contact_card, text="Messaggio").grid(row=2, column=0, sticky="nw", pady=(0, 8))
        self.contact_message_text = ScrolledText(
            contact_card,
            wrap="word",
            height=4,
            bg="#f8fafc",
            fg=TEXT,
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 10),
        )
        self.contact_message_text.grid(row=2, column=1, columnspan=2, sticky="nsew", padx=(10, 0), pady=(0, 8))
        self.contact_message_text.insert("1.0", self.contact_message_var.get())
        ttk.Checkbutton(contact_card, text="Invia davvero il messaggio finale", variable=self.contact_submit_var).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(0, 8),
        )
        ttk.Label(contact_card, text="Tieni browser aperto (sec)").grid(row=4, column=0, sticky="w")
        ttk.Entry(contact_card, textvariable=self.contact_keep_open_seconds_var, width=12).grid(row=4, column=1, sticky="w", padx=(10, 0))
        ttk.Button(contact_card, text="Apri allegato", style="Secondary.TButton", command=self._open_attachment_file).grid(row=4, column=2, sticky="e")
        self.contact_button = ttk.Button(contact_card, text="Prepara annuncio", style="Accent.TButton", command=self._start_contact_action)
        self.contact_button.grid(row=5, column=0, sticky="ew", pady=(12, 0), padx=(0, 6))
        self.contact_selected_button = ttk.Button(contact_card, text="Invia CV solo ai selezionati", style="Accent.TButton", command=self._start_batch_contact_selected)
        self.contact_selected_button.grid(row=5, column=1, sticky="ew", pady=(12, 0), padx=(6, 6))
        self.contact_accepted_button = ttk.Button(contact_card, text="Invia CV a tutti i consigliati", style="Accent.TButton", command=self._start_batch_contact_accepted)
        self.contact_accepted_button.grid(row=5, column=2, sticky="ew", pady=(12, 0))
        self.subito_open_selected_button = ttk.Button(contact_card, text="Apri annuncio", style="Secondary.TButton", command=self._open_selected_link)
        self.subito_open_selected_button.grid(row=6, column=2, sticky="ew", pady=(10, 0))
        contact_card.columnconfigure(1, weight=1)
        contact_card.columnconfigure(2, weight=1)
        self.contact_card.pack_forget()

    def _configure_results_columns(self, source: str) -> None:
        if source == "google_maps":
            column_config = (
                ("lead_priority", "Priorita", 90, "center"),
                ("opportunity_score", "Score", 70, "center"),
                ("name", "Attivita", 260, "w"),
                ("category", "Categoria", 150, "w"),
                ("city", "Citta", 120, "w"),
                ("phone", "Telefono", 145, "w"),
                ("email", "Email", 210, "w"),
                ("website_status", "Stato sito", 110, "center"),
                ("rating", "Rating", 75, "center"),
                ("reviews_count", "Recensioni", 90, "center"),
            )
        elif source == "vinted":
            column_config = (
                ("search_term", "Ricerca", 120, "w"),
                ("evaluation_label", "Valutazione", 170, "center"),
                ("tag", "Tag", 95, "center"),
                ("name", "Nome prodotto", 300, "w"),
                ("price_value", "Totale", 90, "center"),
                ("favorite_count", "Preferiti", 85, "center"),
                ("item_id", "ID articolo", 110, "center"),
                ("times_seen", "Rilevato", 80, "center"),
                ("extracted_at", "Ultima volta", 140, "center"),
                ("link", "Link Vinted", 420, "w"),
            )
        else:
            column_config = (
                ("screening_decision", "Candidatura", 105, "center"),
                ("screening_score", "Score", 70, "center"),
                ("contact_status", "Contatto", 95, "center"),
                ("decision", "Decisione", 100, "center"),
                ("published_at", "Data", 110, "center"),
                ("extracted_at", "Estratto", 125, "center"),
                ("distance_km", "Km", 70, "center"),
                ("location", "Luogo", 150, "w"),
                ("title", "Titolo", 360, "w"),
                ("company", "Azienda", 180, "w"),
                ("schedule", "Orario", 100, "center"),
            )

        columns = tuple(item[0] for item in column_config)
        self.results_tree.configure(columns=columns)
        self.results_column_labels = {key: label for key, label, _width, _anchor in column_config}
        for key, label, width, anchor in column_config:
            self.results_tree.heading(key, text=label, command=lambda current_key=key: self._on_results_heading_click(current_key))
            self.results_tree.column(key, width=width, anchor=anchor)

    def _handle_source_tab_changed(self) -> None:
        self._update_hint()
        selected = self._selected_source_text()
        if selected == "Subito Jobs":
            source = "subito"
        elif selected == "Vinted":
            source = "vinted"
        else:
            source = "google_maps"
        self._configure_result_panels(source)

    def _configure_result_panels(self, source: str) -> None:
        if not hasattr(self, "lead_card") or not hasattr(self, "contact_card"):
            return
        if source == "subito":
            self.lead_card.pack_forget()
            if not self.contact_card.winfo_manager():
                self.contact_card.pack(fill="x", pady=(12, 0))
        else:
            self.contact_card.pack_forget()
            if not self.lead_card.winfo_manager():
                self.lead_card.pack(fill="x", pady=(12, 0))
            if source == "vinted":
                self.lead_card.configure(text="Azioni prodotto")
                self.lead_action_hint_label.configure(text="Apri l'annuncio Vinted selezionato per verificare foto e dettagli originali.")
                self.open_selected_button.configure(text="Apri annuncio Vinted")
            else:
                self.lead_card.configure(text="Azioni lead")
                self.lead_action_hint_label.configure(
                    text="Apri la scheda Maps per verificare i dati oppure visita il sito pubblico prima di preparare la demo commerciale."
                )
                self.open_selected_button.configure(text="Apri Google Maps")

    def _configure_detail_labels(self, source: str) -> None:
        if not hasattr(self, "detail_card"):
            return
        if source == "google_maps":
            labels = {
                (1, 0): "Sorgente", (1, 2): "Priorita",
                (2, 0): "Stato lead", (2, 2): "Stato sito",
                (3, 0): "Estratto", (3, 2): "Rating",
                (4, 0): "Indirizzo", (4, 2): "Email",
                (5, 0): "Categoria", (5, 2): "Telefono",
                (6, 0): "Recensioni", (6, 2): "Risposta sito",
                (7, 0): "Google Maps", (8, 0): "Sito web", (9, 0): "Analisi lead",
            }
            self.detail_card.configure(text="Dettaglio lead")
        elif source == "vinted":
            labels = {
                (1, 0): "Sorgente", (1, 2): "Valutazione",
                (2, 0): "Tag", (2, 2): "Preferiti",
                (3, 0): "Prezzo", (3, 2): "Totale",
                (4, 0): "Estratto", (4, 2): "Spedizione",
                (5, 0): "Nome", (5, 2): "Ricerca",
                (6, 0): "Prima rilevazione", (6, 2): "Volte rilevato",
                (7, 0): "Link Vinted", (8, 0): "Database", (9, 0): "Testo scheda",
            }
            self.detail_card.configure(text="Dettaglio prodotto Vinted")
        else:
            labels = {
                (1, 0): "Sorgente", (1, 2): "Candidatura",
                (2, 0): "Contatto", (2, 2): "Decisione geo",
                (3, 0): "Data", (3, 2): "Distanza",
                (4, 0): "Luogo", (4, 2): "Azienda",
                (5, 0): "Settore", (5, 2): "Ruolo",
                (6, 0): "Orario", (6, 2): "Prezzo",
                (7, 0): "Link", (8, 0): "Sito web", (9, 0): "Testo annuncio",
            }
            self.detail_card.configure(text="Dettaglio annuncio")
        for (row, column), text in labels.items():
            for widget in self.detail_card.grid_slaves(row=row, column=column):
                if isinstance(widget, (ttk.Label, tk.Label)):
                    widget.configure(text=text)
                    break

    def _build_log_tab(self) -> None:
        card = self._card(self.log_tab, "Log esecuzione")
        card.pack(fill="x")
        self.log_widget = ScrolledText(card, wrap="word", state="disabled", bg="#182120", fg="#edf4f1", insertbackground="#edf4f1", relief="flat", padx=12, pady=12, font=("Consolas", 10))
        self.log_widget.configure(height=16)
        self.log_widget.pack(fill="both", expand=True)

    def _card(self, parent: ttk.Frame, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=16)

    def _row(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar, width: int = 78) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 10))
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(0, 10))
        parent.columnconfigure(1, weight=1)

    def _detail_row(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar, column_offset: int = 0) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column_offset, sticky="w", pady=(6, 0))
        tk.Label(parent, textvariable=variable, bg=PANEL_BG, fg=TEXT, justify="left", wraplength=180).grid(
            row=row,
            column=column_offset + 1,
            sticky="w",
            padx=(10, 0),
            pady=(6, 0),
        )

    def _bind_treeview_scroll(self, widget: ttk.Treeview) -> None:
        widget.bind("<MouseWheel>", lambda event: self._on_treeview_mousewheel(widget, event), add="+")
        widget.bind("<Shift-MouseWheel>", lambda event: self._on_treeview_horizontal_mousewheel(widget, event), add="+")
        widget.bind("<Button-4>", lambda event: self._on_treeview_button_scroll(widget, event, -1), add="+")
        widget.bind("<Button-5>", lambda event: self._on_treeview_button_scroll(widget, event, 1), add="+")
        widget.bind("<Shift-Button-4>", lambda event: self._on_treeview_button_scroll(widget, event, -1, horizontal=True), add="+")
        widget.bind("<Shift-Button-5>", lambda event: self._on_treeview_button_scroll(widget, event, 1, horizontal=True), add="+")

    def _on_treeview_mousewheel(self, widget: ttk.Treeview, event: tk.Event) -> str:
        delta = getattr(event, "delta", 0)
        if delta == 0:
            return "break"
        step = -1 if delta > 0 else 1
        widget.yview_scroll(step, "units")
        return "break"

    def _on_treeview_horizontal_mousewheel(self, widget: ttk.Treeview, event: tk.Event) -> str:
        delta = getattr(event, "delta", 0)
        if delta == 0:
            return "break"
        step = -1 if delta > 0 else 1
        widget.xview_scroll(step, "units")
        return "break"

    def _on_treeview_button_scroll(self, widget: ttk.Treeview, _event: tk.Event, step: int, horizontal: bool = False) -> str:
        if horizontal:
            widget.xview_scroll(step, "units")
        else:
            widget.yview_scroll(step, "units")
        return "break"

    def _sync_active_source_from_notebook(self) -> None:
        if hasattr(self, "notebook"):
            self.active_source_var.set(self.notebook.tab(self.notebook.select(), "text"))
        self._handle_source_tab_changed()

    def _selected_source_text(self) -> str:
        if hasattr(self, "notebook"):
            return self.notebook.tab(self.notebook.select(), "text")
        return self.active_source_var.get().strip() or "Google Maps"

    def _scroll_to_widget(self, widget: tk.Misc) -> None:
        if not hasattr(self, "page_scroll"):
            return
        self.root.update_idletasks()
        body = self.page_scroll.body
        body_height = max(body.winfo_height(), 1)
        relative_y = max(widget.winfo_rooty() - body.winfo_rooty(), 0)
        self.page_scroll.canvas.yview_moveto(min(relative_y / body_height, 1.0))

    def _update_hint(self) -> None:
        selected = self._selected_source_text()
        if selected == "Subito Jobs":
            self.hint_var.set(
                "Subito Jobs: scegli keyword, citta e filtri data. I risultati possono essere smistati, letti nel dettaglio e usati per il monitor automatico."
            )
        elif selected == "Google Maps":
            self.hint_var.set(
                "Google Maps: inserisci categorie e citta. Il sistema apre le schede, trova sito e contatti pubblici e ordina i lead."
            )
        elif selected == "Vinted":
            self.hint_var.set(
                "Vinted: cerca dal catalogo, seleziona gli articoli e poi aggiorna descrizione, spedizione e totale dal dettaglio annuncio."
            )
        else:
            self.hint_var.set(
                "Custom Site: usa selettori CSS personalizzati e visualizza l'output nella tabella risultati."
            )

    def _update_browser_mode(self) -> None:
        mode = normalize_browser_mode(self.browser_mode_var.get())
        profile_state = "normal" if browser_mode_uses_profile(mode) else "disabled"
        custom_state = "normal" if browser_mode_requires_custom_dir(mode) else "disabled"
        self.browser_user_data_entry.configure(state=custom_state)
        self.browser_browse_button.configure(state=custom_state)
        self.browser_profile_dir_entry.configure(state=profile_state)
        for widget_name, state in (
            ("vinted_browser_user_data_entry", custom_state),
            ("vinted_browser_browse_button", custom_state),
            ("vinted_browser_profile_dir_entry", profile_state),
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(state=state)
        self._update_vinted_profile_status()

    def _update_vinted_profile_status(self) -> None:
        mode = normalize_browser_mode(self.browser_mode_var.get())
        if mode != "sessione_persistente":
            self.vinted_profile_session_var.set(f"Non in uso: modalita {mode}")
            self.vinted_profile_cookies_var.set("Nessun controllo: la sessione persistente e disattivata.")
            self.vinted_profile_last_import_var.set("-")
            if self.vinted_last_access_marker_present is None:
                self.vinted_profile_access_var.set("Non ancora verificato sul sito.")
            return

        profile_directory = self.browser_profile_directory_var.get().strip() or "Default"
        source_dir_raw = self.browser_user_data_dir_var.get().strip()
        source_dir = Path(source_dir_raw).expanduser() if source_dir_raw else None
        source_exists = bool(source_dir and source_dir.exists())
        profile_info = inspect_persistent_profile(profile_directory)
        cookies_present = bool(profile_info["cookies_present"])
        has_snapshot = bool(profile_info["tracked_file_count"])
        last_import = self._format_status_timestamp(str(profile_info["last_updated_at"] or ""))
        profile_root = str(profile_info["root_path"] or "")

        if cookies_present:
            session_label = "Probabile attiva"
        elif has_snapshot:
            session_label = "Profilo presente ma sessione non confermata"
        else:
            session_label = "Non inizializzata"

        if self.vinted_refresh_browser_profile_var.get():
            session_label += " | refresh attivo al prossimo run"

        source_label = "Chrome rilevato" if source_exists else "Chrome sorgente non trovato"
        self.vinted_profile_session_var.set(f"{session_label} | profilo {profile_directory}")
        self.vinted_profile_cookies_var.set(f"{'Presenti' if cookies_present else 'Assenti'} | {source_label} | {profile_root}")
        self.vinted_profile_last_import_var.set(last_import)
        if self.vinted_last_access_marker_present is None:
            self.vinted_profile_access_var.set("Profilo pronto ma accesso sito non ancora verificato.")

    def _format_status_timestamp(self, raw_value: str) -> str:
        if not raw_value:
            return "Mai"
        try:
            dt = datetime.fromisoformat(raw_value)
        except ValueError:
            return raw_value
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _apply_vinted_access_status(self, status: dict[str, object], notify: bool = False) -> None:
        marker_present = bool(status.get("marker_present"))
        expected_alt = str(status.get("expected_alt", "bonaccarla") or "bonaccarla")
        checked_at = self._format_status_timestamp(str(status.get("checked_at", "") or ""))
        current_url = str(status.get("current_url", "") or "")
        if marker_present:
            self.vinted_last_access_marker_present = True
            self.vinted_profile_access_var.set(
                f"Confermato | marker {expected_alt} trovato | {checked_at}"
            )
            return

        self.vinted_last_access_marker_present = False
        self.vinted_profile_access_var.set(
            f"NON confermato | marker {expected_alt} assente | {checked_at}"
        )
        self.vinted_status_var.set(
            "Attenzione: accesso Vinted non confermato. Marker account assente."
        )
        if notify and not self.vinted_access_warning_shown_for_process:
            self.vinted_access_warning_shown_for_process = True
            suffix = f"\n\nURL controllato:\n{current_url}" if current_url else ""
            messagebox.showwarning(
                "Accesso Vinted non confermato",
                f"Non ho trovato il marker account '{expected_alt}'.\n"
                "Per questo run considero l'accesso non confermato."+suffix,
            )

    def _sync_vinted_access_from_meta(self, meta: dict[str, object]) -> None:
        if "vinted_access_marker_present" not in meta:
            return
        status = {
            "marker_present": bool(meta.get("vinted_access_marker_present")),
            "expected_alt": str(meta.get("vinted_access_expected_alt", "bonaccarla") or "bonaccarla"),
            "current_url": str(meta.get("vinted_access_current_url", "") or ""),
            "checked_at": str(meta.get("vinted_access_checked_at", "") or ""),
        }
        self._apply_vinted_access_status(status, notify=False)

    def _handle_vinted_login_required(self, status: dict[str, object]) -> None:
        self._apply_vinted_access_status(status, notify=False)
        if self.vinted_login_prompt_open:
            return
        self.vinted_login_prompt_open = True
        current_url = str(status.get("current_url", "") or "")
        suffix = f"\n\nURL aperto:\n{current_url}" if current_url else ""
        try:
            messagebox.showinfo(
                "Login Vinted richiesto",
                "Non risulti loggato su Vinted.\n\n"
                "Esegui ora il login manualmente nel browser aperto, poi premi OK."+suffix,
            )
            request_vinted_login_confirmed()
            self._append_log("[vinted-login] Conferma login inviata dalla UI.\n")
        finally:
            self.vinted_login_prompt_open = False

    def _update_result_actions(self) -> None:
        row = self._get_selected_row()
        selected_rows = self._get_selected_rows()
        selected_new_rows = [item for item in selected_rows if not self._row_has_submitted_contact(item)]
        accepted_rows = self._get_accepted_subito_rows()
        selected_vinted_rows = [item for item in selected_rows if str(item.get("source", "") or "").strip().lower() == "vinted"]
        has_row = row is not None and bool(str(row.get("link", "") or "").strip())
        has_website = row is not None and bool(str(row.get("website", "") or "").strip())
        is_subito = has_row and str(row.get("source", "") or "").strip().lower() == "subito"
        self.open_selected_button.configure(state="normal" if has_row else "disabled")
        self.subito_open_selected_button.configure(state="normal" if has_row else "disabled")
        self.open_website_button.configure(state="normal" if has_website else "disabled")
        if hasattr(self, "vinted_extract_button"):
            self.vinted_extract_button.configure(state="normal" if selected_vinted_rows else "disabled")
        self.contact_button.configure(state="normal" if is_subito else "disabled")
        self.contact_selected_button.configure(state="normal" if selected_new_rows else "disabled")
        self.contact_accepted_button.configure(state="normal" if accepted_rows else "disabled")

    def _update_vinted_search_preview(self) -> None:
        search = self.vinted_search_var.get().strip()
        if not search:
            self.vinted_search_preview_var.set("https://www.vinted.it/catalog")
        elif search.lower().startswith(("http://", "https://")):
            self.vinted_search_preview_var.set(search)
        else:
            self.vinted_search_preview_var.set(f"https://www.vinted.it/catalog?search_text={quote_plus(search)}")

    def _start_vinted_search(self) -> None:
        if self.process is not None:
            messagebox.showinfo("Ricerca Vinted", "Attendi la fine del processo corrente.")
            return
        active_browser = get_active_vinted_browser_session() if self.vinted_keep_browser_open_var.get() else None
        self.vinted_status_var.set("Avvio della ricerca Vinted in corso...")
        self._start_scrape()
        if self.process is None or self.current_run_source != "vinted":
            self.vinted_status_var.set("Ricerca non avviata: controlla i campi evidenziati nel messaggio.")
        elif active_browser is not None and self.vinted_keep_browser_open_var.get():
            self.vinted_status_var.set(
                "Ricerca Vinted avviata: browser Vinted gia aperto, non ne verra aperto un secondo."
            )
        elif self.vinted_keep_browser_open_var.get():
            seconds = self.vinted_keep_open_seconds_var.get().strip() or "0"
            if seconds == "0":
                self.vinted_status_var.set("Ricerca Vinted avviata. Il browser non verra chiuso automaticamente e i risultati si caricano appena termina l estrazione.")
            else:
                self.vinted_status_var.set(
                    f"Ricerca Vinted avviata. Il browser resta aperto per {seconds} secondi; i risultati si caricano alla chiusura o a timer scaduto."
                )

    def _start_vinted_browser(self) -> None:
        if self.process is not None:
            messagebox.showinfo("Browser Vinted", "Attendi la fine del processo corrente.")
            return
        active_browser = get_active_vinted_browser_session()
        if active_browser is not None:
            existing_url = str(active_browser.get("url", "") or "")
            self.vinted_status_var.set("Browser Vinted gia aperto.")
            messagebox.showinfo(
                "Browser Vinted gia aperto",
                "Esiste gia un browser Vinted aperto dal tool."
                + (f"\n\nURL:\n{existing_url}" if existing_url else ""),
            )
            return
        self.vinted_status_var.set("Apertura della ricerca Vinted nel browser...")
        self._start_browser()

    def _start_vinted_description_extraction_selected(self) -> None:
        if self.process is not None:
            messagebox.showinfo("Descrizioni Vinted", "Attendi la fine del processo corrente.")
            return
        rows = [row for row in self._get_selected_rows() if str(row.get("source", "") or "").strip().lower() == "vinted"]
        if not rows:
            messagebox.showerror("Descrizioni Vinted", "Seleziona almeno un prodotto Vinted nei risultati.")
            return
        if not self._confirm_vinted_description_extraction(rows):
            return
        try:
            command = self._build_vinted_description_command(rows)
        except ValueError as exc:
            messagebox.showerror("Descrizioni Vinted", str(exc))
            return
        self.vinted_status_var.set(f"Estrazione descrizioni Vinted in corso su {len(rows)} prodotti selezionati...")
        self.current_run_source = "vinted"
        self._scroll_to_widget(self.log_tab)
        self._start_process(command, kind="scrape", load_results=True)

    def _confirm_vinted_description_extraction(self, rows: list[dict]) -> bool:
        preview_lines = []
        for row in rows[:8]:
            title = str(row.get("name", "") or "Prodotto senza nome").strip()
            preview_lines.append(f"- {title}")
        remaining = len(rows) - len(preview_lines)
        if remaining > 0:
            preview_lines.append(f"... altri {remaining} prodotti")
        message = (
            f"Stai per estrarre la descrizione completa da {len(rows)} prodotti Vinted selezionati.\n\n"
            "Anteprima:\n"
            + "\n".join(preview_lines)
            + "\n\nConfermi?"
        )
        return messagebox.askyesno("Conferma estrazione descrizioni", message)

    def _choose_vinted_db_path(self) -> None:
        current_path = Path(self.vinted_db_path_var.get().strip() or self.script_path.parent / "data" / "scraper.db")
        initial_dir = current_path.parent if current_path.parent.exists() else self.script_path.parent
        selected_path = filedialog.asksaveasfilename(
            initialdir=str(initial_dir),
            initialfile=current_path.name or "scraper.db",
            defaultextension=".db",
            filetypes=(("Database SQLite", "*.db"), ("Tutti i file", "*.*")),
        )
        if selected_path:
            self.vinted_db_path_var.set(selected_path)
            self.vinted_status_var.set("Database selezionato. Premi Mostra database oppure avvia una ricerca.")

    def _build_vinted_description_command(self, rows: list[dict]) -> list[str]:
        if not rows:
            raise ValueError("Seleziona almeno un prodotto Vinted.")
        db_path = self.vinted_db_path_var.get().strip()
        if not db_path:
            raise ValueError("Inserisci il percorso del database SQLite.")
        links_file = self._write_vinted_links_file(rows)
        cmd = [
            sys.executable,
            str(self.script_path),
            "run",
            "vinted_descriptions",
            "--links-file",
            str(links_file),
            "--db-path",
            db_path,
        ]
        if self.vinted_keep_browser_open_var.get():
            keep_open_seconds = self.vinted_keep_open_seconds_var.get().strip() or "0"
            if not keep_open_seconds.isdigit():
                raise ValueError("I secondi per tenere aperto il browser Vinted devono essere un intero maggiore o uguale a zero.")
            if int(keep_open_seconds) == 0:
                cmd.append("--keep-browser-open")
            else:
                cmd += ["--keep-open-seconds", keep_open_seconds]
        else:
            cmd.append("--no-keep-browser-open")
        if self.vinted_refresh_browser_profile_var.get():
            cmd.append("--refresh-browser-profile")
        cmd += self._browser_command_args()
        cmd += [
            "--format",
            self.output_format_var.get().strip(),
            "--output-dir",
            self.output_dir_var.get().strip(),
            "--ui-result-json",
            str(self.ui_result_json_path),
        ]
        if self.filename_var.get().strip():
            cmd += ["--filename", self.filename_var.get().strip()]
        return cmd

    def _open_vinted_db_folder(self) -> None:
        db_path = Path(self.vinted_db_path_var.get().strip() or self.script_path.parent / "data" / "scraper.db").expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        os.startfile(db_path.parent.resolve())

    def _write_vinted_links_file(self, rows: list[dict]) -> Path:
        output_dir = Path(self.output_dir_var.get()).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        links_file = output_dir / "_ui_vinted_items.json"
        items: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            link = str(row.get("link", "") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            items.append(
                {
                    "link": link,
                    "search_term": str(row.get("search_term", "") or ""),
                    "search_url": str(row.get("search_url", "") or ""),
                    "tag": str(row.get("tag", "") or ""),
                    "name": str(row.get("name", "") or ""),
                }
            )
        links_file.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return links_file

    def _reset_vinted_form(self) -> None:
        self.vinted_search_var.set("macbook")
        self.vinted_max_results_var.set("100")
        self.vinted_db_path_var.set(str((self.script_path.parent / "data" / "scraper.db").resolve()))
        self.vinted_db_filter_var.set("")
        self.vinted_db_limit_var.set("500")
        self.vinted_signal_filter_var.set("tutti")
        self.vinted_keep_browser_open_var.set(True)
        self.vinted_keep_open_seconds_var.set("0")
        self.vinted_refresh_browser_profile_var.set(False)
        self.vinted_status_var.set("Campi Vinted ripristinati.")
        self._update_vinted_profile_status()

    def _choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(self.script_path.parent))
        if folder:
            self.output_dir_var.set(folder)

    def _choose_browser_user_data_dir(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.browser_user_data_dir_var.get() or str(Path.home()))
        if folder:
            self.browser_user_data_dir_var.set(folder)

    def _choose_attachment_file(self) -> None:
        initial = Path(self.attachment_path_var.get()).parent if self.attachment_path_var.get().strip() else Path.home()
        file_path = filedialog.askopenfilename(initialdir=str(initial))
        if file_path:
            self.attachment_path_var.set(file_path)

    def _open_attachment_file(self) -> None:
        attachment = self.attachment_path_var.get().strip()
        if attachment and Path(attachment).exists():
            os.startfile(attachment)

    def _open_output_dir(self) -> None:
        output_dir = Path(self.output_dir_var.get()).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir)

    def _clear_log(self) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

    def _clear_results(self) -> None:
        self.result_rows = []
        self.result_row_lookup = {}
        self.current_results_generated_at = ""
        self.result_total_var.set("0 risultati")
        self.result_counts_var.set("accepted 0 | maybe 0 | rejected 0")
        self.result_meta_var.set("Nessun output caricato")
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self._clear_detail_panel()
        self._update_result_actions()

    def _clear_detail_panel(self) -> None:
        self.detail_title_var.set("Nessun lead selezionato")
        self.detail_source_var.set("-")
        self.detail_screening_var.set("-")
        self.detail_contact_var.set("-")
        self.detail_decision_var.set("-")
        self.detail_date_var.set("-")
        self.detail_distance_var.set("-")
        self.detail_location_var.set("-")
        self.detail_company_var.set("-")
        self.detail_sector_var.set("-")
        self.detail_role_var.set("-")
        self.detail_schedule_var.set("-")
        self.detail_price_var.set("-")
        self.detail_link_var.set("")
        self.detail_website_var.set("")
        self._set_detail_raw_text("")

    def _set_detail_raw_text(self, text: str) -> None:
        self.detail_raw_text.configure(state="normal")
        self.detail_raw_text.delete("1.0", "end")
        if text:
            self.detail_raw_text.insert("1.0", text)
        self.detail_raw_text.configure(state="disabled")

    def _get_contact_message(self) -> str:
        value = self.contact_message_text.get("1.0", "end").strip()
        self.contact_message_var.set(value)
        return value

    def _start_auto_monitor(self) -> None:
        if self._selected_source_text() != "Subito Jobs":
            messagebox.showerror("Monitor automatico", "Il monitor automatico e disponibile solo quando la sorgente attiva e Subito Jobs.")
            return
        if self.process is not None:
            messagebox.showinfo("Monitor automatico", "Attendi la fine del processo corrente prima di avviare il monitor.")
            return
        interval_hours = self._parse_auto_interval_hours()
        if interval_hours is None:
            return
        try:
            command = self._build_scrape_command()
        except ValueError as exc:
            messagebox.showerror("Monitor automatico", str(exc))
            return
        if len(command) < 4 or command[3] != "subito":
            messagebox.showerror("Monitor automatico", "Il monitor automatico supporta solo lo scraping Subito.")
            return
        self.auto_monitor_enabled = True
        self.auto_monitor_command = list(command)
        self.auto_monitor_interval_ms = max(int(interval_hours * 3600 * 1000), 60_000)
        self._cancel_auto_monitor_timer()
        self.auto_monitor_status_var.set(f"Monitor attivo: run ogni {interval_hours:g} ore. Avvio iniziale in corso.")
        self.current_run_source = command[3]
        self._append_log(f"[auto] Monitor attivato: run ogni {interval_hours:g} ore.\n")
        self._start_process(list(command), kind="scrape", load_results=True)

    def _stop_auto_monitor(self) -> None:
        was_enabled = self.auto_monitor_enabled or self.auto_monitor_after_id is not None
        self.auto_monitor_enabled = False
        self.auto_monitor_command = None
        self.auto_monitor_interval_ms = 0
        self._cancel_auto_monitor_timer()
        self.auto_monitor_status_var.set("Monitor automatico disattivato")
        if was_enabled:
            self._append_log("[auto] Monitor automatico fermato.\n")

    def _parse_auto_interval_hours(self) -> float | None:
        raw_value = self.subito_auto_interval_hours_var.get().strip()
        if not raw_value:
            messagebox.showerror("Monitor automatico", "Inserisci ogni quante ore deve ripartire il monitor.")
            return None
        try:
            interval_hours = float(raw_value)
        except ValueError:
            messagebox.showerror("Monitor automatico", "Monitor ogni (ore) deve essere un numero valido, ad esempio 6.")
            return None
        if interval_hours <= 0:
            messagebox.showerror("Monitor automatico", "Monitor ogni (ore) deve essere maggiore di zero.")
            return None
        return interval_hours

    def _cancel_auto_monitor_timer(self) -> None:
        if self.auto_monitor_after_id is not None:
            self.root.after_cancel(self.auto_monitor_after_id)
            self.auto_monitor_after_id = None

    def _command_argument_value(self, command: list[str], flag: str) -> str:
        try:
            index = command.index(flag)
        except ValueError:
            return ""
        next_index = index + 1
        if next_index >= len(command):
            return ""
        return str(command[next_index] or "")

    def _schedule_next_auto_run(self, *, code: int) -> None:
        if not self.auto_monitor_enabled or not self.auto_monitor_command or self.auto_monitor_interval_ms <= 0:
            return
        self._cancel_auto_monitor_timer()
        next_run_at = datetime.now() + timedelta(milliseconds=self.auto_monitor_interval_ms)
        status = "ok" if code == 0 else f"errore exit {code}"
        self.auto_monitor_status_var.set(
            f"Monitor attivo: prossimo run alle {next_run_at.strftime('%H:%M')} ({status})."
        )
        self._append_log(
            f"[auto] Prossimo run pianificato per le {next_run_at.strftime('%H:%M:%S')}.\n"
        )
        self.auto_monitor_after_id = self.root.after(self.auto_monitor_interval_ms, self._run_scheduled_auto_monitor)

    def _run_scheduled_auto_monitor(self) -> None:
        self.auto_monitor_after_id = None
        if not self.auto_monitor_enabled or not self.auto_monitor_command:
            return
        if self.process is not None:
            retry_at = datetime.now() + timedelta(minutes=1)
            self.auto_monitor_status_var.set(
                f"Monitor attivo: processo ancora in corso, nuovo tentativo alle {retry_at.strftime('%H:%M')}."
            )
            self._append_log("[auto] Processo ancora attivo, ritento tra 60 secondi.\n")
            self.auto_monitor_after_id = self.root.after(60_000, self._run_scheduled_auto_monitor)
            return
        self.auto_monitor_status_var.set("Monitor attivo: avvio run programmato.")
        self.current_run_source = self.auto_monitor_command[3]
        self._append_log("[auto] Avvio run programmato.\n")
        self._start_process(list(self.auto_monitor_command), kind="scrape", load_results=True)

    def _start_scrape(self) -> None:
        try:
            command = self._build_scrape_command()
        except ValueError as exc:
            messagebox.showerror("Configurazione non valida", str(exc))
            return
        self._clear_results()
        self.current_run_source = command[3]
        self._start_process(command, kind="scrape", load_results=True)

    def _start_browser(self) -> None:
        try:
            command = self._build_browser_command()
        except ValueError as exc:
            messagebox.showerror("Browser non disponibile", str(exc))
            return
        self._scroll_to_widget(self.log_tab)
        self._start_process(command, kind="browser", load_results=False)

    def _start_contact_action(self) -> None:
        row = self._get_selected_row()
        if self.contact_submit_var.get() and row is not None:
            title = str(row.get("title", row.get("name", "")) or "Annuncio senza titolo").strip()
            if not messagebox.askyesno(
                "Conferma invio singolo",
                f"Stai per inviare il CV a questo annuncio:\n\n{title}\n\nConfermi?",
            ):
                return
        try:
            command = self._build_contact_command()
        except ValueError as exc:
            messagebox.showerror("Contatto non disponibile", str(exc))
            return
        self._scroll_to_widget(self.log_tab)
        self._start_process(command, kind="contact", load_results=False)

    def _start_batch_contact_selected(self) -> None:
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showerror("Invio CV", "Seleziona almeno un annuncio Subito.")
            return
        self._start_batch_contact(rows, scope_label="solo agli annunci selezionati")

    def _start_batch_contact_accepted(self) -> None:
        rows = self._get_accepted_subito_rows()
        if not rows:
            messagebox.showerror("Invio CV", "Non ci sono annunci consigliati disponibili.")
            return
        self._start_batch_contact(rows, scope_label="a tutti gli annunci consigliati")

    def _start_batch_contact(self, rows: list[dict], scope_label: str) -> None:
        if not self._confirm_batch_contact(rows, scope_label):
            return
        try:
            command = self._build_contact_batch_command(rows)
        except ValueError as exc:
            messagebox.showerror("Invio CV", str(exc))
            return
        self._scroll_to_widget(self.log_tab)
        self._start_process(command, kind="contact", load_results=False)

    def _confirm_batch_contact(self, rows: list[dict], scope_label: str) -> bool:
        unique_rows: list[dict] = []
        seen_links: set[str] = set()
        for row in rows:
            link = str(row.get("link", "") or "").strip()
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            unique_rows.append(row)
        if not unique_rows:
            messagebox.showerror("Invio CV", "Non ci sono annunci validi da inviare.")
            return False

        preview_lines = []
        for row in unique_rows[:8]:
            title = str(row.get("title", row.get("name", "")) or "Annuncio senza titolo").strip()
            location = str(row.get("location", "") or "").strip()
            preview_lines.append(f"- {title}" + (f" | {location}" if location else ""))
        remaining = len(unique_rows) - len(preview_lines)
        if remaining > 0:
            preview_lines.append(f"... altri {remaining} annunci")
        message = (
            f"Stai per inviare il CV {scope_label}.\n\n"
            f"Annunci coinvolti: {len(unique_rows)}\n"
            f"Allegato: {Path(self.attachment_path_var.get().strip()).name or '-'}\n\n"
            "Anteprima:\n"
            + "\n".join(preview_lines)
            + "\n\nConfermi?"
        )
        return messagebox.askyesno("Conferma invio CV", message)

    def _start_process(self, command: list[str], kind: str, load_results: bool) -> None:
        if self.process is not None:
            messagebox.showinfo("Processo in esecuzione", "Attendi la fine del processo corrente.")
            return
        clear_runtime_control_requests()
        self.process_kind = kind
        self.process_should_load_results = load_results
        self.vinted_access_warning_shown_for_process = False
        self.status_var.set("Running")
        self._append_log(f"$ {' '.join(command)}\n")
        self.run_button.configure(state="disabled")
        self.open_browser_button.configure(state="disabled")
        self.vinted_run_button.configure(state="disabled")
        if kind == "contact":
            self.contact_button.configure(state="disabled")
        if kind == "scrape":
            ui_result_json = self._command_argument_value(command, "--ui-result-json")
            if ui_result_json:
                self.ui_result_json_path = Path(ui_result_json).resolve()
            self.ui_result_json_path.parent.mkdir(parents=True, exist_ok=True)
            if self.ui_result_json_path.exists():
                self.ui_result_json_path.unlink()
            self.ui_result_json_mtime = 0.0
        self.process = subprocess.Popen(command, cwd=self.script_path.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def _stop_current_process(self) -> None:
        if self.process is None:
            messagebox.showinfo("Ferma processo", "Nessun processo in esecuzione.")
            return
        clear_runtime_control_requests()
        self._append_log("[control] Richiesta di stop immediato del processo.\n")
        self.status_var.set("Stopping")
        self.process.terminate()

    def _request_stop_after_current_item(self) -> None:
        if self.process is None:
            messagebox.showinfo("Ferma dopo attivita", "Nessun processo in esecuzione.")
            return
        request_stop_after_current_item()
        self._append_log("[control] Il processo si fermerà dopo l'annuncio corrente.\n")

    def _request_skip_current_item(self) -> None:
        if self.process is None:
            messagebox.showinfo("Salta attivita", "Nessun processo in esecuzione.")
            return
        request_skip_current_item()
        self._append_log("[control] Richiesto skip dell'attivita corrente.\n")

    def _build_scrape_command(self) -> list[str]:
        self.ui_result_json_path = (Path(self.output_dir_var.get()).resolve() / "_ui_last_result.json")
        cmd = [sys.executable, str(self.script_path), "run"]
        selected = self._selected_source_text()
        if selected == "Google Maps":
            if not self.google_search_var.get().strip():
                raise ValueError("Inserisci una query o URL per Google Maps.")
            if not self.google_max_results_var.get().strip().isdigit():
                raise ValueError("Max risultati Google Maps deve essere un intero positivo.")
            try:
                website_timeout = float(self.google_website_timeout_var.get().strip())
            except ValueError as exc:
                raise ValueError("Timeout audit sito deve essere un numero valido.") from exc
            if website_timeout <= 0:
                raise ValueError("Timeout audit sito deve essere maggiore di zero.")
            cmd += ["google_maps", "--search", self.google_search_var.get().strip(), "--max-results", self.google_max_results_var.get().strip()]
            for flag, var in (("--city", self.google_city_var), ("--province", self.google_province_var), ("--country", self.google_country_var)):
                if var.get().strip():
                    cmd += [flag, var.get().strip()]
            cmd += ["--website-timeout-seconds", str(website_timeout)]
            cmd.append("--exclude-sponsored" if self.google_exclude_sponsored_var.get() else "--no-exclude-sponsored")
            cmd.append("--include-details" if self.google_include_details_var.get() else "--no-include-details")
            cmd.append("--audit-websites" if self.google_audit_websites_var.get() else "--no-audit-websites")
        elif selected == "Vinted":
            search = self.vinted_search_var.get().strip()
            if not search:
                raise ValueError("Inserisci una ricerca o un URL Vinted.")
            max_results = self.vinted_max_results_var.get().strip()
            if not max_results.isdigit():
                raise ValueError("Max risultati Vinted deve essere un intero maggiore o uguale a zero.")
            db_path = self.vinted_db_path_var.get().strip()
            if not db_path:
                raise ValueError("Inserisci il percorso del database SQLite.")
            keep_open_seconds = self.vinted_keep_open_seconds_var.get().strip() or "0"
            if self.vinted_keep_browser_open_var.get() and not keep_open_seconds.isdigit():
                raise ValueError("I secondi per tenere aperto il browser Vinted devono essere un intero maggiore o uguale a zero.")
            cmd += [
                "vinted",
                "--search", search,
                "--max-results", max_results,
                "--db-path", db_path,
            ]
            if self.vinted_keep_browser_open_var.get():
                if int(keep_open_seconds) == 0:
                    cmd.append("--keep-browser-open")
                else:
                    cmd += ["--keep-open-seconds", keep_open_seconds]
            else:
                cmd.append("--no-keep-browser-open")
            if self.vinted_refresh_browser_profile_var.get():
                cmd.append("--refresh-browser-profile")
        elif selected == "Subito Jobs":
            if not self.subito_max_results_var.get().strip().isdigit():
                raise ValueError("Max risultati Subito deve essere un intero positivo.")
            raw_query = self.subito_query_var.get().strip()
            is_direct_url = raw_query.lower().startswith(("http://", "https://"))
            selected_job_keywords = self._effective_subito_job_keywords()
            query_argument_value = raw_query
            city_value = self.subito_city_var.get().strip()
            region_value = self.subito_region_var.get().strip()
            category_value = self.subito_category_var.get().strip()
            if (
                not is_direct_url
                and not raw_query
                and not selected_job_keywords
                and city_value
                and "," not in city_value
            ):
                query_argument_value = build_subito_search_url(
                    query_value="",
                    region=region_value or "lazio",
                    category=category_value or "offerte-lavoro",
                    city=city_value,
                )
                is_direct_url = True
            max_age_hours_raw = self.subito_max_age_hours_var.get().strip()
            if max_age_hours_raw and not max_age_hours_raw.isdigit():
                raise ValueError("Ultime ore deve essere un intero maggiore o uguale a zero.")
            max_age_hours = int(max_age_hours_raw) if max_age_hours_raw else 0
            if not self.subito_max_age_days_var.get().strip().isdigit():
                raise ValueError("Max eta annuncio deve essere un intero maggiore o uguale a zero.")
            exact_age_raw = self.subito_exact_age_days_var.get().strip()
            if exact_age_raw and not exact_age_raw.isdigit():
                raise ValueError("Solo giorno deve essere un intero maggiore o uguale a zero. Esempio: 1 = ieri.")
            try:
                float(self.subito_max_distance_var.get().strip())
            except ValueError as exc:
                raise ValueError("Distanza max Subito deve essere un numero valido.") from exc
            cmd += [
                "subito",
                "--max-results",
                self.subito_max_results_var.get().strip(),
                "--max-distance-km",
                self.subito_max_distance_var.get().strip(),
                "--max-age-hours",
                str(max_age_hours),
                "--max-age-days",
                self.subito_max_age_days_var.get().strip(),
            ]
            if exact_age_raw:
                cmd += ["--exact-age-days", exact_age_raw]
            if query_argument_value:
                cmd += ["--query", query_argument_value]
            for flag, value in (
                ("--region", region_value),
                ("--city", city_value),
                ("--category", category_value),
                ("--anchor-place", self.subito_anchor_place_var.get().strip()),
            ):
                if is_direct_url and flag in {"--region", "--city", "--category"}:
                    continue
                if value:
                    cmd += [flag, value]
            if selected_job_keywords and not is_direct_url:
                cmd += ["--job-keywords", ",".join(selected_job_keywords)]
            if self.subito_nearby_only_var.get():
                cmd.append("--nearby-only")
            include_details = self.subito_include_details_var.get() or self.subito_llm_screening_var.get() or max_age_hours > 0
            if include_details:
                cmd.append("--include-details")
            if self.subito_llm_screening_var.get():
                if not os.environ.get("OPENAI_API_KEY", "").strip():
                    raise ValueError("Per lo screening OpenAI devi impostare OPENAI_API_KEY.")
                model_name = self.subito_openai_model_var.get().strip()
                if not model_name:
                    raise ValueError("Inserisci un modello OpenAI valido per lo screening.")
                cmd += ["--llm-screening", "--openai-model", model_name]
        else:
            if not self.custom_url_var.get().strip() or not self.custom_item_selector_var.get().strip():
                raise ValueError("Inserisci URL e selector item per il sito custom.")
            cmd += [
                "custom_site",
                "--url", self.custom_url_var.get().strip(),
                "--item-selector", self.custom_item_selector_var.get().strip(),
                "--name-selector", self.custom_name_selector_var.get().strip(),
                "--phone-selector", self.custom_phone_selector_var.get().strip(),
                "--link-selector", self.custom_link_selector_var.get().strip(),
                "--cookie-reject-texts", self.custom_cookie_texts_var.get().strip(),
            ]
        cmd += self._browser_command_args()
        cmd += [
            "--format", self.output_format_var.get().strip(),
            "--output-dir", self.output_dir_var.get().strip(),
            "--ui-result-json", str(self.ui_result_json_path),
        ]
        if self.filename_var.get().strip():
            cmd += ["--filename", self.filename_var.get().strip()]
        return cmd

    def _build_browser_command(self) -> list[str]:
        selected = self._selected_source_text()
        if selected == "Google Maps":
            search = self._first_list_value(self.google_search_var.get())
            city = self._first_list_value(self.google_city_var.get())
            if search:
                url = build_google_maps_search_url(
                    search,
                    city,
                    self.google_province_var.get().strip(),
                    self.google_country_var.get().strip(),
                )
            else:
                url = "https://www.google.com/maps"
        elif selected == "Vinted":
            search = self.vinted_search_var.get().strip()
            if search.lower().startswith(("http://", "https://")):
                url = search
            elif search:
                url = f"https://www.vinted.it/catalog?search_text={quote_plus(search)}"
            else:
                url = "https://www.vinted.it/catalog"
        elif selected == "Subito Jobs":
            query = self.subito_query_var.get().strip()
            if query.lower().startswith(("http://", "https://")):
                url = query
            else:
                url = build_subito_search_url(
                    query_value=query,
                    region=self.subito_region_var.get().strip() or "lazio",
                    category=self.subito_category_var.get().strip() or "offerte-lavoro",
                    city=self._first_list_value(self.subito_city_var.get()),
                )
        else:
            url = self.custom_url_var.get().strip() or "https://www.google.com/"

        command = [
            sys.executable,
            str(self.script_path),
            "browser",
            "--url",
            url,
            "--keep-open-seconds",
            "0",
        ]
        if selected == "Vinted" and self.vinted_refresh_browser_profile_var.get():
            command.append("--refresh-browser-profile")
        command += self._browser_command_args()
        return command

    def _first_list_value(self, raw_value: str) -> str:
        return next((part.strip() for part in str(raw_value or "").replace(";", ",").split(",") if part.strip()), "")

    def _build_contact_command(self) -> list[str]:
        row = self._get_selected_row()
        if row is None:
            raise ValueError("Seleziona prima un annuncio nei risultati.")
        if str(row.get("source", "") or "").strip().lower() != "subito":
            raise ValueError("Il contatto automatico e disponibile solo per gli annunci Subito.")
        if not browser_mode_uses_profile(self.browser_mode_var.get()):
            raise ValueError("Per contattare su Subito usa sessione_persistente, chrome_normale o profilo_personalizzato.")

        link = str(row.get("link", "") or "").strip()
        if not link:
            raise ValueError("L annuncio selezionato non ha un link valido.")
        if self.contact_submit_var.get() and self._row_has_submitted_contact(row):
            raise ValueError("Questo annuncio risulta gia contattato con invio completato. Aprilo manualmente se vuoi verificarlo, ma evita un nuovo invio automatico.")

        attachment = self.attachment_path_var.get().strip()
        if attachment and not Path(attachment).exists():
            raise ValueError("Il file allegato selezionato non esiste.")
        message = self._get_contact_message()

        keep_open_raw = self.contact_keep_open_seconds_var.get().strip() or "120"
        if not keep_open_raw.isdigit():
            raise ValueError("Tieni browser aperto deve essere un intero positivo.")

        cmd = [sys.executable, str(self.script_path), "contact", "subito", "--link", link, "--keep-open-seconds", keep_open_raw]
        if attachment:
            cmd += ["--attachment", attachment]
        if message:
            cmd += ["--message", message]
        if self.contact_submit_var.get():
            cmd.append("--submit")
        cmd += self._browser_command_args()
        return cmd

    def _build_contact_batch_command(self, rows: list[dict]) -> list[str]:
        if not browser_mode_uses_profile(self.browser_mode_var.get()):
            raise ValueError("Per inviare i CV usa sessione_persistente, chrome_normale o profilo_personalizzato.")

        links = []
        seen: set[str] = set()
        for row in rows:
            if str(row.get("source", "") or "").strip().lower() != "subito":
                continue
            if self._row_has_submitted_contact(row):
                continue
            link = str(row.get("link", "") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            links.append(link)
        if not links:
            raise ValueError("Non ci sono link Subito nuovi da contattare. Gli annunci gia inviati vengono esclusi automaticamente.")

        attachment = self.attachment_path_var.get().strip()
        if not attachment:
            raise ValueError("Per inviare i CV seleziona prima un allegato.")
        if attachment and not Path(attachment).exists():
            raise ValueError("Il file allegato selezionato non esiste.")
        message = self._get_contact_message()
        keep_open_raw = self.contact_keep_open_seconds_var.get().strip() or "120"
        if not keep_open_raw.isdigit():
            raise ValueError("Tieni browser aperto deve essere un intero positivo.")
        if not self.contact_submit_var.get():
            raise ValueError("Per inviare davvero i CV devi spuntare 'Invia davvero il messaggio finale'.")

        links_file = self._write_contact_links_file(links)
        cmd = [
            sys.executable,
            str(self.script_path),
            "contact",
            "subito",
            "--links-file",
            str(links_file),
            "--keep-open-seconds",
            keep_open_raw,
            "--delay-between-seconds",
            "2",
        ]
        if attachment:
            cmd += ["--attachment", attachment]
        if message:
            cmd += ["--message", message]
        if self.contact_submit_var.get():
            cmd.append("--submit")
        cmd += self._browser_command_args()
        return cmd

    def _browser_command_args(self) -> list[str]:
        action_delay = self._validated_delay_text(self.action_delay_seconds_var.get().strip() or "1.5", "Pausa azioni")
        page_settle = self._validated_delay_text(self.page_settle_seconds_var.get().strip() or "3.0", "Attesa post-caricamento")
        args = [
            "--browser-mode", self.browser_mode_var.get().strip(),
            "--browser-user-data-dir", self.browser_user_data_dir_var.get().strip(),
            "--browser-profile-directory", self.browser_profile_directory_var.get().strip(),
            "--action-delay-seconds", action_delay,
            "--page-settle-seconds", page_settle,
        ]
        if self.slow_mode_var.get():
            args.append("--slow-mode")
        return args

    def _validated_delay_text(self, raw_value: str, label: str) -> str:
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(f"{label} deve essere un numero valido.") from exc
        if value < 0:
            raise ValueError(f"{label} non puo essere negativa.")
        return str(value)

    def _contact_status_label(self, row: dict) -> str:
        status = str(row.get("contact_status", "new") or "new").strip().lower()
        return CONTACT_STATUS_LABELS.get(status, CONTACT_STATUS_LABELS["new"])

    def _screening_status_label(self, row: dict) -> str:
        status = str(row.get("screening_decision", "") or "").strip().lower()
        return SCREENING_DECISION_LABELS.get(status, "-")

    def _screening_detail_label(self, row: dict) -> str:
        status = self._screening_status_label(row)
        score = str(row.get("screening_score", "") or "").strip()
        if status == "-":
            return "-"
        if score:
            return f"{status} ({score})"
        return status

    def _screening_score_value(self, row: dict) -> int:
        try:
            return int(row.get("screening_score", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _row_has_submitted_contact(self, row: dict) -> bool:
        return bool(row.get("contact_already_submitted", False))

    def _contact_detail_line(self, row: dict) -> str:
        status = self._contact_status_label(row)
        attempts = int(row.get("contact_attempt_count", 0) or 0)
        last_attempt_at = str(row.get("contact_last_attempt_at", "") or "").strip()
        last_submitted_at = str(row.get("contact_last_submitted_at", "") or "").strip()
        parts = [f"Stato contatto: {status}"]
        if attempts > 0:
            parts.append(f"tentativi {attempts}")
        if last_submitted_at:
            parts.append(f"ultimo invio {last_submitted_at}")
        elif last_attempt_at:
            parts.append(f"ultimo tentativo {last_attempt_at}")
        return " | ".join(parts)

    def _selected_subito_job_keywords(self) -> list[str]:
        selected_indexes = self.subito_jobs_listbox.curselection()
        keywords: list[str] = []
        for index in selected_indexes:
            if 0 <= index < len(SUBITO_JOB_OPTIONS):
                keywords.append(SUBITO_JOB_OPTIONS[index][0])
        return keywords

    def _selected_saved_profile_names(self) -> list[str]:
        if not hasattr(self, "subito_saved_profiles_listbox"):
            return []
        selected_indexes = self.subito_saved_profiles_listbox.curselection()
        names = list(self.subito_job_profiles.keys())
        selected_names: list[str] = []
        for index in selected_indexes:
            if 0 <= index < len(names):
                selected_names.append(names[index])
        return selected_names

    def _selected_nearby_city_values(self) -> list[str]:
        if not hasattr(self, "subito_nearby_cities_listbox"):
            return []
        selected_indexes = self.subito_nearby_cities_listbox.curselection()
        selected_values: list[str] = []
        for index in selected_indexes:
            if 0 <= index < len(SUBITO_NEARBY_CITY_OPTIONS):
                selected_values.append(SUBITO_NEARBY_CITY_OPTIONS[index])
        return selected_values

    def _apply_nearby_city_selection(self) -> None:
        selected_values = self._selected_nearby_city_values()
        if selected_values:
            self.subito_city_var.set(", ".join(selected_values))

    def _clear_nearby_city_selection(self) -> None:
        if hasattr(self, "subito_nearby_cities_listbox"):
            self.subito_nearby_cities_listbox.selection_clear(0, "end")
        self.subito_city_var.set("")

    def _effective_subito_job_keywords(self) -> list[str]:
        keywords: list[str] = []
        keywords.extend(self._selected_subito_job_keywords())
        keywords.extend(parse_job_keywords(self.subito_custom_job_keywords_var.get()))
        for profile_name in self._selected_saved_profile_names():
            keywords.extend(self.subito_job_profiles.get(profile_name, []))
        return normalize_job_keywords(keywords)

    def _refresh_subito_saved_profiles(self, preserve_selection: list[str] | None = None) -> None:
        self.subito_job_profiles = load_job_profiles()
        if not hasattr(self, "subito_saved_profiles_listbox"):
            return

        selected_names = preserve_selection if preserve_selection is not None else self._selected_saved_profile_names()
        self.subito_saved_profiles_listbox.delete(0, "end")
        for name in self.subito_job_profiles:
            self.subito_saved_profiles_listbox.insert("end", name)
        for index, name in enumerate(self.subito_job_profiles):
            if name in selected_names:
                self.subito_saved_profiles_listbox.selection_set(index)
        self._update_subito_job_keywords_preview()

    def _update_subito_job_keywords_preview(self) -> None:
        keywords = self._effective_subito_job_keywords()
        if keywords:
            self.subito_profiles_info_var.set(f"Keyword attive: {', '.join(keywords)}")
        else:
            self.subito_profiles_info_var.set("Keyword attive: nessuna. Seleziona lavori, profili o keyword extra.")

    def _save_subito_profile(self) -> None:
        profile_name = self.subito_profile_name_var.get().strip()
        if not profile_name:
            messagebox.showerror("Profili lavoro", "Inserisci un nome profilo.")
            return
        if is_builtin_job_profile(profile_name):
            messagebox.showerror("Profili lavoro", "Questo nome e gia usato da un profilo predefinito.")
            return
        keywords = self._effective_subito_job_keywords()
        if not keywords:
            messagebox.showerror("Profili lavoro", "Seleziona almeno una keyword prima di salvare il profilo.")
            return
        save_custom_job_profile(profile_name, keywords)
        self._refresh_subito_saved_profiles(preserve_selection=[*self._selected_saved_profile_names(), profile_name])
        self.subito_profile_name_var.set(profile_name)
        messagebox.showinfo("Profili lavoro", f"Profilo '{profile_name}' salvato.")

    def _delete_selected_subito_profiles(self) -> None:
        selected_names = self._selected_saved_profile_names()
        if not selected_names:
            messagebox.showerror("Profili lavoro", "Seleziona almeno un profilo da eliminare.")
            return

        skipped_builtin: list[str] = []
        deleted_any = False
        for name in selected_names:
            if is_builtin_job_profile(name):
                skipped_builtin.append(name)
                continue
            deleted_any = delete_custom_job_profile(name) or deleted_any

        self._refresh_subito_saved_profiles(preserve_selection=[])
        if skipped_builtin and deleted_any:
            messagebox.showinfo(
                "Profili lavoro",
                "Profili personalizzati eliminati. I profili predefiniti non possono essere cancellati: "
                + ", ".join(skipped_builtin),
            )
        elif skipped_builtin:
            messagebox.showinfo(
                "Profili lavoro",
                "I profili predefiniti non possono essere cancellati: " + ", ".join(skipped_builtin),
            )
        elif deleted_any:
            messagebox.showinfo("Profili lavoro", "Profili personalizzati eliminati.")

    def _clear_saved_profile_selection(self) -> None:
        if hasattr(self, "subito_saved_profiles_listbox"):
            self.subito_saved_profiles_listbox.selection_clear(0, "end")
        self._update_subito_job_keywords_preview()

    def _write_contact_links_file(self, links: list[str]) -> Path:
        output_dir = Path(self.output_dir_var.get()).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        links_file = output_dir / "_ui_contact_links.json"
        links_file.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
        return links_file

    def _read_process_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self.log_queue.put(line)
        code = self.process.wait()
        self.log_queue.put(f"\nProcesso terminato con exit code {code}.\n")
        self.log_queue.put(f"__DONE__:{code}")

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if line.startswith("__VINTED_ACCESS__:"):
                try:
                    payload = json.loads(line.split(":", 1)[1].strip())
                except (json.JSONDecodeError, IndexError):
                    payload = {}
                self._apply_vinted_access_status(payload, notify=True)
                self._append_log(
                    "[vinted-access] "
                    + (
                        "marker account trovato.\n"
                        if bool(payload.get("marker_present"))
                        else "marker account assente.\n"
                    )
                )
                continue
            if line.startswith("__VINTED_LOGIN_REQUIRED__:"):
                try:
                    payload = json.loads(line.split(":", 1)[1].strip())
                except (json.JSONDecodeError, IndexError):
                    payload = {}
                self._append_log("[vinted-login] Login richiesto, attendo conferma utente.\n")
                self._handle_vinted_login_required(payload)
                continue
            if line.startswith("__DONE__:"):
                code = int(line.split(":", 1)[1])
                completed_kind = self.process_kind
                should_load_results = self.process_should_load_results
                self.process = None
                clear_runtime_control_requests()
                self.process_kind = ""
                self.process_should_load_results = False
                self.vinted_access_warning_shown_for_process = False
                self.vinted_login_prompt_open = False
                self.status_var.set("Idle" if code == 0 else "Error")
                self._update_vinted_profile_status()
                self.run_button.configure(state="normal")
                self.open_browser_button.configure(state="normal")
                self.vinted_run_button.configure(state="normal")
                self._update_result_actions()
                if code == 0 and should_load_results:
                    self._load_results()
                if completed_kind == "scrape" and self.auto_monitor_enabled:
                    self._schedule_next_auto_run(code=code)
                elif completed_kind == "scrape" and self.current_run_source == "vinted" and code != 0:
                    self.vinted_status_var.set("Ricerca Vinted fallita. Controlla il log per il dettaglio.")
                elif completed_kind == "contact":
                    if code == 0:
                        if self.ui_result_json_path.exists():
                            self._load_results()
                        messagebox.showinfo("Contatto pronto", "Flusso Contatta eseguito. Controlla il browser e il log.")
                    else:
                        messagebox.showerror("Contatto fallito", "Il flusso Contatta non e stato completato. Controlla il log.")
                continue
            self._append_log(line)
        self._maybe_load_live_results()
        self.root.after(150, self._drain_logs)

    def _maybe_load_live_results(self) -> None:
        if self.process is None:
            return
        if self.process_kind != "scrape" or not self.process_should_load_results:
            return
        if not self.ui_result_json_path.exists():
            return
        try:
            current_mtime = self.ui_result_json_path.stat().st_mtime
        except OSError:
            return
        if current_mtime <= self.ui_result_json_mtime:
            return
        self.ui_result_json_mtime = current_mtime
        self._load_results()

    def _append_log(self, text: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _selected_vinted_signal_filter(self) -> str:
        return str(self.vinted_signal_filter_var.get() or "tutti").strip().lower() or "tutti"

    def _filter_vinted_rows(self, rows: list[dict]) -> list[dict]:
        selected_filter = self._selected_vinted_signal_filter()
        if selected_filter == "tutti":
            return list(rows)
        filtered_rows: list[dict] = []
        for row in rows:
            tag = str(row.get("tag", "") or "").strip().lower()
            evaluation_label = str(row.get("evaluation_label", "") or "").strip().lower()
            if selected_filter == "ricercato":
                if tag == "ricercato":
                    filtered_rows.append(row)
                continue
            if evaluation_label == selected_filter:
                filtered_rows.append(row)
        return filtered_rows

    def _handle_vinted_signal_filter_change(self) -> None:
        if self.current_result_source != "vinted":
            return
        if not self.ui_result_json_path.exists():
            return
        self._load_results()

    def _load_vinted_database_results(self) -> None:
        db_path = self.vinted_db_path_var.get().strip()
        if not db_path:
            messagebox.showerror("Database Vinted", "Inserisci il percorso del database SQLite.")
            return
        raw_limit = self.vinted_db_limit_var.get().strip() or "500"
        if not raw_limit.isdigit():
            messagebox.showerror("Database Vinted", "Il limite righe deve essere un intero maggiore o uguale a zero.")
            return
        self.vinted_status_var.set("Caricamento del database Vinted...")
        try:
            rows, meta = load_vinted_rows(
                db_path=db_path,
                search_term=self.vinted_db_filter_var.get().strip(),
                tag_filter="",
                limit=int(raw_limit),
            )
        except (FileNotFoundError, ValueError) as exc:
            self.vinted_status_var.set("Impossibile caricare il database Vinted.")
            messagebox.showerror("Database Vinted", str(exc))
            return

        generated_at = datetime.now().isoformat(timespec="seconds")
        payload = {
            "source": "vinted",
            "generated_at": generated_at,
            "row_count": len(rows),
            "meta": meta,
            "rows": rows,
        }
        self.ui_result_json_path.parent.mkdir(parents=True, exist_ok=True)
        self.ui_result_json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.current_run_source = "vinted"
        self._load_results()

    def _load_results(self) -> None:
        if not self.ui_result_json_path.exists():
            self.result_meta_var.set("Run completato, ma il JSON UI non e stato trovato.")
            return
        try:
            payload = json.loads(self.ui_result_json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.result_meta_var.set(f"Errore caricamento risultati: {exc}")
            return
        try:
            self.ui_result_json_mtime = self.ui_result_json_path.stat().st_mtime
        except OSError:
            pass
        generated_at = str(payload.get("generated_at", "") or "")
        rows = annotate_rows_with_contact_history(payload.get("rows", []))
        for index, row in enumerate(rows, start=1):
            if generated_at and not str(row.get("extracted_at", "") or "").strip():
                row["extracted_at"] = generated_at
            if row.get("extracted_order") in ("", None):
                row["extracted_order"] = index
        source = str(payload.get("source", self.current_run_source) or "").strip().lower()
        self.current_result_source = source
        self.current_result_meta = dict(payload.get("meta", {}) or {})
        if source == "vinted":
            self._sync_vinted_access_from_meta(self.current_result_meta)
        self._configure_results_columns(source)
        self._configure_result_panels(source)
        lead_sort_modes = {
            "Score opportunita",
            "Priorita lead",
            "Nome attivita",
            "Categoria Maps",
            "Valutazione Maps",
            "Numero recensioni",
        }
        vinted_sort_modes = {
            "Prezzo Vinted",
            "Ricerca Vinted",
            "Preferiti Vinted",
            "Valutazione Vinted",
            "Nome attivita",
            "Data estrazione",
        }
        current_sort = self.result_sort_var.get().strip()
        if source == "google_maps" and current_sort not in lead_sort_modes:
            current_sort = "Score opportunita"
        elif source == "vinted" and current_sort not in vinted_sort_modes:
            current_sort = "Prezzo Vinted"
        elif source not in {"google_maps", "vinted"} and current_sort in lead_sort_modes | vinted_sort_modes:
            current_sort = "Priorita consigliata"
        self._updating_result_sort_var = True
        self.result_sort_var.set(current_sort)
        self._updating_result_sort_var = False
        self.result_sort_reverse = RESULT_SORT_DEFAULT_DESC.get(current_sort, False)
        self.result_sort_active_column = RESULT_SORT_MODE_DEFAULT_COLUMN.get(current_sort, "")
        self._update_result_heading_labels()
        self.current_results_generated_at = generated_at
        if source == "vinted":
            rows = self._filter_vinted_rows(rows)
        self.result_rows = self._sorted_result_rows(rows)
        self.result_row_lookup = {}
        meta = self.current_result_meta
        if source == "google_maps":
            priority_counts = dict(meta.get("lead_priority_counts", {}))
            if not priority_counts:
                priority_counts = {"alta": 0, "media": 0, "bassa": 0}
                for row in rows:
                    priority = str(row.get("lead_priority", "") or "").strip().lower()
                    if priority in priority_counts:
                        priority_counts[priority] += 1
            self.result_total_var.set(f"{len(rows)} lead")
            self.result_counts_var.set(
                f"alta {priority_counts.get('alta', 0)} | "
                f"media {priority_counts.get('media', 0)} | "
                f"bassa {priority_counts.get('bassa', 0)}"
            )
            meta_parts = [
                f"Sorgente: Google Maps",
                f"Ricerche: {meta.get('search_count', 1)}",
                f"Siti analizzati: {meta.get('audited_website_count', 0)}",
                f"Generato: {payload.get('generated_at', '-')}",
            ]
            total_errors = len(meta.get("search_errors", []) or []) + len(meta.get("detail_errors", []) or []) + len(meta.get("audit_errors", []) or [])
            if total_errors:
                meta_parts.append(f"Errori isolati: {total_errors}")
            self.result_meta_var.set(" | ".join(meta_parts))
            self._render_results_rows(self.result_rows)
            self._update_result_actions()
            self._scroll_to_widget(self.results_tab)
            return
        if source == "vinted":
            active_filter = self._selected_vinted_signal_filter()
            self.result_total_var.set(f"{len(rows)} prodotti")
            if meta.get("loaded_from_db"):
                self.result_counts_var.set(
                    f"articoli DB {meta.get('db_total_items', 0)} | "
                    f"tag DB {meta.get('db_total_search_hits', 0)} | "
                    f"mostrati {len(rows)}"
                )
                if meta.get("db_created") and not rows:
                    self.vinted_status_var.set("Database creato correttamente. Non contiene ancora prodotti: avvia la prima ricerca.")
                else:
                    self.vinted_status_var.set(
                        f"Database caricato: {len(rows)} righe mostrate su {meta.get('db_filtered_search_hits', len(rows))} con filtro {active_filter}."
                    )
            else:
                self.result_counts_var.set(
                    f"nuovi articoli {meta.get('new_items', 0)} | "
                    f"aggiornati {meta.get('updated_items', 0)} | "
                    f"nuovi tag {meta.get('new_search_hits', 0)}"
                )
                self.vinted_status_var.set(
                    f"Ricerca completata: {len(rows)} prodotti visibili con filtro {active_filter}, {meta.get('new_items', 0)} nuovi nel database."
                )
            self.result_meta_var.set(
                " | ".join(
                    (
                        "Sorgente: Vinted",
                        f"Ricerca: {meta.get('db_search_filter', '') or meta.get('search_term', 'tutte')}",
                        f"Tag: {meta.get('db_tag_filter', '') or meta.get('tag', '') or 'tutti'}",
                        f"Filtro rapido: {active_filter}",
                        f"Database: {meta.get('db_path', '-')}",
                        f"Generato: {payload.get('generated_at', '-')}",
                    )
                )
            )
            self._render_results_rows(self.result_rows)
            self._update_result_actions()
            self._scroll_to_widget(self.results_tab)
            return
        counts = dict(meta.get("geo_counts", {}))
        contact_counts = summarize_contact_status(rows)
        screening_counts = dict(meta.get("screening_counts", {}))
        age_counts = dict(meta.get("age_filter_counts", {}))
        if not counts:
            counts = {"accepted": 0, "maybe": 0, "rejected": 0}
            for row in rows:
                counts[str(row.get("geo_decision", "maybe"))] = counts.get(str(row.get("geo_decision", "maybe")), 0) + 1
        if not screening_counts:
            screening_counts = {"candida": 0, "valuta": 0, "no": 0}
            for row in rows:
                screening = str(row.get("screening_decision", "") or "").strip().lower()
                if screening in screening_counts:
                    screening_counts[screening] += 1
        self.result_total_var.set(f"{len(rows)} risultati")
        counts_parts = []
        if any(screening_counts.values()):
            counts_parts.extend(
                [
                    f"candida {screening_counts.get('candida', 0)}",
                    f"valuta {screening_counts.get('valuta', 0)}",
                    f"no {screening_counts.get('no', 0)}",
                ]
            )
        counts_parts.extend(
            [
                f"accepted {counts.get('accepted', 0)}",
                f"maybe {counts.get('maybe', 0)}",
                f"fresh {age_counts.get('fresh', 0)}" if any(age_counts.values()) else "",
                f"nuovi {contact_counts.get('new', 0)}",
                f"inviati {contact_counts.get('submitted', 0)}",
            ]
        )
        self.result_counts_var.set(" | ".join(part for part in counts_parts if part))
        meta_parts = [
            f"Sorgente: {payload.get('source', self.current_run_source)}",
            f"Anchor: {meta.get('geo_anchor_place', '-')}",
            f"Generato: {payload.get('generated_at', '-')}",
        ]
        city_values = meta.get("cities", [])
        if isinstance(city_values, list) and city_values:
            meta_parts.append(f"Citta: {', '.join(str(value) for value in city_values)}")
        exact_label = str(meta.get("age_filter_exact_label", "") or "").strip()
        max_hours = int(meta.get("age_filter_max_hours", 0) or 0)
        if max_hours > 0:
            meta_parts.append(f"Ultime ore: {max_hours}h")
        elif exact_label:
            meta_parts.append(f"Giorno: {exact_label}")
        elif meta.get("age_filter_enabled"):
            meta_parts.append(f"Eta max: {meta.get('age_filter_max_days', '-') }g")
        removed_count = int(meta.get("age_filter_removed_count", 0) or 0)
        if removed_count > 0:
            meta_parts.append(f"Scartati per data: {removed_count}")
        if meta.get("screening_enabled"):
            meta_parts.append(f"OpenAI: {meta.get('screening_model', '-')}")
        self.result_meta_var.set(" | ".join(meta_parts))
        self._render_results_rows(self.result_rows)
        self._update_result_actions()
        self._scroll_to_widget(self.results_tab)

    def _render_results_rows(self, rows: list[dict]) -> None:
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self.result_row_lookup = {}
        for index, row in enumerate(rows):
            row_id = f"row-{index}"
            self.result_row_lookup[row_id] = row
            if str(row.get("source", "") or "").strip().lower() == "google_maps":
                values = (
                    str(row.get("lead_priority", "") or "-"),
                    str(row.get("opportunity_score", "") or "-"),
                    str(row.get("name", row.get("title", "")) or ""),
                    str(row.get("category", row.get("sector", "")) or ""),
                    str(row.get("city", row.get("location", "")) or ""),
                    str(row.get("phone", "") or ""),
                    str(row.get("email", "") or ""),
                    str(row.get("website_status", "") or "-"),
                    str(row.get("rating", "") or "-"),
                    str(row.get("reviews_count", "") or "-"),
                )
                priority = str(row.get("lead_priority", "") or "").lower()
                tag = priority if priority in LEAD_PRIORITY_ORDER else ""
            elif str(row.get("source", "") or "").strip().lower() == "vinted":
                favorite_count = row.get("favorite_count")
                favorite_display = (
                    str(favorite_count)
                    if favorite_count not in ("", None)
                    else "-"
                )
                evaluation_label = str(row.get("evaluation_label", "") or "-")
                values = (
                    str(row.get("search_term", "") or ""),
                    evaluation_label,
                    str(row.get("tag", "") or "-"),
                    str(row.get("name", "") or ""),
                    self._vinted_total_display_value(row),
                    favorite_display,
                    str(row.get("item_id", "") or ""),
                    str(row.get("times_seen", 1) or 1),
                    self._extracted_display_value(row),
                    str(row.get("link", "") or ""),
                )
                if evaluation_label == "da valutare assolutamente":
                    tag = "vinted_hot"
                elif evaluation_label == "da valutare":
                    tag = "vinted_review"
                elif str(row.get("tag", "") or "").strip().lower() == "ricercato":
                    tag = "vinted_badge"
                else:
                    tag = ""
            else:
                distance = row.get("distance_km", "-")
                if distance in ("", None):
                    distance_display = "-"
                elif isinstance(distance, float):
                    distance_display = f"{distance:.1f}"
                else:
                    distance_display = str(distance)
                decision = str(row.get("geo_decision", "") or "-")
                values = (
                    self._screening_status_label(row),
                    str(row.get("screening_score", "") or "-"),
                    self._contact_status_label(row),
                    decision,
                    str(row.get("published_at", "") or "-"),
                    self._extracted_display_value(row),
                    distance_display,
                    str(row.get("location", "") or ""),
                    str(row.get("title", row.get("name", "")) or ""),
                    str(row.get("company", "") or ""),
                    str(row.get("schedule", "") or ""),
                )
                tag = decision if decision in {"accepted", "maybe", "rejected"} else ""
            self.results_tree.insert("", "end", iid=row_id, values=values, tags=(tag,))
        if rows:
            first_row_id = "row-0"
            self.results_tree.focus(first_row_id)
            self.results_tree.see(first_row_id)
            self._populate_detail_panel(rows[0])
            self.results_tree.selection_remove(first_row_id)
        else:
            self._clear_detail_panel()

    def _sorted_result_rows(self, rows: list[dict]) -> list[dict]:
        return sorted(list(rows), key=self._result_sort_key, reverse=self.result_sort_reverse)

    def _apply_result_sort(self) -> None:
        if not getattr(self, "results_tree", None):
            return
        if not self.result_rows:
            return
        self.result_rows = self._sorted_result_rows(self.result_rows)
        self._render_results_rows(self.result_rows)
        self._update_result_actions()
        self._update_result_heading_labels()

    def _handle_result_sort_change(self) -> None:
        if self._updating_result_sort_var:
            return
        sort_mode = self.result_sort_var.get().strip()
        self.result_sort_reverse = RESULT_SORT_DEFAULT_DESC.get(sort_mode, False)
        self.result_sort_active_column = RESULT_SORT_MODE_DEFAULT_COLUMN.get(sort_mode, "")
        if not self.result_rows:
            self._update_result_heading_labels()
            return
        self._apply_result_sort()

    def _on_results_heading_click(self, column: str) -> None:
        sort_mode = RESULT_SORT_COLUMN_MAP.get(column)
        if not sort_mode:
            return
        if self.result_sort_var.get().strip() == sort_mode:
            self.result_sort_reverse = not self.result_sort_reverse
            self.result_sort_active_column = column
            self._apply_result_sort()
            return
        self._updating_result_sort_var = True
        self.result_sort_var.set(sort_mode)
        self._updating_result_sort_var = False
        self.result_sort_reverse = RESULT_SORT_DEFAULT_DESC.get(sort_mode, False)
        self.result_sort_active_column = column
        self._apply_result_sort()

    def _update_result_heading_labels(self) -> None:
        if not getattr(self, "results_tree", None):
            return
        active_column = self.result_sort_active_column or RESULT_SORT_MODE_DEFAULT_COLUMN.get(self.result_sort_var.get().strip(), "")
        arrow = " ↓" if self.result_sort_reverse else " ↑"
        for column, base_label in self.results_column_labels.items():
            label = base_label
            if column == active_column:
                label = f"{base_label}{arrow}"
            self.results_tree.heading(column, text=label, command=lambda current_key=column: self._on_results_heading_click(current_key))

    def _result_sort_key(self, row: dict) -> tuple:
        sort_mode = self.result_sort_var.get().strip()
        if sort_mode == "Prezzo Vinted":
            price = row.get("total_price_value", row.get("price_value"))
            return (
                price in (None, ""),
                self._safe_float(price, default=0),
                str(row.get("name", "") or "").lower(),
            )
        if sort_mode == "Ricerca Vinted":
            return (
                str(row.get("search_term", "") or "").lower(),
                str(row.get("name", "") or "").lower(),
            )
        if sort_mode == "Preferiti Vinted":
            favorite_count = row.get("favorite_count")
            return (
                favorite_count in (None, ""),
                -self._safe_int(favorite_count, default=0),
                str(row.get("name", "") or "").lower(),
            )
        if sort_mode == "Valutazione Vinted":
            evaluation_label = str(row.get("evaluation_label", "") or "").strip().lower()
            evaluation_rank = {
                "da valutare assolutamente": 0,
                "da valutare": 1,
                "": 2,
            }.get(evaluation_label, 3)
            tag_rank = 0 if str(row.get("tag", "") or "").strip().lower() == "ricercato" else 1
            return (
                evaluation_rank,
                tag_rank,
                -self._safe_int(row.get("favorite_count"), default=0),
                str(row.get("name", "") or "").lower(),
            )
        if sort_mode == "Score opportunita":
            return (
                self._safe_float(row.get("opportunity_score"), default=-1),
                str(row.get("name", "") or "").lower(),
            )
        if sort_mode == "Priorita lead":
            return (
                LEAD_PRIORITY_ORDER.get(str(row.get("lead_priority", "") or "").lower(), 99),
                -self._safe_float(row.get("opportunity_score"), default=-1),
                str(row.get("name", "") or "").lower(),
            )
        if sort_mode == "Nome attivita":
            return (str(row.get("name", "") or "").lower(),)
        if sort_mode == "Categoria Maps":
            return (
                str(row.get("category", "") or "").lower(),
                str(row.get("name", "") or "").lower(),
            )
        if sort_mode == "Valutazione Maps":
            return (
                self._safe_float(row.get("rating"), default=-1),
                self._safe_int(row.get("reviews_count"), default=-1),
            )
        if sort_mode == "Numero recensioni":
            return (
                self._safe_int(row.get("reviews_count"), default=-1),
                self._safe_float(row.get("rating"), default=-1),
            )
        contact_status = str(row.get("contact_status", "") or "new").strip().lower()
        published = self._published_datetime_for_sort(row)
        extracted = self._parse_iso_datetime(str(row.get("extracted_at", "") or self.current_results_generated_at or ""))
        extracted_order = self._safe_int(row.get("extracted_order"), default=0)
        distance = row.get("distance_km")
        title = str(row.get("title", row.get("name", "")) or "").lower()
        company = str(row.get("company", "") or "").lower()
        location = str(row.get("location", "") or "").lower()
        schedule = str(row.get("schedule", "") or "").lower()
        contact_attempt = self._parse_iso_datetime(str(row.get("contact_last_attempt_at", "") or ""))
        screening_score = self._screening_score_value(row)
        if sort_mode == "Contatto":
            contact_status = str(row.get("contact_status", "") or "new").strip().lower()
            return (
                CONTACT_STATUS_SORT_ORDER.get(contact_status, 99),
                contact_attempt is None,
                contact_attempt.timestamp() if contact_attempt else 0,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Score candidatura":
            return (
                screening_score,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Decisione geo":
            return (
                DECISION_ORDER.get(str(row.get("geo_decision", "maybe")), 99),
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Data annuncio":
            published = self._published_datetime_for_sort(row)
            return (
                published is None,
                published.timestamp() if published else 0,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Data estrazione":
            return (
                extracted is None,
                extracted.timestamp() if extracted else 0,
                extracted_order,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Distanza":
            return (
                distance is None,
                distance if distance is not None else 9999,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Luogo":
            return (
                location,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Titolo":
            return (
                title,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Azienda":
            return (
                company,
                self._fallback_priority_sort_key(row),
            )
        if sort_mode == "Orario":
            return (
                schedule,
                self._fallback_priority_sort_key(row),
            )
        return self._fallback_priority_sort_key(row)

    def _fallback_priority_sort_key(self, row: dict) -> tuple:
        if str(row.get("source", "") or "").strip().lower() == "google_maps":
            return (
                LEAD_PRIORITY_ORDER.get(str(row.get("lead_priority", "") or "").lower(), 99),
                -self._safe_float(row.get("opportunity_score"), default=-1),
                str(row.get("name", "") or "").lower(),
            )
        published = self._published_datetime_for_sort(row)
        distance = row.get("distance_km")
        return (
            SCREENING_DECISION_ORDER.get(str(row.get("screening_decision", "") or "").strip().lower(), 1),
            -self._screening_score_value(row),
            published is None,
            -(published.timestamp()) if published else 0,
            DECISION_ORDER.get(str(row.get("geo_decision", "maybe")), 99),
            distance is None,
            distance if distance is not None else 9999,
            str(row.get("title", row.get("name", ""))).lower(),
        )

    def _published_datetime_for_sort(self, row: dict) -> datetime | None:
        published_value = str(
            row.get("published_datetime_iso", "")
            or row.get("published_date_iso", "")
            or row.get("published_at", "")
            or ""
        )
        return self._parse_published_at(published_value)

    def _parse_published_at(self, value: str) -> datetime | None:
        raw = value.strip()
        if not raw:
            return None
        try:
            if "T" in raw or (len(raw) == 10 and raw[4] == "-" and raw[7] == "-"):
                return datetime.fromisoformat(raw)
        except ValueError:
            pass
        return to_datetime_for_sorting(raw)

    def _parse_iso_datetime(self, value: str) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _extracted_display_value(self, row: dict) -> str:
        raw_value = str(row.get("extracted_at", "") or self.current_results_generated_at or "").strip()
        parsed = self._parse_iso_datetime(raw_value)
        if parsed is None:
            return raw_value or "-"
        return parsed.strftime("%Y-%m-%d %H:%M")

    def _vinted_total_display_value(self, row: dict) -> str:
        total = str(row.get("total_price", "") or "").strip()
        if total:
            return total
        price = row.get("price")
        if price not in ("", None):
            return str(price)
        raw_value = row.get("total_price_value", row.get("price_value"))
        if raw_value in ("", None):
            return ""
        return str(raw_value)

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _safe_float(self, value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _handle_result_selection(self) -> None:
        row = self._get_selected_row()
        if row is None:
            self._clear_detail_panel()
        else:
            self._populate_detail_panel(row)
        self._update_result_actions()

    def _populate_detail_panel(self, row: dict) -> None:
        source = str(row.get("source", "") or "").strip().lower()
        self._configure_detail_labels(source)
        if source == "vinted":
            favorite_count = row.get("favorite_count")
            favorite_display = str(favorite_count) if favorite_count not in ("", None) else "-"
            evaluation_label = str(row.get("evaluation_label", "") or "-")
            offer_display = "si" if row.get("offer_available") else "no"
            self.detail_title_var.set(str(row.get("name", "") or "Prodotto senza nome"))
            self.detail_source_var.set("Vinted")
            self.detail_screening_var.set(evaluation_label)
            self.detail_contact_var.set(str(row.get("tag", "") or "-"))
            self.detail_decision_var.set(favorite_display)
            self.detail_date_var.set(str(row.get("price", "") or "-"))
            self.detail_distance_var.set(str(row.get("total_price", "") or row.get("price", "") or "-"))
            self.detail_location_var.set(self._extracted_display_value(row))
            self.detail_company_var.set(str(row.get("shipping_price", "") or "-"))
            self.detail_sector_var.set(str(row.get("name", "") or "-"))
            self.detail_role_var.set(str(row.get("search_term", "") or "-"))
            self.detail_schedule_var.set(str(row.get("first_seen_at", row.get("extracted_at", "")) or "-"))
            self.detail_price_var.set(str(row.get("times_seen", 1) or 1))
            self.detail_link_var.set(str(row.get("link", "") or ""))
            self.detail_website_var.set(str(row.get("db_path", "") or "-"))
            detail_text_parts = []
            total_value = str(row.get("total_price", "") or "").strip()
            shipping_value = str(row.get("shipping_price", "") or "").strip()
            offer_text = str(row.get("offer_text", "") or "").strip()
            item_id = str(row.get("item_id", "") or "").strip()
            if evaluation_label and evaluation_label != "-":
                detail_text_parts.append(f"Valutazione: {evaluation_label}")
            if favorite_display != "-":
                detail_text_parts.append(f"Preferiti: {favorite_display}")
            if str(row.get("tag", "") or "").strip():
                detail_text_parts.append(f"Tag: {str(row.get('tag', '') or '').strip()}")
            if item_id:
                detail_text_parts.append(f"ID articolo: {item_id}")
            if total_value:
                detail_text_parts.append(f"Totale stimato: {total_value}")
            if shipping_value:
                detail_text_parts.append(f"Spedizione: {shipping_value}")
            if offer_text:
                detail_text_parts.append(f"Pulsante offerta: {offer_text}")
            else:
                detail_text_parts.append(f"Offerta disponibile: {offer_display}")
            description = str(row.get("description", "") or "").strip()
            if description:
                if detail_text_parts:
                    detail_text_parts.append("")
                detail_text_parts.append("Descrizione estratta:")
                detail_text_parts.append(description)
            raw_text = str(row.get("raw_text", "") or "").strip()
            if raw_text:
                if detail_text_parts:
                    detail_text_parts.append("")
                detail_text_parts.append("Testo scheda:")
                detail_text_parts.append(raw_text)
            self._set_detail_raw_text("\n".join(detail_text_parts))
            return
        if source == "google_maps":
            self.detail_title_var.set(str(row.get("name", "") or "Attivita senza nome"))
            self.detail_source_var.set("Google Maps")
            priority = str(row.get("lead_priority", "") or "-")
            score = str(row.get("opportunity_score", "") or "-")
            self.detail_screening_var.set(f"{priority} ({score})")
            self.detail_contact_var.set(str(row.get("lead_status", "") or "nuovo"))
            self.detail_decision_var.set(str(row.get("website_status", "") or "-"))
            self.detail_date_var.set(self._extracted_display_value(row))
            rating = str(row.get("rating", "") or "-")
            self.detail_distance_var.set(rating)
            self.detail_location_var.set(str(row.get("address", row.get("location", "")) or "-"))
            self.detail_company_var.set(str(row.get("website_emails", row.get("email", "")) or "-"))
            self.detail_sector_var.set(str(row.get("category", "") or "-"))
            self.detail_role_var.set(str(row.get("phone", "") or "-"))
            self.detail_schedule_var.set(str(row.get("reviews_count", "") or "-"))
            response_ms = row.get("website_response_ms", "")
            self.detail_price_var.set(f"{response_ms} ms" if response_ms not in ("", None) else "-")
            self.detail_link_var.set(str(row.get("link", "") or ""))
            self.detail_website_var.set(str(row.get("website_final_url", "") or row.get("website", "") or ""))

            detail_text_parts = []
            lead_reason = str(row.get("lead_reason", "") or "").strip()
            if lead_reason:
                detail_text_parts.append(f"Motivo priorita: {lead_reason}")
            for label, field in (
                ("Titolo sito", "website_title"),
                ("Descrizione sito", "website_meta_description"),
                ("Tecnologia", "website_generator"),
                ("Facebook", "social_facebook"),
                ("Instagram", "social_instagram"),
                ("LinkedIn", "social_linkedin"),
                ("Errore sito", "website_error"),
                ("Errore dettaglio Maps", "detail_error"),
            ):
                value = str(row.get(field, "") or "").strip()
                if value:
                    detail_text_parts.append(f"{label}: {value}")
            raw_text = str(row.get("raw_text", "") or "").strip()
            if raw_text:
                detail_text_parts.extend(("", "Testo scheda Maps:", raw_text))
            self._set_detail_raw_text("\n".join(detail_text_parts))
            return

        self.detail_title_var.set(str(row.get("title", row.get("name", "")) or "Annuncio senza titolo"))
        self.detail_source_var.set(str(row.get("source", "") or "-"))
        self.detail_screening_var.set(self._screening_detail_label(row))
        self.detail_contact_var.set(self._contact_status_label(row))
        self.detail_decision_var.set(str(row.get("geo_decision", "") or "-"))
        self.detail_date_var.set(self._date_detail_label(row))

        distance = row.get("distance_km")
        if distance in ("", None):
            self.detail_distance_var.set("-")
        elif isinstance(distance, float):
            self.detail_distance_var.set(f"{distance:.1f} km")
        else:
            self.detail_distance_var.set(f"{distance} km")

        self.detail_location_var.set(str(row.get("location", "") or "-"))
        self.detail_company_var.set(str(row.get("company", "") or "-"))
        self.detail_sector_var.set(str(row.get("sector", "") or "-"))
        self.detail_role_var.set(str(row.get("role_type", "") or "-"))
        self.detail_schedule_var.set(str(row.get("schedule", "") or "-"))
        self.detail_price_var.set(str(row.get("price", "") or "-"))
        self.detail_link_var.set(str(row.get("link", "") or ""))
        self.detail_website_var.set(str(row.get("website", "") or ""))

        detail_text_parts = []
        screening_reason = str(row.get("screening_reason", "") or "").strip()
        if screening_reason:
            detail_text_parts.append(f"Smistamento candidatura: {screening_reason}")
        reason = str(row.get("geo_decision_reason", "") or "").strip()
        if reason:
            if detail_text_parts:
                detail_text_parts.append("")
            detail_text_parts.append(f"Filtro geografico: {reason}")
        age_reason = str(row.get("age_filter_reason", "") or "").strip()
        if age_reason:
            if detail_text_parts:
                detail_text_parts.append("")
            detail_text_parts.append(f"Filtro data: {age_reason}")
        contact_line = self._contact_detail_line(row)
        if contact_line:
            if detail_text_parts:
                detail_text_parts.append("")
            detail_text_parts.append(contact_line)
        llm_reason = str(row.get("llm_reason", "") or "").strip()
        if llm_reason and llm_reason != screening_reason:
            if detail_text_parts:
                detail_text_parts.append("")
            detail_text_parts.append(f"Motivo OpenAI: {llm_reason}")
        llm_red_flags = str(row.get("llm_red_flags", "") or "").strip()
        if llm_red_flags:
            if detail_text_parts:
                detail_text_parts.append("")
            detail_text_parts.append(f"Red flag: {llm_red_flags}")
        description = str(row.get("description", "") or "").strip()
        if description:
            if detail_text_parts:
                detail_text_parts.append("")
            detail_text_parts.append("Descrizione completa:")
            detail_text_parts.append(description)
        raw_text = str(row.get("raw_text", "") or "").strip()
        if raw_text:
            if detail_text_parts:
                detail_text_parts.append("")
            if description:
                detail_text_parts.append("Testo card lista:")
            detail_text_parts.append(raw_text)
        self._set_detail_raw_text("\n".join(detail_text_parts))

    def _get_selected_row(self) -> dict | None:
        rows = self._get_selected_rows()
        return rows[0] if rows else None

    def _date_detail_label(self, row: dict) -> str:
        published_at = str(row.get("published_at", "") or "").strip()
        age_days = row.get("age_days")
        age_hours = row.get("age_hours")
        if published_at and age_hours not in ("", None):
            try:
                return f"{published_at} ({float(age_hours):.1f}h fa)"
            except (TypeError, ValueError):
                return published_at
        if published_at and age_days not in ("", None):
            return f"{published_at} ({describe_age_days(int(age_days))})"
        if published_at:
            return published_at
        if age_days not in ("", None):
            return describe_age_days(int(age_days))
        return "-"

    def _get_selected_rows(self) -> list[dict]:
        selected = self.results_tree.selection()
        rows: list[dict] = []
        seen_keys: set[str] = set()
        for item in selected:
            row = self.result_row_lookup.get(str(item))
            if not row:
                continue
            link = str(row.get("link", "") or "").strip()
            key = link or str(row.get("website", "") or "").strip() or str(item)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(row)
        return rows

    def _get_accepted_subito_rows(self) -> list[dict]:
        rows: list[dict] = []
        seen_links: set[str] = set()
        for row in self.result_rows:
            if str(row.get("source", "") or "").strip().lower() != "subito":
                continue
            screening_decision = str(row.get("screening_decision", "") or "").strip().lower()
            if screening_decision:
                if screening_decision != "candida":
                    continue
            elif str(row.get("geo_decision", "") or "").strip().lower() != "accepted":
                continue
            if self._row_has_submitted_contact(row):
                continue
            link = str(row.get("link", "") or "").strip()
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            rows.append(row)
        return rows

    def _open_selected_link(self) -> None:
        row = self._get_selected_row()
        if row is None:
            return
        link = str(row.get("link", "") or "").strip()
        if link:
            os.startfile(link)

    def _open_selected_website(self) -> None:
        row = self._get_selected_row()
        if row is None:
            return
        website = str(row.get("website_final_url", "") or row.get("website", "") or "").strip()
        if website:
            os.startfile(website)


def launch_gui(script_path: Path) -> None:
    root = tk.Tk()
    ScraperApp(root, script_path)
    root.mainloop()
