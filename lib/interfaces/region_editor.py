"""Edit regions over a frozen capture or video frame, in native coordinates.

The official modes edit only their required polygons and validate before
writing. Calibration never constructs a detector or counts cards.
"""

import math
from numbers import Real
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from ..common import constants
from ..logic import region_profiles
from ..logic.app_modes import (INDIVIDUAL, MULTIPLAYER, APP_MODE_LABELS,
                               normalize_mode, zones_for_mode)
from ..logic.card_layout import CardLayout, ZONE_COLORS

C = constants.COLORS
HANDLE = 5  # handle radius in canvas px

EDITOR_ZONE_LABELS = {"table": "Mesa general", "mine": "Mi mano", "dealer": "Crupier"}


def build_layout_payload(mode, resolution, zone_keys, zones, scale, reference_source=None):
    """Convert canvas coordinates once, then delegate geometry to CardLayout."""
    mode = normalize_mode(mode)
    required = zones_for_mode(mode)
    if tuple(zone_keys) != required or len(zones) != len(required):
        raise ValueError("Las zonas no coinciden con el modo seleccionado.")
    if isinstance(scale, bool) or not isinstance(scale, Real) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("La escala debe ser numérica, finita y mayor que cero.")
    try:
        payload = {"mode": mode, "resolution": list(resolution)}
        for key, points in zip(required, zones):
            payload[key] = [[round(x / scale, 1), round(y / scale, 1)] for x, y in points]
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Coordenadas o resolución inválidas.") from None
    if reference_source is not None:
        payload["reference_source"] = reference_source
    return CardLayout.from_payload(payload, resolution).to_payload()


