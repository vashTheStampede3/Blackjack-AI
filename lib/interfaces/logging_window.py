"""Live log window with search, category filter, export, and auto-scroll.

Thread-safety: LogManager invokes callbacks on whatever thread logged the
message (usually the detection worker). The callback here only appends to a
lock-guarded list; an `after()` loop on the Tk thread drains it. No Tkinter
call ever happens off the main thread.
"""

import threading
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox

from ..common import constants
from ..logic.log_manager import LogCategory

C = constants.COLORS

MAX_DISPLAY_LINES = 2000

CATEGORY_COLORS = {
    "MODEL_LOADING": C["success"],
    "CARD_COUNTER": C["accent"],
    "DETECTION": C["text_primary"],
    "PERFORMANCE": "#b8860b",
    "ERROR": C["danger"],
    "STARTUP": C["success"],
    "UI": C["text_secondary"],
    "GENERAL": C["text_secondary"],
}

FILTERS = ["All", "Errors only"] + [c.value for c in LogCategory]


class LoggingWindow(tk.Toplevel):
    def __init__(self, parent, log_manager):
        super().__init__(parent)
        self.log_manager = log_manager
        self.title("Blackjack AI — Logs")
        self.geometry("1100x650")
        self._visibility_state = "created"
        self.bind("<Map>", self._on_mapped, add="+")
        self.bind("<Unmap>", self._on_unmapped, add="+")
        self.configure(bg=C["bg_primary"])
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.auto_scroll = tk.BooleanVar(value=True)
        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="All")
        self._search_job = None
        self._shown_count = 0

        self._pending = []
        self._pending_lock = threading.Lock()

        self._build_controls()
        path = str(log_manager.session_path or "No se pudo crear el archivo")
        if log_manager.persistence_error:
            path += f" | Error de escritura: {log_manager.persistence_error}"
        tk.Label(self, text=f"Archivo de esta sesión: {path}", anchor="w", wraplength=1050,
                 bg=C["bg_primary"], fg=C["text_secondary"],
                 font=constants.FONT_SMALL).pack(fill=tk.X, padx=14, pady=(0, 8))
        self._build_display()

        # Register BEFORE loading history so nothing logged in between is
        # lost; the first drain dedups entries that appear in both.
        self.log_manager.register_callback(self._on_log)
        loaded = self.log_manager.get_all_logs()
        self._loaded_ids = {id(e) for e in loaded}
        matching = [e for e in loaded if self._matches(e)]
        if matching:
            self._insert_entries(matching)
        self.after(150, self._drain_pending)

        # Make the user-requested window discoverable even when its parent is
        # behind another window. Mapping is confirmed by <Map>, not after_idle.
        self.transient(parent)
        self.deiconify()
        self.lift()

    def _on_mapped(self, _event=None):
        self._visibility_state = "visible"

    def _on_unmapped(self, _event=None):
        if self._visibility_state != "closed":
            self._visibility_state = "hidden"

    # ---------------------------------------------------------------- layout

    def _build_controls(self):
        bar = tk.Frame(self, bg=C["bg_secondary"])
        bar.pack(fill=tk.X, padx=14, pady=10)

        tk.Label(bar, text="Search:", font=constants.FONT_BODY,
                 bg=C["bg_secondary"], fg=C["text_primary"]).pack(side=tk.LEFT)
        entry = tk.Entry(bar, textvariable=self.search_var, font=constants.FONT_BODY, width=28)
        entry.pack(side=tk.LEFT, padx=(6, 18))
        self.search_var.trace_add("write", lambda *a: self._debounced_refresh())

        tk.Label(bar, text="Filter:", font=constants.FONT_BODY,
                 bg=C["bg_secondary"], fg=C["text_primary"]).pack(side=tk.LEFT)
        combo = ttk.Combobox(bar, textvariable=self.filter_var, state="readonly",
                             font=constants.FONT_BODY, width=18, values=FILTERS)
        combo.pack(side=tk.LEFT, padx=(6, 18))
        combo.bind("<<ComboboxSelected>>", lambda e: self._refresh())

        tk.Checkbutton(bar, text="Auto-scroll", variable=self.auto_scroll,
                       font=constants.FONT_BODY, bg=C["bg_secondary"],
                       activebackground=C["bg_secondary"]).pack(side=tk.LEFT)

        self.count_label = tk.Label(bar, text="", font=constants.FONT_BODY,
                                    bg=C["bg_secondary"], fg=C["text_secondary"])
        self.count_label.pack(side=tk.RIGHT)

        tk.Button(bar, text="Export", command=self._export, bg=C["accent"], fg="white",
                  font=constants.FONT_BODY, relief="flat", cursor="hand2",
                  padx=14, pady=5).pack(side=tk.RIGHT, padx=8)
        tk.Button(bar, text="Abrir carpeta", command=self._open_folder,
                  bg=C["bg_secondary"], fg=C["text_primary"], font=constants.FONT_BODY,
                  relief="flat", padx=10, pady=5).pack(side=tk.RIGHT, padx=4)
        tk.Button(bar, text="Limpiar vista", command=self._clear, bg=C["warning"],
                  fg=C["text_primary"], font=constants.FONT_BODY, relief="flat",
                  cursor="hand2", padx=14, pady=5).pack(side=tk.RIGHT)

    def _build_display(self):
        frame = tk.Frame(self, bg=C["bg_primary"])
        frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text = tk.Text(frame, wrap=tk.WORD, font=("Consolas", 9),
                            bg=C["bg_secondary"], fg=C["text_primary"],
                            yscrollcommand=scrollbar.set, state="disabled",
                            relief="flat", highlightbackground=C["border"],
                            highlightthickness=1)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text.yview)
        for name, color in CATEGORY_COLORS.items():
            self.text.tag_config(f"cat_{name}", foreground=color)
        self.text.bind("<MouseWheel>", self._on_manual_scroll)

    # ----------------------------------------------------------------- logic

    def _on_log(self, entry):
        """LogManager callback — may run on any thread. No Tk calls here."""
        with self._pending_lock:
            self._pending.append(entry)

    def _drain_pending(self):
        if not self.winfo_exists():
            return
        try:
            with self._pending_lock:
                entries, self._pending = self._pending, []
            if self._loaded_ids:
                entries = [e for e in entries if id(e) not in self._loaded_ids]
                self._loaded_ids = set()
            if entries:
                shown = [e for e in entries if self._matches(e)]
                if shown:
                    self._insert_entries(shown)
        except Exception:
            if not getattr(self, "_drain_error_reported", False):
                self._drain_error_reported = True
                self.log_manager.exception("No se pudo actualizar la ventana de Logs.")
        self.after(150, self._drain_pending)

    def _insert_entries(self, entries):
        self.text.config(state="normal")
        for entry in entries:
            color = "ERROR" if entry.level == "ERROR" else "PERFORMANCE" if entry.level == "WARNING" else entry.category.name
            self.text.insert(tk.END, self._format(entry), f"cat_{color}")
        self._shown_count += len(entries)
        # Trim the widget so long sessions don't degrade. index("end-1c")
        # counts one extra (empty) line after the trailing newline.
        shown_lines = int(self.text.index("end-1c").split(".")[0]) - 1
        if shown_lines > MAX_DISPLAY_LINES:
            self.text.delete("1.0", f"{shown_lines - MAX_DISPLAY_LINES + 1}.0")
            self._shown_count = MAX_DISPLAY_LINES
        self.text.config(state="disabled")
        if self.auto_scroll.get():
            self.text.see(tk.END)
        self._update_count()

    @staticmethod
    def _format(entry):
        timestamp = entry.timestamp.strftime('%H:%M:%S.%f')[:-3]
        action = f" [{entry.action_id}]" if entry.action_id else ""
        event = f" [{entry.event}]" if entry.event else ""
        return f"[{timestamp}] [{entry.level}]{action}{event} [{entry.category.value}] {entry.message}\n"

    def _matches(self, entry):
        needle = self.search_var.get().lower()
        if needle and needle not in self._format(entry).lower():
            return False
        chosen = self.filter_var.get()
        if chosen == "All":
            return True
        if chosen == "Errors only":
            return entry.level == "ERROR"
        return entry.category.value == chosen

    def _debounced_refresh(self):
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(250, self._refresh)

    def _refresh(self):
        self._search_job = None
        if not self.winfo_exists():
            return
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.config(state="disabled")
        self._shown_count = 0
        matching = [e for e in self.log_manager.get_all_logs() if self._matches(e)]
        if matching:
            self._insert_entries(matching)
        else:
            self._update_count()

    def _update_count(self):
        total = len(self.log_manager.get_all_logs())
        self.count_label.config(text=f"Showing {self._shown_count} of {total}")

    def _clear(self):
        if messagebox.askyesno("Limpiar vista", "¿Limpiar el historial en memoria? El archivo de la sesión se conserva.",
                               parent=self):
            self.log_manager.clear_logs()
            self._refresh()

    def _export(self):
        try:
            constants.LOGS_DIR.mkdir(exist_ok=True)
        except OSError:
            pass  # the save dialog will fall back to a default directory
        default = f"blackjack_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        path = filedialog.asksaveasfilename(
            parent=self, title="Export logs", initialdir=constants.LOGS_DIR,
            initialfile=default, defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt")])
        if not path:
            return
        if self.log_manager.session_path and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.log_manager.session_path)):
            messagebox.showerror("Export failed", "Elegí otro archivo: el registro de esta sesión está en uso.", parent=self)
            return
        entries = self.log_manager.get_all_logs()
        try:
            with open(path, "w", encoding="utf-8", errors="replace") as f:
                f.write(f"Blackjack AI logs — exported {datetime.now():%Y-%m-%d %H:%M:%S}\n")
                f.write("=" * 80 + "\n")
                for e in entries:
                    f.write(self._format(e))
            messagebox.showinfo("Export", f"Exported {len(entries)} entries to:\n{path}", parent=self)
        except OSError as e:
            self.log_manager.exception("No se pudieron exportar los logs.")
            messagebox.showerror("Export failed", str(e), parent=self)

    def _open_folder(self):
        directory = self.log_manager.session_path.parent if self.log_manager.session_path else constants.LOGS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))
        self.log_manager.add_log("Carpeta de logs solicitada al Explorador de Windows.",
                                 event="LOG_FOLDER_REQUESTED")

    def _on_manual_scroll(self, _event):
        self.after_idle(self._sync_autoscroll)

    def _sync_autoscroll(self):
        if not self.winfo_exists():
            return
        at_bottom = self.text.yview()[1] >= 0.99
        if at_bottom and not self.auto_scroll.get():
            self.auto_scroll.set(True)
        elif not at_bottom and self.auto_scroll.get():
            self.auto_scroll.set(False)

    def on_close(self):
        self._visibility_state = "closed"
        self.log_manager.unregister_callback(self._on_log)
        self.destroy()
