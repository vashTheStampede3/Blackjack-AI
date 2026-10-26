"""Choose a local video frame and edit its regions with the existing editor.

No detector, capture service, executor or network client is constructed here.
Frames remain in memory; saving writes only profile coordinates and source data.
"""
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

from ..logic import region_profiles
from ..logic.app_modes import APP_MODES, APP_MODE_LABELS, normalize_mode
from ..logic.card_layout import ZONE_COLORS, ZONE_LABELS
from ..logic.video_source import VideoFile, parse_video_time
from .region_editor import RegionEditor


class VideoFramePicker(tk.Toplevel):
    def __init__(self, parent, video_path=None, profile="Video", on_close=None):
        super().__init__(parent)
        self.title("Mesa y regiones — video local")
        self.reader = self.source = self.editor = None
        self.on_close = on_close
        self._seek_job = None
        self._setting_slider = False
        self._photo = None
        self.time_var = tk.StringVar(value="0:00")
        self.profile_var = tk.StringVar(value=profile)
        self.mode_var = tk.StringVar(value=APP_MODE_LABELS["individual"])
        self.info_var = tk.StringVar(value="Abrí un video local y elegí un fotograma con cartas.")
        self.status_var = tk.StringVar()

        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Abrir video", command=self._choose_file).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.info_var).pack(side=tk.LEFT, padx=10)
        self.canvas = tk.Canvas(self, width=640, height=360, bg="#20262b", highlightthickness=0)
        self.canvas.pack(padx=8)

        seek = ttk.Frame(self, padding=8)
        seek.pack(fill=tk.X)
        ttk.Label(seek, text="Momento (min:seg o segundos):").pack(side=tk.LEFT)
        entry = ttk.Entry(seek, textvariable=self.time_var, width=12)
        entry.pack(side=tk.LEFT, padx=5)
        entry.bind("<Return>", lambda event: self._go())
        ttk.Button(seek, text="Ir", command=self._go).pack(side=tk.LEFT)
        ttk.Button(seek, text="−1 fotograma", command=lambda: self._step(-1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(seek, text="+1 fotograma", command=lambda: self._step(1)).pack(side=tk.LEFT)
        self.slider = tk.Scale(self, from_=0, to=1, resolution=.001, orient=tk.HORIZONTAL,
                               showvalue=False, command=self._schedule_seek)
        self.slider.pack(fill=tk.X, padx=8)

        save = ttk.Frame(self, padding=8)
        save.pack(fill=tk.X)
        ttk.Label(save, text="Perfil:").pack(side=tk.LEFT)
        self.profile_combo = ttk.Combobox(save, textvariable=self.profile_var, width=24,
            values=[p["name"] for p in region_profiles.list_profiles()])
        self.profile_combo.pack(side=tk.LEFT, padx=6)
        self.profile_combo.bind("<<ComboboxSelected>>", self._profile_changed)
        self.profile_combo.bind("<Return>", self._profile_changed)
        ttk.Button(save, text="Dibujar / editar zonas", command=self._edit_regions).pack(side=tk.LEFT)
        ttk.Button(save, text="Ver zonas guardadas", command=self._draw).pack(side=tk.LEFT, padx=6)
        ttk.Button(save, text="Cerrar", command=self.close).pack(side=tk.RIGHT)
        modes = ttk.Frame(self, padding=(8, 0, 8, 4))
        modes.pack(fill=tk.X)
        ttk.Label(modes, text="Modo:").pack(side=tk.LEFT)
        self.mode_combo = ttk.Combobox(modes, textvariable=self.mode_var, state="readonly",
                                       width=34, values=[APP_MODE_LABELS[m] for m in APP_MODES])
        self.mode_combo.pack(side=tk.LEFT, padx=6)
        self.mode_combo.bind("<<ComboboxSelected>>", self._mode_changed)
        ttk.Label(modes, text="Cada modo conserva sus propias zonas.").pack(side=tk.LEFT)
        ttk.Label(self, textvariable=self.status_var, wraplength=1050).pack(fill=tk.X, padx=10, pady=3)
        ttk.Label(self, text="El perfil usa el tamaño del video. Otra resolución o encuadre en vivo "
                  "requiere revisión. No se analiza ni se cuenta ninguna carta.",
                  wraplength=1050).pack(fill=tk.X, padx=10, pady=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.close)
        if video_path is not None:
            self.open_video(video_path)

    def _choose_file(self):
        path = filedialog.askopenfilename(parent=self, title="Elegir video local",
            filetypes=[("Videos", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v"), ("Todos", "*.*")])
        if path:
            self.open_video(path)

    def open_video(self, path):
        if self.editor is not None and self.editor.winfo_exists():
            self.status_var.set("Terminá o cancelá la edición antes de cambiar el video.")
            return False
        candidate = None
        try:
            candidate = VideoFile(path)
            source = candidate.frame_at(0)
        except (OSError, ValueError) as exc:
            if candidate:
                candidate.close()
            self.status_var.set(str(exc))
            return False
        self._cancel_seek()
        if self.reader:
            self.reader.close()
        self.reader, self.source = candidate, source
        self.slider.configure(to=candidate.last_frame_seconds)
        self._restore_mode()
        self._accept_source(source)
        return True

    def _current_mode(self):
        labels = {label: mode for mode, label in APP_MODE_LABELS.items()}
        try:
            return normalize_mode(labels[self.mode_var.get()])
        except KeyError:
            raise ValueError("Seleccioná Individual o Multijugador.") from None

    def _restore_mode(self):
        if self.source is not None:
            try:
                mode = region_profiles.layout_mode(self.source.resolution, profile=self.profile_var.get().strip())
            except ValueError as exc:
                self.status_var.set(str(exc))
                return
            self.mode_var.set(APP_MODE_LABELS[mode])

    def _profile_changed(self, event=None):
        self._restore_mode()
        self._draw()

    def _mode_changed(self, event=None):
        if self.editor is not None and self.editor.winfo_exists():
            self.mode_var.set(APP_MODE_LABELS[self.editor.mode])
            self.status_var.set("Terminá o cancelá la edición antes de cambiar el modo.")
            return
        self._draw()

    def _cancel_seek(self):
        if self._seek_job is not None:
            self.after_cancel(self._seek_job)
            self._seek_job = None

    def _schedule_seek(self, value):
        if self.source and abs(float(value) - self.source.timestamp_seconds) < .001:
            return
        if not self._setting_slider and self.reader:
            self._cancel_seek()
            self._seek_job = self.after(180, lambda: self.seek(float(value)))

    def _go(self):
        try:
            return self.seek(parse_video_time(self.time_var.get()))
        except ValueError as exc:
            self.status_var.set(str(exc))
            return False

    def _step(self, delta):
        if self.reader and self.source:
            index = min(self.reader.frame_count - 1, max(0, self.source.frame_index + delta))
            self.seek(index / self.reader.fps)

    def seek(self, seconds):
        self._cancel_seek()
        if not self.reader:
            self.status_var.set("Abrí un video primero.")
            return False
        if self.editor is not None and self.editor.winfo_exists():
            self.status_var.set("Terminá o cancelá la edición antes de cambiar el fotograma.")
            return False
        try:
            source = self.reader.frame_at(seconds)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return False
        self._accept_source(source)
        return True

    def _accept_source(self, source):
        self.source = source
        self.time_var.set(f"{source.timestamp_seconds:.3f}")
        self._setting_slider = True
        self.slider.set(source.timestamp_seconds)
        self._setting_slider = False
        w, h = source.resolution
        self.info_var.set(f"{self.reader.path.name} · {w}×{h} · {self.reader.fps:g} fps · "
                          f"{self.reader.duration_seconds:.1f} s")
        self._draw()

    def _draw(self):
        if self.source is None:
            return
        w, h = self.source.resolution
        scale = min(1100 / w, (self.winfo_screenwidth() - 70) / w,
                    (self.winfo_screenheight() - 340) / h, 1.0)
        image = Image.fromarray(self.source.grab_bgr()[:, :, ::-1])
        image = image.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.canvas.configure(width=image.width, height=image.height)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        profile = self.profile_var.get().strip()
        self._draw_card_layout(profile, self._current_mode(), scale)

    def _draw_card_layout(self, profile, mode, scale):
        try:
            layout = region_profiles.get_card_layout(self.source.resolution, mode, profile=profile) if profile else None
        except ValueError as exc:
            self.status_var.set(f"Zonas guardadas inválidas: {exc}")
            return
        if layout is None:
            self.status_var.set(f'"{profile}" no tiene zonas para {APP_MODE_LABELS[mode]} en este tamaño. '
                                "Dibujá Mesa, Mi asiento y Crupier; no se usan las zonas de otro modo.")
            return
        for key, label in ZONE_LABELS.items():
            poly = getattr(layout, key)
            if poly is None:
                continue
            self.canvas.create_polygon(*[v * scale for p in poly for v in p], fill="",
                                       outline=ZONE_COLORS[key], width=2, tags="regions")
            xs, ys = zip(*poly)
            self.canvas.create_text(sum(xs)/len(xs)*scale, sum(ys)/len(ys)*scale,
                                    text=label, fill=ZONE_COLORS[key], tags="regions")
        w, h = layout.resolution
        self.status_var.set(f'Zonas válidas en "{profile}" · {APP_MODE_LABELS[mode]} · {w}×{h}. '
                            "Mi asiento todavía debe compararse con tus etiquetas.")

    def _edit_regions(self):
        if self.source is None:
            self.status_var.set("Abrí un video y elegí un fotograma primero.")
            return
        if self.editor is not None and self.editor.winfo_exists():
            self.editor.lift()
            return
        profile = self.profile_var.get().strip()
        if not profile:
            self.status_var.set("Escribí un nombre de perfil.")
            return
        self._cancel_seek()
        try:
            self.editor = RegionEditor(self, self.source, on_save=self._saved, profile=profile,
                                       mode=self._current_mode())
        except ValueError as exc:
            self.status_var.set(str(exc))

    def _saved(self, saved=True):
        if saved:
            self.profile_var.set(region_profiles.active_name())
            self._restore_mode()
        self.profile_combo.configure(values=[p["name"] for p in region_profiles.list_profiles()])
        self._draw()

    def close(self):
        self._cancel_seek()
        if self.reader:
            self.reader.close()
        self.destroy()
        if self.on_close:
            self.on_close()