class RegionEditor(tk.Toplevel):
    def __init__(self, parent, capture, on_save=None, profile=None, mode=INDIVIDUAL):
        self.mode = normalize_mode(mode)
        self.zone_keys = zones_for_mode(self.mode)
        layout = region_profiles.get_card_layout(capture.resolution, self.mode, profile=profile)
        frame_bgr = capture.grab_bgr()
        audit = getattr(parent, "ui_actions", None)
        if audit:
            audit.record("CALIBRATION_FRAME", "Fotograma recibido para el editor; no se guarda la imagen.",
                         details={"expected_resolution": capture.resolution,
                                  "frame_resolution": [frame_bgr.shape[1], frame_bgr.shape[0]]})
        if (frame_bgr.shape[1], frame_bgr.shape[0]) != tuple(capture.resolution):
            raise ValueError("El tamaño capturado cambió. Volvé a seleccionar la ventana antes de calibrar.")
        super().__init__(parent)
        self.transient(parent)
        self._editor_parent = parent
        self._shown_logged = False
        self.bind("<Map>", self._on_editor_mapped, add="+")
        self.title(f"Editar regiones — {APP_MODE_LABELS[self.mode]}")
        self.configure(bg=C["bg_primary"])
        self.on_save = on_save
        self.capture = capture
        self.resolution = capture.resolution
        self._frame_bgr = frame_bgr

        img = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        # Fit the screenshot into a workable window, remember the scale to
        # convert canvas coords back to native pixels on save.
        max_w, max_h = min(1380, self.winfo_screenwidth() - 80), self.winfo_screenheight() - 270
        self.scale = min(max_w / img.width, max_h / img.height, 1.0)
        view = img.resize((int(img.width * self.scale), int(img.height * self.scale)),
                          Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(view)

        self.canvas = tk.Canvas(self, width=view.width, height=view.height,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=10, pady=(10, 6))
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")

        self.zones = [[] for _ in self.zone_keys]
        if layout is not None:
            self.zones = [[[x * self.scale, y * self.scale] for x, y in getattr(layout, key)]
                          for key in self.zone_keys]

        self._drag = None  # ("zone", zone index, vertex index)
        self._draw_start = None
        self.draw_mode = tk.BooleanVar(value=layout is None)
        controls = tk.Frame(self, bg=C["bg_primary"])
        controls.pack(fill=tk.X, padx=10, pady=3)
        tk.Label(controls, text="Zona:", bg=C["bg_primary"]).pack(side=tk.LEFT)
        zone_names = [EDITOR_ZONE_LABELS[key] for key in self.zone_keys]
        self.zone_combo = ttk.Combobox(controls, state="readonly", width=13, values=zone_names)
        self.zone_combo.current(0)
        self.zone_combo.pack(side=tk.LEFT, padx=6)
        tk.Checkbutton(controls, text="Dibujar rectángulo (arrastrar sobre la mesa)",
            variable=self.draw_mode, bg=C["bg_primary"]).pack(side=tk.LEFT)
        w, h = self.resolution
        hint = ("Dibujá dos regiones: una que contenga tus cartas y otra que contenga las cartas "
                "del crupier. No deben superponerse." if self.mode == INDIVIDUAL else
                "Dibujá la mesa general, tu región y la del crupier. Tu región debe estar dentro "
                "de la mesa general. Las demás manos se identificarán automáticamente.")
        hint += (" Arrastrá para crear un rectángulo. Desmarcá Dibujar para mover vértices. "
                 "Clic derecho en un borde agrega un punto; en un punto lo quita.")
        tk.Label(self, text=f"{getattr(capture, 'name', 'Captura')} · {w}×{h} · {hint}",
            wraplength=max_w,
            bg=C["bg_primary"], fg=C["text_secondary"]).pack(fill=tk.X, padx=10)
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._edit_vertex)

        bar = tk.Frame(self, bg=C["bg_primary"])
        bar.pack(fill=tk.X, padx=10, pady=(0, 10))
        legend = ("Naranja: Mi mano · Rojo: Crupier" if self.mode == INDIVIDUAL else
                  "Verde: Mesa general · Naranja: Mi mano · Rojo: Crupier")
        tk.Label(bar, text=legend,
                 bg=C["bg_primary"], fg=C["text_secondary"],
                 font=constants.FONT_SMALL).pack(side=tk.LEFT)
        actions = [("Guardar", self._save, C["success"])]
        actions.append(("Cancelar", self.destroy, C["text_secondary"]))
        for text, cmd, bg in actions:
            tk.Button(bar, text=text, command=cmd, bg=bg,
                      fg="white" if bg != C["warning"] else C["text_primary"],
                      relief="flat", padx=14, pady=6, font=constants.FONT_BODY,
                      cursor="hand2").pack(side=tk.RIGHT, padx=4)
        # Save target: a table profile by name. Prefilled with the active
        # profile; typing a new name creates it (and makes it active).
        self.profile_var = tk.StringVar(value=profile or region_profiles.active_name())
        entry = tk.Entry(bar, textvariable=self.profile_var, width=18,
                         font=constants.FONT_BODY)
        entry.pack(side=tk.RIGHT, padx=(12, 2))
        tk.Label(bar, text="Save as profile:", bg=C["bg_primary"],
                 fg=C["text_secondary"], font=constants.FONT_BODY
                 ).pack(side=tk.RIGHT)

        self._redraw()
        self.grab_set()

    def show_editor(self):
        """Restore and place the editor over its owner without permanent topmost."""
        self.update_idletasks()
        parent = self._editor_parent
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, min(parent.winfo_rootx() + (parent.winfo_width() - width) // 2,
                       self.winfo_screenwidth() - width))
        y = max(0, min(parent.winfo_rooty() + (parent.winfo_height() - height) // 2,
                       self.winfo_screenheight() - height))
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.lift()
        self.focus_set()
        self.grab_set()

    def _on_editor_mapped(self, event):
        if event.widget is not self or self._shown_logged:
            return
        self._shown_logged = True
        parent = self._editor_parent
        audit = getattr(parent, "ui_actions", None)
        if audit:
            audit.record("CALIBRATION_SHOWN", "Ventana del editor de regiones mostrada.",
                         details={"resolution": self.resolution, "mode": self.mode})
        if hasattr(parent, "set_status"):
            parent.set_status("Calibrate Regions visible: ajustá las zonas y pulsá Guardar.")

    # ------------------------------------------------------------- drawing

    def _shapes(self):
        for index, key in enumerate(self.zone_keys):
            yield "zone", index, self.zones[index], EDITOR_ZONE_LABELS[key], ZONE_COLORS[key]

    def _redraw(self):
        self.canvas.delete("overlay")
        for kind, _, pts, label, color in self._shapes():
            if not pts:
                continue
            flat = [c for p in pts for c in p]
            self.canvas.create_polygon(*flat, outline=color, fill="", width=2, tags="overlay")
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            self.canvas.create_text(cx, cy, text=label, fill=color,
                                    font=constants.FONT_BODY_BOLD, tags="overlay")
            for p in pts:
                self._handle(p, color)

    def _handle(self, p, color):
        x, y = p
        self.canvas.create_oval(x - HANDLE, y - HANDLE, x + HANDLE, y + HANDLE,
                                fill=color, outline="white", tags="overlay")

    # ------------------------------------------------------------- dragging

    def _press(self, event):
        if self.draw_mode.get():
            self._draw_start = self._point(event)
            self._drag = None
            return
        best, best_d = None, (HANDLE * 3) ** 2
        for kind, i, pts, _, _ in self._shapes():
            for j, (x, y) in enumerate(pts):
                d = (x - event.x) ** 2 + (y - event.y) ** 2
                if d < best_d:
                    best, best_d = (kind, i, j), d
        self._drag = best

    def _move(self, event):
        if self.draw_mode.get() and self._draw_start is not None:
            x1, y1 = self._draw_start
            x2, y2 = self._point(event)
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right - left < 2 or bottom - top < 2:
                return
            zone = self.zone_combo.current()
            if not 0 <= zone < len(self.zone_keys):
                return
            self.zones[zone] = [[left, top], [right, top], [right, bottom], [left, bottom]]
            self._redraw()
            return
        if self._drag is None:
            return
        x, y = self._point(event)
        kind, i, j = self._drag
        if kind != "zone" or not 0 <= i < len(self.zone_keys) or not 0 <= j < len(self.zones[i]):
            return
        self.zones[i][j] = [x, y]
        self._redraw()

    def _edit_vertex(self, event):
        """Add/remove vertices of the selected new-mode polygon, without a new editor."""
        zone = self.zone_combo.current()
        if not 0 <= zone < len(self.zone_keys):
            return
        pts = self.zones[zone]
        if not pts:
            return
        x, y = self._point(event)
        radius2 = (HANDLE * 3) ** 2
        for i, (px, py) in enumerate(pts):
            if (x - px) ** 2 + (y - py) ** 2 <= radius2:
                if len(pts) > 3:
                    pts.pop(i)
                    self._redraw()
                return
        best = None
        for i, ((ax, ay), (bx, by)) in enumerate(zip(pts, pts[1:] + pts[:1])):
            length2 = (bx - ax) ** 2 + (by - ay) ** 2
            if not length2:
                continue
            t = min(1., max(0., ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length2))
            px, py = ax + t * (bx - ax), ay + t * (by - ay)
            distance = (x - px) ** 2 + (y - py) ** 2
            if distance <= radius2:
                best, radius2 = (i + 1, [px, py]), distance
        if best:
            pts.insert(*best)
            self._redraw()

    def _point(self, event):
        return (min(max(event.x, 0), self._photo.width()),
                min(max(event.y, 0), self._photo.height()))

    def _release(self, event):
        if self._draw_start is not None:
            self._move(event)
        self._draw_start = self._drag = None

    # --------------------------------------------------------------- save

    def _save(self):
        self._save_card_layout()

    def _save_card_layout(self):
        try:
            profile = self.profile_var.get().strip()
            if not profile:
                raise ValueError("Escribí un nombre de perfil.")
            context = getattr(self.capture, "calibration_context", None)
            payload = build_layout_payload(self.mode, self.resolution, self.zone_keys,
                                           self.zones, self.scale, context)
            layout = CardLayout.from_payload(payload, self.resolution)
            region_profiles.set_card_layout(layout, profile=profile)
            saved = region_profiles.get_card_layout(self.resolution, self.mode, profile=profile)
            if saved is None or saved.to_payload() != layout.to_payload():
                raise ValueError("No se pudo comprobar el guardado; las zonas siguen abiertas.")
        except (OSError, ValueError) as exc:
            self._log_save_error(exc)
            messagebox.showerror("No se guardaron las zonas", str(exc), parent=self)
            return
        self._log_saved()
        if self.on_save:
            self.on_save()
        self.destroy()

    def _log_save_error(self, exc):
        audit = getattr(self._root(), "ui_actions", None)
        if audit:
            audit.log.exception("No se pudieron guardar/verificar las regiones.",
                                action_id=audit.current_id)
            audit.status(f"No se guardaron las regiones: {exc}", True)

    def _log_saved(self):
        audit = getattr(self._root(), "ui_actions", None)
        if audit:
            audit.record("PROFILE_SAVED", "CONFIRMADO · regiones guardadas y verificadas al releer el perfil.",
                         details={"profile": self.profile_var.get().strip(),
                                  "resolution": self.resolution, "mode": self.mode})
