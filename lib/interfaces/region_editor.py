"""Edit regions over a frozen capture or video frame, in native coordinates.

The seven-seat mode keeps its existing profile format. The two table modes
save separate Mesa / Mi asiento / Crupier polygons and validate them before
writing. Calibration never constructs a detector or counts cards.
"""

import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from ..common import constants
from ..logic import monitor_utils, region_profiles
from ..logic.card_layout import (SEVEN_SEATS, MODE_LABELS, ZONE_COLORS,
                                 ZONE_LABELS, CardLayout)

C = constants.COLORS
HANDLE = 5  # handle radius in canvas px


class RegionEditor(tk.Toplevel):
    def __init__(self, parent, capture, on_save=None, profile=None, mode=SEVEN_SEATS):
        if mode not in MODE_LABELS:
            raise ValueError("Modo de zonas desconocido.")
        layout = (region_profiles.get_card_layout(capture.resolution, mode, profile=profile)
                  if mode != SEVEN_SEATS else None)
        frame_bgr = capture.grab_bgr()
        super().__init__(parent)
        self.title(f"Editar zonas — {MODE_LABELS[mode]}")
        self.configure(bg=C["bg_primary"])
        self.on_save = on_save
        self.capture = capture
        self.resolution = capture.resolution
        self._frame_bgr = frame_bgr
        self.mode = mode

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

        self.polys = []
        self.dealer = []
        self.zones = [[] for _ in ZONE_LABELS]
        if mode == SEVEN_SEATS:
            custom = monitor_utils.load_custom_regions(self.resolution, profile=profile)
            players = custom["players"] if custom else monitor_utils.default_player_regions(self.resolution)
            dealer = custom["dealer"] if custom else monitor_utils.default_dealer_rect(self.resolution)
            # Drop the duplicated closing vertex for editing; re-added on save.
            for poly in players:
                pts = [list(p) for p in poly]
                if len(pts) > 1 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                self.polys.append([[x * self.scale, y * self.scale] for x, y in pts])
            left, top, right, bottom = dealer
            self.dealer = [[left * self.scale, top * self.scale],
                           [right * self.scale, bottom * self.scale]]
        elif layout is not None:
            self.zones = [[[x * self.scale, y * self.scale] for x, y in getattr(layout, key)]
                          for key in ZONE_LABELS]

        self._drag = None  # ("poly", i, j) or ("dealer", i)
        self._draw_start = None
        self.draw_mode = tk.BooleanVar(value=mode != SEVEN_SEATS and layout is None)
        controls = tk.Frame(self, bg=C["bg_primary"])
        controls.pack(fill=tk.X, padx=10, pady=3)
        tk.Label(controls, text="Zona:", bg=C["bg_primary"]).pack(side=tk.LEFT)
        zone_names = ([f"P{i + 1}" for i in range(constants.NUM_SEATS)] + ["Crupier"]
                      if mode == SEVEN_SEATS else list(ZONE_LABELS.values()))
        self.zone_combo = ttk.Combobox(controls, state="readonly", width=13, values=zone_names)
        self.zone_combo.current(0)
        self.zone_combo.pack(side=tk.LEFT, padx=6)
        tk.Checkbutton(controls, text="Dibujar rectángulo (arrastrar sobre la mesa)",
            variable=self.draw_mode, bg=C["bg_primary"]).pack(side=tk.LEFT)
        w, h = self.resolution
        hint = ("P1 izquierda / P7 derecha. Desmarcá Dibujar para ajustar vértices."
                if mode == SEVEN_SEATS else
                "Mi asiento va dentro de Mesa. Crupier puede ir fuera, sobre el gráfico del casino. "
                "Si usás el gráfico, dejá las cartas físicas del crupier fuera de Mesa. "
                "Desmarcá Dibujar para mover puntos. "
                "Clic derecho en un borde agrega un punto; en un punto lo quita.")
        tk.Label(self, text=f"{getattr(capture, 'name', 'Captura')} · {w}×{h} · {hint}",
            wraplength=max_w,
            bg=C["bg_primary"], fg=C["text_secondary"]).pack(fill=tk.X, padx=10)
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        if mode != SEVEN_SEATS:
            self.canvas.bind("<Button-3>", self._edit_vertex)

        bar = tk.Frame(self, bg=C["bg_primary"])
        bar.pack(fill=tk.X, padx=10, pady=(0, 10))
        legend = ("Naranja: asientos · Rojo: crupier" if mode == SEVEN_SEATS else
                  "Verde: Mesa · Naranja: Mi asiento · Rojo: Crupier")
        tk.Label(bar, text=legend,
                 bg=C["bg_primary"], fg=C["text_secondary"],
                 font=constants.FONT_SMALL).pack(side=tk.LEFT)
        actions = [("Guardar", self._save, C["success"])]
        if profile is None and mode == SEVEN_SEATS:
            actions.append(("Reset to defaults", self._reset, C["warning"]))
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

    # ------------------------------------------------------------- drawing

    def _shapes(self):
        if self.mode != SEVEN_SEATS:
            for i, (key, label) in enumerate(ZONE_LABELS.items()):
                yield "zone", i, self.zones[i], label, ZONE_COLORS[key]
        else:
            for i, pts in enumerate(self.polys):
                yield "poly", i, pts, f"P{i + 1}", "#ff9f1a"
            yield "dealer", 0, self.dealer, "Crupier", "#ff4d4d"

    def _redraw(self):
        self.canvas.delete("overlay")
        for kind, _, pts, label, color in self._shapes():
            if not pts:
                continue
            flat = [c for p in pts for c in p]
            if kind == "dealer":
                self.canvas.create_rectangle(*flat, outline=color, width=2, tags="overlay")
            else:
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
            if self.mode != SEVEN_SEATS:
                self.zones[zone] = [[left, top], [right, top], [right, bottom], [left, bottom]]
            elif zone == constants.NUM_SEATS:
                self.dealer = [[left, top], [right, bottom]]
            else:
                self.polys[zone] = [[left, top], [right, top], [right, bottom], [left, bottom]]
            self._redraw()
            return
        if self._drag is None:
            return
        x, y = self._point(event)
        kind, i, j = self._drag
        if kind == "zone":
            self.zones[i][j] = [x, y]
        elif kind == "poly":
            self.polys[i][j] = [x, y]
        else:
            self.dealer[j] = [x, y]
        self._redraw()

    def _edit_vertex(self, event):
        """Add/remove vertices of the selected new-mode polygon, without a new editor."""
        if self.mode == SEVEN_SEATS:
            return
        pts = self.zones[self.zone_combo.current()]
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
        if self.mode != SEVEN_SEATS:
            self._save_card_layout()
            return
        players = []
        for pts in self.polys:
            native = [[round(x / self.scale, 1), round(y / self.scale, 1)]
                      for x, y in pts]
            native.append(list(native[0]))  # close the polygon (base format)
            players.append(native)
        (x1, y1), (x2, y2) = self.dealer
        dealer = [int(min(x1, x2) / self.scale), int(min(y1, y2) / self.scale),
                  int(max(x1, x2) / self.scale), int(max(y1, y2) / self.scale)]
        options = {"profile": self.profile_var.get()}
        context = getattr(self.capture, "calibration_context", None)
        if context is not None:
            options["source_context"] = context
        try:
            monitor_utils.save_custom_regions(self.resolution, players, dealer, **options)
            if context is not None:
                saved = region_profiles.get_regions(self.resolution, profile=self.profile_var.get().strip())
                if (not saved or saved.get("players") != players or saved.get("dealer") != dealer
                        or saved.get("reference_source") != context):
                    raise ValueError("No se pudo comprobar el guardado. Las zonas siguen abiertas para reintentar.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se guardaron las zonas", str(exc), parent=self)
            return
        if self.on_save:
            self.on_save()
        self.destroy()

    def _save_card_layout(self):
        try:
            profile = self.profile_var.get().strip()
            if not profile:
                raise ValueError("Escribí un nombre de perfil.")
            payload = {"mode": self.mode, "resolution": list(self.resolution)}
            for key, pts in zip(ZONE_LABELS, self.zones):
                payload[key] = [[round(x / self.scale, 1), round(y / self.scale, 1)] for x, y in pts]
            context = getattr(self.capture, "calibration_context", None)
            if context is not None:
                payload["reference_source"] = context
            layout = CardLayout.from_payload(payload, self.resolution)
            region_profiles.set_card_layout(layout, profile=profile)
            saved = region_profiles.get_card_layout(self.resolution, self.mode, profile=profile)
            if saved is None or saved.to_payload() != layout.to_payload():
                raise ValueError("No se pudo comprobar el guardado; las zonas siguen abiertas.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se guardaron las zonas", str(exc), parent=self)
            return
        if self.on_save:
            self.on_save()
        self.destroy()

    def _reset(self):
        # Reset targets the ACTIVE profile (the typed save-as name is a save
        # target only) — name it and confirm before destroying a calibration.
        active = region_profiles.active_name()
        if not messagebox.askyesno(
                "Reset to defaults",
                f"Remove the calibrated layout of profile \"{active}\" for "
                "this resolution and use the shipped defaults?", parent=self):
            return
        monitor_utils.delete_custom_regions(self.resolution)
        if self.on_save:
            self.on_save(False)
        self.destroy()
