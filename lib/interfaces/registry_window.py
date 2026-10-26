"""Inspection and explicit native-location selection; never changes a counter."""
from copy import deepcopy
import json
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


class RegistryWindow(tk.Toplevel):
    def __init__(self, parent, on_correct, on_add):
        super().__init__(parent)
        self.title("Lecturas y correcciones del mazo")
        self.geometry("1120x590")
        self.transient(parent)
        self.records = {}
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary).pack(fill=tk.X, padx=10, pady=8)
        columns = ("round", "group", "rank", "suit", "state", "confidence", "observed", "counted")
        self.tree = ttk.Treeview(self, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Identidad fisica")
        self.tree.column("#0", width=235)
        for column, label in zip(columns, ("Ronda", "Grupo", "Rango", "Palo", "Estado",
                                          "Confianza", "Validas / necesarias", "Contada")):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=90)
        self.tree.tag_configure("pending", foreground="#B45309")
        self.tree.tag_configure("uncertain", foreground="#B91C1C")
        self.tree.tag_configure("manual", foreground="#15803D")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._details())
        self.tree.bind("<Double-1>", lambda event: self._correct(on_correct))
        self.details = tk.Text(self, height=9, wrap="word", state="disabled")
        self.details.pack(fill=tk.X, padx=10, pady=6)
        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(actions, text="Corregir seleccionada", command=lambda: self._correct(on_correct)).pack(side=tk.LEFT)
        ttk.Button(actions, text="Anadir carta omitida", command=on_add).pack(side=tk.LEFT, padx=8)
        ttk.Label(actions, text="Naranja: pendiente | Rojo: incierta | Verde: manual").pack(side=tk.LEFT, padx=8)
        ttk.Button(actions, text="Cerrar", command=self.destroy).pack(side=tk.RIGHT)

    def _correct(self, callback):
        selected = self.tree.selection()
        if selected and selected[0] in self.records:
            callback(deepcopy(self.records[selected[0]]))

    def _details(self):
        selected = self.tree.selection()
        record = self.records.get(selected[0]) if selected else None
        text = json.dumps(record, ensure_ascii=False, indent=2) if record else "Selecciona una lectura para ver su evidencia e historial."
        self.details.configure(state="normal")
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def refresh(self, data):
        count = data["count"]
        self.summary.set(f"RC {count['running']:+d} | TC {count['true']:+.2f} | "
                         f"Vistas {count['cards_seen']} | "
                         f"{'CONTEO INCIERTO' if count.get('count_uncertain') else 'Sin dudas registradas'}")
        records = {row["id"]: row for row in data["cards"]}
        for identity in set(self.records)-set(records):
            self.tree.delete(identity)
        for identity, row in records.items():
            state = ("Manual" if row["manual"] else row["state"])
            if row["status"] == "archived":
                state += " (historica)"
            tag = ("uncertain" if row.get("reason") else "manual" if row["manual"]
                   else "pending" if row["state"] != "confirmed" else "")
            confidence = row.get("confidence")
            values = (row["round_id"], row.get("seat_id") or row["owner"], row["rank"] or "?",
                      row["suit"] or "?", state,
                      f"{confidence:.0%}" if confidence is not None else "?",
                      f"{row.get('valid_observations', 0)} / {row['required_observations']}",
                      "Si" if row["counted"] else "No")
            if identity not in self.records:
                self.tree.insert("", tk.END, iid=identity, text=identity, values=values, tags=(tag,))
            elif self.records[identity] != row:
                self.tree.item(identity, values=values, tags=(tag,))
        selected = self.tree.selection()
        changed = bool(selected and self.records.get(selected[0]) != records.get(selected[0]))
        self.records = deepcopy(records)
        if changed:
            self._details()


class ManualCardLocation(tk.Toplevel):
    """Require a real native bounding box before a manual identity is created."""
    def __init__(self, parent, frame, on_select, on_close=None):
        super().__init__(parent)
        self.on_close = on_close
        self._closed = False
        self.title("Carta omitida - marcar indice en la imagen")
        self.transient(parent)
        self.attributes("-topmost", True)
        self.scale = min(1., (self.winfo_screenwidth()-100)/frame.shape[1],
                         (self.winfo_screenheight()-180)/frame.shape[0])
        image = Image.fromarray(frame[:, :, ::-1])
        image = image.resize((round(image.width*self.scale), round(image.height*self.scale)))
        self.photo = ImageTk.PhotoImage(image)
        ttk.Label(self, text="Arrastra un rectangulo sobre el indice de la carta real omitida. "
                  "Debe quedar dentro de una region calibrada.").pack(padx=10, pady=8)
        self.canvas = tk.Canvas(self, width=image.width, height=image.height, cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.rectangle = self.canvas.create_rectangle(0, 0, 0, 0, outline="#F59E0B", width=2)
        self.start = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", lambda event: self._release(event, on_select))
        ttk.Button(self, text="Cancelar", command=self.destroy).pack(pady=6)
        self.grab_set()
        self.lift()

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        super().destroy()
        if callable(self.on_close):
            self.on_close()

    def _press(self, event):
        self.start = (max(0, event.x), max(0, event.y))

    def _move(self, event):
        if self.start is not None:
            self.canvas.coords(self.rectangle, *self.start,
                               min(self.photo.width(), max(0, event.x)),
                               min(self.photo.height(), max(0, event.y)))

    def _release(self, event, callback):
        if self.start is None:
            return
        self._move(event)
        x1, y1, x2, y2 = self.canvas.coords(self.rectangle)
        if abs(x2-x1) < 4 or abs(y2-y1) < 4:
            return
        bbox = [min(x1,x2)/self.scale, min(y1,y2)/self.scale,
                max(x1,x2)/self.scale, max(y1,y2)/self.scale]
        self.destroy()
        callback(bbox)
