import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .browser_runtime import (
    browser_mode_requires_custom_dir,
    browser_mode_uses_profile,
    default_chrome_user_data_dir,
    normalize_browser_mode,
)


APP_BG = "#f3efe6"
PANEL_BG = "#fbf8f2"
ACCENT = "#1f4d45"
ACCENT_SOFT = "#dfe9e4"
BUTTON_BG = "#c96f42"
BUTTON_FG = "#ffffff"
TEXT = "#1f2523"
MUTED = "#5f6c67"
DECISION_ORDER = {"accepted": 0, "maybe": 1, "rejected": 2}
SUBITO_JOB_OPTIONS = (
    ("pulizie", "Pulizie"),
    ("colf", "Colf"),
    ("badante", "Badante"),
    ("assistente familiare", "Assistente familiare"),
    ("domestica", "Domestica"),
    ("baby sitter", "Baby sitter"),
    ("governante", "Governante"),
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
        self.ui_result_json_path = (script_path.parent / "output" / "_ui_last_result.json").resolve()

        self.google_search_var = tk.StringVar(value="avvocati")
        self.google_city_var = tk.StringVar()
        self.google_province_var = tk.StringVar()
        self.google_country_var = tk.StringVar(value="Italia")
        self.google_max_results_var = tk.StringVar(value="25")

        self.subito_query_var = tk.StringVar()
        self.subito_region_var = tk.StringVar(value="lazio")
        self.subito_city_var = tk.StringVar(value="roma")
        self.subito_category_var = tk.StringVar(value="offerte-lavoro")
        self.subito_anchor_place_var = tk.StringVar(value="Morlupo")
        self.subito_max_distance_var = tk.StringVar(value="30")
        self.subito_nearby_only_var = tk.BooleanVar(value=False)
        self.subito_max_results_var = tk.StringVar(value="25")

        self.custom_url_var = tk.StringVar()
        self.custom_item_selector_var = tk.StringVar(value="article")
        self.custom_name_selector_var = tk.StringVar()
        self.custom_phone_selector_var = tk.StringVar()
        self.custom_link_selector_var = tk.StringVar(value="a[href]")
        self.custom_cookie_texts_var = tk.StringVar(value="Continua senza accettare,Rifiuta tutto,Rifiuta")

        self.browser_mode_var = tk.StringVar(value="sessione_persistente")
        self.browser_user_data_dir_var = tk.StringVar(value=default_chrome_user_data_dir())
        self.browser_profile_directory_var = tk.StringVar(value="Default")

        self.output_format_var = tk.StringVar(value="all")
        self.output_dir_var = tk.StringVar(value=str((script_path.parent / "output").resolve()))
        self.filename_var = tk.StringVar()
        self.attachment_path_var = tk.StringVar()
        self.contact_message_var = tk.StringVar(value="Buongiorno, allego il mio curriculum per la posizione. Grazie per l'attenzione.")
        self.contact_submit_var = tk.BooleanVar(value=False)
        self.contact_keep_open_seconds_var = tk.StringVar(value="120")
        self.status_var = tk.StringVar(value="Idle")
        self.hint_var = tk.StringVar()
        self.result_total_var = tk.StringVar(value="0 risultati")
        self.result_counts_var = tk.StringVar(value="accepted 0 | maybe 0 | rejected 0")
        self.result_meta_var = tk.StringVar(value="Nessun output caricato")
        self.detail_title_var = tk.StringVar(value="Nessun annuncio selezionato")
        self.detail_source_var = tk.StringVar(value="-")
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

        self.root.title("The Main Scraper")
        self.root.geometry("1320x920")
        self.root.minsize(1040, 780)
        self.root.configure(bg=APP_BG)

        self._configure_styles()
        self._build()
        self._update_hint()
        self._update_browser_mode()
        self._update_result_actions()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._update_hint())
        self.browser_mode_var.trace_add("write", lambda *_: self._update_browser_mode())
        self.browser_mode_var.trace_add("write", lambda *_: self._update_result_actions())
        self.root.after(150, self._drain_logs)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=APP_BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("App.TFrame", background=APP_BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Card.TLabelframe", background=PANEL_BG, bordercolor=ACCENT_SOFT, relief="solid")
        style.configure("Card.TLabelframe.Label", background=PANEL_BG, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        style.configure("Header.TLabel", background=APP_BG, foreground=ACCENT, font=("Georgia", 22, "bold"))
        style.configure("Subtitle.TLabel", background=APP_BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Hint.TLabel", background=PANEL_BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=ACCENT_SOFT, foreground=ACCENT, font=("Segoe UI", 10, "bold"), padding=(10, 6))
        style.configure("Run.TButton", background=BUTTON_BG, foreground=BUTTON_FG, padding=(16, 12), font=("Segoe UI", 10, "bold"))
        style.map("Run.TButton", background=[("active", "#b66237")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", padding=(14, 10), font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#173b35")])
        style.configure("Secondary.TButton", padding=(12, 9))
        style.configure("TNotebook", background=APP_BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 10), background="#e7e1d6", foreground=TEXT)
        style.map("TNotebook.Tab", background=[("selected", PANEL_BG)], foreground=[("selected", ACCENT)])
        style.configure("Treeview", rowheight=28, background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Metric.TLabel", background=PANEL_BG, foreground=ACCENT, font=("Segoe UI", 11, "bold"))
        style.configure("Muted.TLabel", background=PANEL_BG, foreground=MUTED, font=("Segoe UI", 9))

    def _build(self) -> None:
        container = ttk.Frame(self.root, style="App.TFrame", padding=18)
        container.pack(fill="both", expand=True)
        header = ttk.Frame(container, style="App.TFrame")
        header.pack(fill="x")
        left = ttk.Frame(header, style="App.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="The Main Scraper", style="Header.TLabel").pack(anchor="w")
        ttk.Label(left, text="Subito Jobs con output nel popup e modalita browser reale.", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side="right", anchor="ne")

        config_shell = ttk.Frame(container, style="Panel.TFrame", height=390)
        config_shell.pack(fill="x", pady=(14, 0))
        config_shell.pack_propagate(False)

        self.config_scroll = VerticalScrolledFrame(config_shell, background=APP_BG)
        self.config_scroll.pack(fill="both", expand=True)
        config_content = self.config_scroll.body
        config_content.configure(style="App.TFrame")

        hint = ttk.Frame(config_content, style="Panel.TFrame", padding=14)
        hint.pack(fill="x", pady=(0, 12))
        tk.Label(hint, textvariable=self.hint_var, bg=PANEL_BG, fg=TEXT, wraplength=1120, justify="left").pack(anchor="w")

        self.notebook = ttk.Notebook(config_content)
        self.notebook.pack(fill="x")
        self.google_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=16)
        self.subito_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=16)
        self.custom_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=16)
        self.notebook.add(self.google_tab, text="Google Maps")
        self.notebook.add(self.subito_tab, text="Subito Jobs")
        self.notebook.add(self.custom_tab, text="Custom Site")
        self._build_google_tab()
        self._build_subito_tab()
        self._build_custom_tab()

        controls = ttk.Frame(config_content, style="App.TFrame")
        controls.pack(fill="x", pady=(12, 0))
        self._build_browser_card(controls)
        self._build_export_card(controls)
        self._build_action_card(controls)

        bottom = ttk.Notebook(container)
        bottom.pack(fill="both", expand=True, pady=(12, 0))
        self.bottom_notebook = bottom
        self.results_tab = ttk.Frame(bottom, style="Panel.TFrame", padding=14)
        self.log_tab = ttk.Frame(bottom, style="Panel.TFrame", padding=14)
        bottom.add(self.results_tab, text="Risultati")
        bottom.add(self.log_tab, text="Log")
        self._build_results_tab()
        self._build_log_tab()

    def _build_google_tab(self) -> None:
        card = self._card(self.google_tab, "Ricerca locale")
        card.pack(fill="x")
        self._row(card, 0, "Query o URL", self.google_search_var)
        self._row(card, 1, "Citta", self.google_city_var, 30)
        self._row(card, 2, "Provincia", self.google_province_var, 30)
        self._row(card, 3, "Paese", self.google_country_var, 30)
        self._row(card, 4, "Max risultati", self.google_max_results_var, 12)

    def _build_subito_tab(self) -> None:
        card = self._card(self.subito_tab, "Annunci lavoro")
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
            if index < 3:
                self.subito_jobs_listbox.selection_set(index)
        ttk.Label(
            jobs_frame,
            text="Seleziona uno o piu lavori. Ogni keyword viene cercata e poi i risultati vengono uniti.",
            style="Hint.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._row(card, 2, "Regione", self.subito_region_var, 24)
        self._row(card, 3, "Citta", self.subito_city_var, 24)
        self._row(card, 4, "Categoria", self.subito_category_var, 24)
        self._row(card, 5, "Punto riferimento", self.subito_anchor_place_var, 24)
        self._row(card, 6, "Distanza max km", self.subito_max_distance_var, 12)
        self._row(card, 7, "Max risultati", self.subito_max_results_var, 12)
        ttk.Checkbutton(card, text="Tieni solo annunci accettati", variable=self.subito_nearby_only_var).grid(row=8, column=0, columnspan=2, sticky="w")

    def _build_custom_tab(self) -> None:
        card = self._card(self.custom_tab, "Configurazione avanzata")
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
        ttk.Label(
            card,
            text="sessione_persistente usa un profilo Chrome dedicato del progetto e mantiene il login tra un run e l altro. Su Subito, se lasci Profile Directory a Default, viene usato automaticamente il profilo dedicato Subito. chrome_normale crea invece uno snapshot leggero del tuo profilo reale, mentre profilo_personalizzato ti lascia scegliere una cartella User Data diversa.",
            style="Hint.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        card.columnconfigure(1, weight=1)

    def _build_export_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Export")
        card.pack(fill="x", pady=(12, 0))
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
        card.pack(fill="x", pady=(12, 0))
        self.run_button = ttk.Button(card, text="Avvia scraping", style="Run.TButton", command=self._start_scrape)
        self.run_button.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(card, text="Apri output", style="Secondary.TButton", command=self._open_output_dir).grid(row=1, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))
        ttk.Button(card, text="Ricarica risultati", style="Secondary.TButton", command=self._load_results).grid(row=1, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))
        ttk.Button(card, text="Pulisci log", style="Secondary.TButton", command=self._clear_log).grid(row=2, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))
        ttk.Button(card, text="Apri annuncio selezionato", style="Secondary.TButton", command=self._open_selected_link).grid(row=2, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

    def _build_results_tab(self) -> None:
        summary = self._card(self.results_tab, "Riepilogo")
        summary.pack(fill="x")
        ttk.Label(summary, textvariable=self.result_total_var, style="Metric.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.result_counts_var, style="Metric.TLabel").grid(row=0, column=1, sticky="w", padx=(18, 0))
        ttk.Label(summary, textvariable=self.result_meta_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(summary, text="Ordine: data piu recente, poi priorita geografica", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        split = ttk.Panedwindow(self.results_tab, orient="horizontal")
        split.pack(fill="both", expand=True, pady=(12, 0))

        table_shell = ttk.Frame(split, style="Panel.TFrame")
        detail_shell = ttk.Frame(split, style="Panel.TFrame")
        split.add(table_shell, weight=3)
        split.add(detail_shell, weight=2)

        card = self._card(table_shell, "Annunci estratti")
        card.pack(fill="both", expand=True)
        columns = ("decision", "published_at", "distance_km", "location", "title", "company", "schedule")
        self.results_tree = ttk.Treeview(card, columns=columns, show="headings", selectmode="extended")
        for key, label, width in (
            ("decision", "Decisione", 100),
            ("published_at", "Data", 110),
            ("distance_km", "Km", 70),
            ("location", "Luogo", 150),
            ("title", "Titolo", 360),
            ("company", "Azienda", 180),
            ("schedule", "Orario", 100),
        ):
            self.results_tree.heading(key, text=label)
            self.results_tree.column(key, width=width, anchor="center" if key in {"decision", "published_at", "distance_km", "schedule"} else "w")
        self.results_tree.tag_configure("accepted", background="#edf7f0")
        self.results_tree.tag_configure("maybe", background="#fff7e8")
        self.results_tree.tag_configure("rejected", background="#fdeeee")
        y_scroll = ttk.Scrollbar(card, orient="vertical", command=self.results_tree.yview)
        x_scroll = ttk.Scrollbar(card, orient="horizontal", command=self.results_tree.xview)
        self.results_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)
        self.results_tree.bind("<Double-1>", lambda _e: self._open_selected_link())
        self.results_tree.bind("<<TreeviewSelect>>", lambda _e: self._handle_result_selection())

        detail_scroll = VerticalScrolledFrame(detail_shell, background=PANEL_BG)
        detail_scroll.pack(fill="both", expand=True)
        detail_body = detail_scroll.body

        detail_card = self._card(detail_body, "Dettaglio annuncio")
        detail_card.pack(fill="x")
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
        self._detail_row(detail_card, 1, "Decisione", self.detail_decision_var, column_offset=2)
        self._detail_row(detail_card, 2, "Data", self.detail_date_var)
        self._detail_row(detail_card, 2, "Distanza", self.detail_distance_var, column_offset=2)
        self._detail_row(detail_card, 3, "Luogo", self.detail_location_var)
        self._detail_row(detail_card, 3, "Azienda", self.detail_company_var, column_offset=2)
        self._detail_row(detail_card, 4, "Settore", self.detail_sector_var)
        self._detail_row(detail_card, 4, "Ruolo", self.detail_role_var, column_offset=2)
        self._detail_row(detail_card, 5, "Orario", self.detail_schedule_var)
        self._detail_row(detail_card, 5, "Prezzo", self.detail_price_var, column_offset=2)
        ttk.Label(detail_card, text="Link").grid(row=6, column=0, sticky="w", pady=(12, 6))
        self.detail_link_entry = ttk.Entry(detail_card, textvariable=self.detail_link_var)
        self.detail_link_entry.grid(row=6, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=(12, 6))
        ttk.Label(detail_card, text="Testo annuncio").grid(row=7, column=0, sticky="nw", pady=(12, 6))
        self.detail_raw_text = ScrolledText(
            detail_card,
            wrap="word",
            height=10,
            state="disabled",
            bg="#ffffff",
            fg=TEXT,
            relief="flat",
            padx=10,
            pady=10,
            font=("Segoe UI", 10),
        )
        self.detail_raw_text.grid(row=7, column=1, columnspan=3, sticky="nsew", padx=(10, 0), pady=(12, 6))
        detail_card.columnconfigure(1, weight=1)
        detail_card.columnconfigure(3, weight=1)
        detail_card.rowconfigure(7, weight=1)

        contact_card = self._card(detail_body, "Contatto Subito")
        contact_card.pack(fill="x", pady=(12, 0))
        ttk.Label(
            contact_card,
            text="Richiede un annuncio Subito selezionato. Con sessione_persistente fai il login una volta sola e i run successivi lo riusano. Se al primo contatto Subito chiede accesso, il flusso aspetta che tu faccia login e poi continua.",
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
            bg="#ffffff",
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
        self.contact_selected_button = ttk.Button(contact_card, text="Invia CV selezionati", style="Accent.TButton", command=self._start_batch_contact_selected)
        self.contact_selected_button.grid(row=5, column=1, sticky="ew", pady=(12, 0), padx=(6, 6))
        self.contact_accepted_button = ttk.Button(contact_card, text="Invia CV accettati", style="Accent.TButton", command=self._start_batch_contact_accepted)
        self.contact_accepted_button.grid(row=5, column=2, sticky="ew", pady=(12, 0))
        self.open_selected_button = ttk.Button(contact_card, text="Apri annuncio", style="Secondary.TButton", command=self._open_selected_link)
        self.open_selected_button.grid(row=6, column=2, sticky="ew", pady=(10, 0))
        contact_card.columnconfigure(1, weight=1)
        contact_card.columnconfigure(2, weight=1)

    def _build_log_tab(self) -> None:
        card = self._card(self.log_tab, "Log esecuzione")
        card.pack(fill="both", expand=True)
        self.log_widget = ScrolledText(card, wrap="word", state="disabled", bg="#182120", fg="#edf4f1", insertbackground="#edf4f1", relief="flat", padx=12, pady=12, font=("Consolas", 10))
        self.log_widget.pack(fill="both", expand=True)

    def _card(self, parent: ttk.Frame, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=14)

    def _row(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar, width: int = 78) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 10))
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(0, 10))
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

    def _update_hint(self) -> None:
        selected = self.notebook.tab(self.notebook.select(), "text")
        if selected == "Subito Jobs":
            self.hint_var.set(
                "Subito Jobs mostra gli annunci nella tab Risultati con dettaglio completo. sessione_persistente tiene un profilo Chrome dedicato con login riusabile, i profili lavoro lanciano ricerche come pulizie/colf/badante, e dopo il parsing puoi inviare il CV ai selezionati o agli accettati."
            )
        elif selected == "Google Maps":
            self.hint_var.set(
                "Google Maps usa la stessa configurazione browser. Se vuoi tenere cookie e sessioni di lavoro nel progetto usa sessione_persistente, altrimenti puoi usare chrome_normale o un profilo personalizzato."
            )
        else:
            self.hint_var.set(
                "Custom Site resta flessibile, ma l output viene comunque mostrato nel popup con tabella, dettaglio e log."
            )

    def _update_browser_mode(self) -> None:
        mode = normalize_browser_mode(self.browser_mode_var.get())
        profile_state = "normal" if browser_mode_uses_profile(mode) else "disabled"
        custom_state = "normal" if browser_mode_requires_custom_dir(mode) else "disabled"
        self.browser_user_data_entry.configure(state=custom_state)
        self.browser_browse_button.configure(state=custom_state)
        self.browser_profile_dir_entry.configure(state=profile_state)

    def _update_result_actions(self) -> None:
        row = self._get_selected_row()
        selected_rows = self._get_selected_rows()
        accepted_rows = self._get_accepted_subito_rows()
        has_row = row is not None and bool(str(row.get("link", "") or "").strip())
        is_subito = has_row and str(row.get("source", "") or "").strip().lower() == "subito"
        self.open_selected_button.configure(state="normal" if has_row else "disabled")
        self.contact_button.configure(state="normal" if is_subito else "disabled")
        self.contact_selected_button.configure(state="normal" if selected_rows else "disabled")
        self.contact_accepted_button.configure(state="normal" if accepted_rows else "disabled")

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
        self.result_total_var.set("0 risultati")
        self.result_counts_var.set("accepted 0 | maybe 0 | rejected 0")
        self.result_meta_var.set("Nessun output caricato")
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self._clear_detail_panel()
        self._update_result_actions()

    def _clear_detail_panel(self) -> None:
        self.detail_title_var.set("Nessun annuncio selezionato")
        self.detail_source_var.set("-")
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

    def _start_scrape(self) -> None:
        try:
            command = self._build_scrape_command()
        except ValueError as exc:
            messagebox.showerror("Configurazione non valida", str(exc))
            return
        self._clear_results()
        self.current_run_source = command[3]
        self._start_process(command, kind="scrape", load_results=True)

    def _start_contact_action(self) -> None:
        try:
            command = self._build_contact_command()
        except ValueError as exc:
            messagebox.showerror("Contatto non disponibile", str(exc))
            return
        self.bottom_notebook.select(self.log_tab)
        self._start_process(command, kind="contact", load_results=False)

    def _start_batch_contact_selected(self) -> None:
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showerror("Invio CV", "Seleziona almeno un annuncio Subito.")
            return
        self._start_batch_contact(rows)

    def _start_batch_contact_accepted(self) -> None:
        rows = self._get_accepted_subito_rows()
        if not rows:
            messagebox.showerror("Invio CV", "Non ci sono annunci accepted disponibili.")
            return
        self._start_batch_contact(rows)

    def _start_batch_contact(self, rows: list[dict]) -> None:
        try:
            command = self._build_contact_batch_command(rows)
        except ValueError as exc:
            messagebox.showerror("Invio CV", str(exc))
            return
        self.bottom_notebook.select(self.log_tab)
        self._start_process(command, kind="contact", load_results=False)

    def _start_process(self, command: list[str], kind: str, load_results: bool) -> None:
        if self.process is not None:
            messagebox.showinfo("Processo in esecuzione", "Attendi la fine del processo corrente.")
            return
        self.process_kind = kind
        self.process_should_load_results = load_results
        self.status_var.set("Running")
        self._append_log(f"$ {' '.join(command)}\n")
        self.run_button.configure(state="disabled")
        if kind == "contact":
            self.contact_button.configure(state="disabled")
        if kind == "scrape":
            self.ui_result_json_path.parent.mkdir(parents=True, exist_ok=True)
            if self.ui_result_json_path.exists():
                self.ui_result_json_path.unlink()
        self.process = subprocess.Popen(command, cwd=self.script_path.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def _build_scrape_command(self) -> list[str]:
        self.ui_result_json_path = (Path(self.output_dir_var.get()).resolve() / "_ui_last_result.json")
        cmd = [sys.executable, str(self.script_path), "run"]
        selected = self.notebook.tab(self.notebook.select(), "text")
        if selected == "Google Maps":
            if not self.google_search_var.get().strip():
                raise ValueError("Inserisci una query o URL per Google Maps.")
            if not self.google_max_results_var.get().strip().isdigit():
                raise ValueError("Max risultati Google Maps deve essere un intero positivo.")
            cmd += ["google_maps", "--search", self.google_search_var.get().strip(), "--max-results", self.google_max_results_var.get().strip()]
            for flag, var in (("--city", self.google_city_var), ("--province", self.google_province_var), ("--country", self.google_country_var)):
                if var.get().strip():
                    cmd += [flag, var.get().strip()]
        elif selected == "Subito Jobs":
            if not self.subito_max_results_var.get().strip().isdigit():
                raise ValueError("Max risultati Subito deve essere un intero positivo.")
            try:
                float(self.subito_max_distance_var.get().strip())
            except ValueError as exc:
                raise ValueError("Distanza max Subito deve essere un numero valido.") from exc
            cmd += ["subito", "--max-results", self.subito_max_results_var.get().strip(), "--max-distance-km", self.subito_max_distance_var.get().strip()]
            for flag, var in (
                ("--query", self.subito_query_var),
                ("--region", self.subito_region_var),
                ("--city", self.subito_city_var),
                ("--category", self.subito_category_var),
                ("--anchor-place", self.subito_anchor_place_var),
            ):
                if var.get().strip():
                    cmd += [flag, var.get().strip()]
            selected_job_keywords = self._selected_subito_job_keywords()
            if selected_job_keywords:
                cmd += ["--job-keywords", ",".join(selected_job_keywords)]
            if self.subito_nearby_only_var.get():
                cmd.append("--nearby-only")
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
            link = str(row.get("link", "") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            links.append(link)
        if not links:
            raise ValueError("Non ci sono link Subito validi per l'invio del CV.")

        attachment = self.attachment_path_var.get().strip()
        if attachment and not Path(attachment).exists():
            raise ValueError("Il file allegato selezionato non esiste.")
        message = self._get_contact_message()
        keep_open_raw = self.contact_keep_open_seconds_var.get().strip() or "120"
        if not keep_open_raw.isdigit():
            raise ValueError("Tieni browser aperto deve essere un intero positivo.")

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
        return [
            "--browser-mode", self.browser_mode_var.get().strip(),
            "--browser-user-data-dir", self.browser_user_data_dir_var.get().strip(),
            "--browser-profile-directory", self.browser_profile_directory_var.get().strip(),
        ]

    def _selected_subito_job_keywords(self) -> list[str]:
        selected_indexes = self.subito_jobs_listbox.curselection()
        keywords: list[str] = []
        for index in selected_indexes:
            if 0 <= index < len(SUBITO_JOB_OPTIONS):
                keywords.append(SUBITO_JOB_OPTIONS[index][0])
        return keywords

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
            if line.startswith("__DONE__:"):
                code = int(line.split(":", 1)[1])
                completed_kind = self.process_kind
                should_load_results = self.process_should_load_results
                self.process = None
                self.process_kind = ""
                self.process_should_load_results = False
                self.status_var.set("Idle" if code == 0 else "Error")
                self.run_button.configure(state="normal")
                self._update_result_actions()
                if code == 0 and should_load_results:
                    self._load_results()
                elif completed_kind == "contact":
                    if code == 0:
                        messagebox.showinfo("Contatto pronto", "Flusso Contatta eseguito. Controlla il browser e il log.")
                    else:
                        messagebox.showerror("Contatto fallito", "Il flusso Contatta non e stato completato. Controlla il log.")
                continue
            self._append_log(line)
        self.root.after(150, self._drain_logs)

    def _append_log(self, text: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _load_results(self) -> None:
        if not self.ui_result_json_path.exists():
            self.result_meta_var.set("Run completato, ma il JSON UI non e stato trovato.")
            return
        try:
            payload = json.loads(self.ui_result_json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.result_meta_var.set(f"Errore caricamento risultati: {exc}")
            return
        rows = sorted(payload.get("rows", []), key=self._result_sort_key)
        self.result_rows = rows
        counts = dict((payload.get("meta", {}) or {}).get("geo_counts", {}))
        if not counts:
            counts = {"accepted": 0, "maybe": 0, "rejected": 0}
            for row in rows:
                counts[str(row.get("geo_decision", "maybe"))] = counts.get(str(row.get("geo_decision", "maybe")), 0) + 1
        self.result_total_var.set(f"{len(rows)} risultati")
        self.result_counts_var.set(f"accepted {counts.get('accepted', 0)} | maybe {counts.get('maybe', 0)} | rejected {counts.get('rejected', 0)}")
        meta = payload.get("meta", {}) or {}
        self.result_meta_var.set(
            f"Sorgente: {payload.get('source', self.current_run_source)} | Anchor: {meta.get('geo_anchor_place', '-')} | Generato: {payload.get('generated_at', '-')}"
        )
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        for index, row in enumerate(rows):
            decision = str(row.get("geo_decision", "") or "-")
            distance = row.get("distance_km", "-")
            if distance in ("", None):
                distance_display = "-"
            elif isinstance(distance, float):
                distance_display = f"{distance:.1f}"
            else:
                distance_display = str(distance)
            values = (
                decision,
                str(row.get("published_at", "") or "-"),
                distance_display,
                str(row.get("location", "") or ""),
                str(row.get("title", row.get("name", "")) or ""),
                str(row.get("company", "") or ""),
                str(row.get("schedule", "") or ""),
            )
            tag = decision if decision in {"accepted", "maybe", "rejected"} else ""
            self.results_tree.insert("", "end", iid=str(index), values=values, tags=(tag,))
        if rows:
            self.results_tree.selection_set("0")
            self.results_tree.focus("0")
            self.results_tree.see("0")
            self._populate_detail_panel(rows[0])
        else:
            self._clear_detail_panel()
        self._update_result_actions()
        self.bottom_notebook.select(self.results_tab)

    def _result_sort_key(self, row: dict) -> tuple:
        published = self._parse_published_at(str(row.get("published_at", "") or ""))
        distance = row.get("distance_km")
        return (
            published is None,
            -(published.timestamp()) if published else 0,
            DECISION_ORDER.get(str(row.get("geo_decision", "maybe")), 99),
            distance is None,
            distance if distance is not None else 9999,
            str(row.get("title", row.get("name", ""))).lower(),
        )

    def _parse_published_at(self, value: str) -> datetime | None:
        raw = value.strip().lower()
        if not raw:
            return None
        today = date.today()
        if raw.startswith("oggi"):
            return datetime.combine(today, datetime.min.time())
        if raw.startswith("ieri"):
            return datetime.combine(today - timedelta(days=1), datetime.min.time())
        raw = raw.split("alle", 1)[0].strip()
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m"):
            try:
                parsed = datetime.strptime(raw, fmt)
                return parsed.replace(year=today.year) if fmt == "%d/%m" else parsed
            except ValueError:
                continue
        return None

    def _handle_result_selection(self) -> None:
        row = self._get_selected_row()
        if row is None:
            self._clear_detail_panel()
        else:
            self._populate_detail_panel(row)
        self._update_result_actions()

    def _populate_detail_panel(self, row: dict) -> None:
        self.detail_title_var.set(str(row.get("title", row.get("name", "")) or "Annuncio senza titolo"))
        self.detail_source_var.set(str(row.get("source", "") or "-"))
        self.detail_decision_var.set(str(row.get("geo_decision", "") or "-"))
        self.detail_date_var.set(str(row.get("published_at", "") or "-"))

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

        detail_text_parts = []
        reason = str(row.get("geo_decision_reason", "") or "").strip()
        if reason:
            detail_text_parts.append(f"Filtro geografico: {reason}")
        raw_text = str(row.get("raw_text", "") or "").strip()
        if raw_text:
            if detail_text_parts:
                detail_text_parts.append("")
            detail_text_parts.append(raw_text)
        self._set_detail_raw_text("\n".join(detail_text_parts))

    def _get_selected_row(self) -> dict | None:
        rows = self._get_selected_rows()
        return rows[0] if rows else None

    def _get_selected_rows(self) -> list[dict]:
        selected = self.results_tree.selection()
        rows: list[dict] = []
        seen_indexes: set[int] = set()
        for item in selected:
            index = int(item)
            if index in seen_indexes:
                continue
            seen_indexes.add(index)
            if 0 <= index < len(self.result_rows):
                row = self.result_rows[index]
                if str(row.get("source", "") or "").strip().lower() == "subito" and str(row.get("link", "") or "").strip():
                    rows.append(row)
        return rows

    def _get_accepted_subito_rows(self) -> list[dict]:
        rows: list[dict] = []
        seen_links: set[str] = set()
        for row in self.result_rows:
            if str(row.get("source", "") or "").strip().lower() != "subito":
                continue
            if str(row.get("geo_decision", "") or "").strip().lower() != "accepted":
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


def launch_gui(script_path: Path) -> None:
    root = tk.Tk()
    ScraperApp(root, script_path)
    root.mainloop()
