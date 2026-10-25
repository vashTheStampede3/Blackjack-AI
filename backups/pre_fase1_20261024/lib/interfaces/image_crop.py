"""Choose the image rectangle corresponding to the live capture, locally."""
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageTk


class ImageCrop(tk.Toplevel):
    def __init__(self, parent, source, on_save):
        super().__init__(parent)
        self.title("Align image — select the same area as the live capture")
        self.source, self.on_save = source, on_save
        h, w = source.original.shape[:2]
        self.scale = min((self.winfo_screenwidth()-100)/w,
                         (self.winfo_screenheight()-200)/h, 1)
        image = Image.fromarray(source.original[:, :, ::-1]).resize(
            (round(w*self.scale), round(h*self.scale)))
        self.photo = ImageTk.PhotoImage(image)
        self.canvas = tk.Canvas(self, width=image.width, height=image.height,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.rect = self.canvas.create_rectangle(
            *[v*self.scale for v in source.crop], outline="orange", width=3)
        self.start = None
        self.canvas.bind("<Button-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._move)
        tk.Label(self, text=f"Target {source.resolution[0]}×{source.resolution[1]} · "
                 "drag from the top-left of the matching area; aspect ratio is locked").pack()
        tk.Button(self, text="Use full image", command=self._full).pack(side="left")
        tk.Button(self, text="Apply crop", command=self._save).pack(side="right")
        self.grab_set()

    def _start(self, event):
        self.start = (max(0, event.x), max(0, event.y))

    def _move(self, event):
        if self.start is None:
            return
        x, y = self.start
        ratio = self.source.resolution[0] / self.source.resolution[1]
        width = min(max(2, event.x-x), self.photo.width()-x,
                    max(0, self.photo.height()-y)*ratio)
        self.canvas.coords(self.rect, x, y, x+width, y+width/ratio)

    def _full(self):
        self.canvas.coords(self.rect, 0, 0, self.photo.width(), self.photo.height())

    def _save(self):
        try:
            self.source.set_crop([round(v/self.scale)
                                  for v in self.canvas.coords(self.rect)])
        except ValueError as exc:
            messagebox.showerror("Align image", str(exc), parent=self)
            return
        self.destroy()
        self.on_save()
