import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


class ScraperApp:
    def __init__(self, root: tk.Tk, script_path: Path) -> None:
        self.root = root
        self.script_path = script_path
        self.process: subprocess.Popen[str] | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.root.title("Multi-Scraper")
        self.root.geometry("900x700")
        self.root.minsize(780, 620)

        self.google_search_var = tk.StringVar(value="advocacia")
        self.google_city_var = tk.StringVar()
        self.google_max_results_var = tk.StringVar(value="25")

        self.custom_url_var = tk.StringVar()
        self.custom_item_selector_var = tk.StringVar(value='[role="article"]')
        self.custom_name_selector_var = tk.StringVar()
        self.custom_phone_selector_var = tk.StringVar()
        self.custom_link_selector_var = tk.StringVar(value="a[href]")
        self.custom_cookie_texts_var = tk.StringVar(value="Rifiuta tutto,Rifiuta,Reject all,Decline all")

        self.output_format_var = tk.StringVar(value="all")
        self.output_dir_var = tk.StringVar(value=str((script_path.parent / "output").resolve()))
        self.filename_var = tk.StringVar()

        self._build_layout()
        self.root.after(150, self._drain_logs)

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)

        header = ttk.Label(
            container,
            text="Scraper multi-sorgente con export CSV/XLSX",
            font=("Segoe UI", 14, "bold"),
        )
        header.pack(anchor="w")

        subtitle = ttk.Label(
            container,
            text="Scegli una sorgente, configura i campi minimi e avvia lo scraping.",
        )
        subtitle.pack(anchor="w", pady=(4, 12))

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="x")

        self.google_tab = ttk.Frame(self.notebook, padding=12)
        self.custom_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.google_tab, text="Google Maps")
        self.notebook.add(self.custom_tab, text="Custom Site")

        self._build_google_tab()
        self._build_custom_tab()
        self._build_export_section(container)
        self._build_action_section(container)
        self._build_log_section(container)

    def _build_google_tab(self) -> None:
        self._add_labeled_entry(
            self.google_tab,
            row=0,
            label="Query o URL Google Maps",
            variable=self.google_search_var,
            width=80,
        )
        self._add_labeled_entry(
            self.google_tab,
            row=1,
            label="Città",
            variable=self.google_city_var,
            width=40,
        )
        self._add_labeled_entry(
            self.google_tab,
            row=2,
            label="Numero massimo risultati",
            variable=self.google_max_results_var,
            width=20,
        )

    def _build_custom_tab(self) -> None:
        self._add_labeled_entry(
            self.custom_tab,
            row=0,
            label="URL del sito",
            variable=self.custom_url_var,
            width=80,
        )
        self._add_labeled_entry(
            self.custom_tab,
            row=1,
            label="Selector contenitore item",
            variable=self.custom_item_selector_var,
            width=80,
        )
        self._add_labeled_entry(
            self.custom_tab,
            row=2,
            label="Selector nome",
            variable=self.custom_name_selector_var,
            width=80,
        )
        self._add_labeled_entry(
            self.custom_tab,
            row=3,
            label="Selector telefono",
            variable=self.custom_phone_selector_var,
            width=80,
        )
        self._add_labeled_entry(
            self.custom_tab,
            row=4,
            label="Selector link",
            variable=self.custom_link_selector_var,
            width=80,
        )
        self._add_labeled_entry(
            self.custom_tab,
            row=5,
            label="Testi bottone cookie",
            variable=self.custom_cookie_texts_var,
            width=80,
        )

    def _build_export_section(self, parent: ttk.Frame) -> None:
        section = ttk.LabelFrame(parent, text="Export", padding=12)
        section.pack(fill="x", pady=(12, 0))

        ttk.Label(section, text="Formato").grid(row=0, column=0, sticky="w")
        format_box = ttk.Combobox(
            section,
            textvariable=self.output_format_var,
            values=("json", "csv", "xlsx", "all"),
            width=12,
            state="readonly",
        )
        format_box.grid(row=0, column=1, sticky="w", padx=(8, 16))

        ttk.Label(section, text="Nome file base").grid(row=0, column=2, sticky="w")
        ttk.Entry(section, textvariable=self.filename_var, width=32).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(section, text="Cartella output").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(section, textvariable=self.output_dir_var, width=70).grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(8, 8),
            pady=(10, 0),
        )
        ttk.Button(section, text="Sfoglia", command=self._choose_output_dir).grid(row=1, column=4, sticky="e", pady=(10, 0))

        section.columnconfigure(3, weight=1)

    def _build_action_section(self, parent: ttk.Frame) -> None:
        section = ttk.Frame(parent)
        section.pack(fill="x", pady=(12, 0))

        self.run_button = ttk.Button(section, text="Avvia scraping", command=self._start_scrape)
        self.run_button.pack(side="left")

        ttk.Button(section, text="Apri output", command=self._open_output_dir).pack(side="left", padx=(8, 0))

    def _build_log_section(self, parent: ttk.Frame) -> None:
        section = ttk.LabelFrame(parent, text="Log", padding=12)
        section.pack(fill="both", expand=True, pady=(12, 0))

        self.log_widget = tk.Text(section, height=18, wrap="word", state="disabled")
        self.log_widget.pack(fill="both", expand=True)

    def _add_labeled_entry(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, width: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(0, 8))
        parent.columnconfigure(1, weight=1)

    def _choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(self.script_path.parent))
        if folder:
            self.output_dir_var.set(folder)

    def _open_output_dir(self) -> None:
        output_dir = Path(self.output_dir_var.get()).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir)

    def _start_scrape(self) -> None:
        if self.process is not None:
            messagebox.showinfo("Scraper in esecuzione", "Attendi la fine del processo corrente.")
            return

        try:
            command = self._build_command()
        except ValueError as exc:
            messagebox.showerror("Configurazione non valida", str(exc))
            return

        self._append_log(f"$ {' '.join(command)}\n")
        self.run_button.configure(state="disabled")

        self.process = subprocess.Popen(
            command,
            cwd=self.script_path.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        threading.Thread(target=self._read_process_output, daemon=True).start()

    def _build_command(self) -> list[str]:
        command = [sys.executable, str(self.script_path), "run"]

        if self.notebook.index(self.notebook.select()) == 0:
            search = self.google_search_var.get().strip()
            if not search:
                raise ValueError("Inserisci una query o URL per Google Maps.")

            max_results = self.google_max_results_var.get().strip()
            if not max_results.isdigit():
                raise ValueError("Il numero massimo risultati deve essere un intero positivo.")

            command.extend(
                [
                    "google_maps",
                    "--search",
                    search,
                ]
            )

            city = self.google_city_var.get().strip()
            if city:
                command.extend(["--city", city])

            command.extend(
                [
                    "--max-results",
                    max_results,
                ]
            )
        else:
            url = self.custom_url_var.get().strip()
            item_selector = self.custom_item_selector_var.get().strip()

            if not url:
                raise ValueError("Inserisci l'URL del sito custom.")
            if not item_selector:
                raise ValueError("Inserisci il selector contenitore per il sito custom.")

            command.extend(
                [
                    "custom_site",
                    "--url",
                    url,
                    "--item-selector",
                    item_selector,
                    "--name-selector",
                    self.custom_name_selector_var.get().strip(),
                    "--phone-selector",
                    self.custom_phone_selector_var.get().strip(),
                    "--link-selector",
                    self.custom_link_selector_var.get().strip(),
                    "--cookie-reject-texts",
                    self.custom_cookie_texts_var.get().strip(),
                ]
            )

        command.extend(
            [
                "--format",
                self.output_format_var.get().strip(),
                "--output-dir",
                self.output_dir_var.get().strip(),
            ]
        )

        filename = self.filename_var.get().strip()
        if filename:
            command.extend(["--filename", filename])

        return command

    def _read_process_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None

        for line in self.process.stdout:
            self.log_queue.put(line)

        return_code = self.process.wait()
        self.log_queue.put(f"\nProcesso terminato con exit code {return_code}.\n")
        self.log_queue.put("__PROCESS_DONE__")

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if line == "__PROCESS_DONE__":
                self.process = None
                self.run_button.configure(state="normal")
                continue

            self._append_log(line)

        self.root.after(150, self._drain_logs)

    def _append_log(self, text: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")


def launch_gui(script_path: Path) -> None:
    root = tk.Tk()
    ScraperApp(root, script_path)
    root.mainloop()
