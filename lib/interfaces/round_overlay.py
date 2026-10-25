"""One compact, interactive table overlay. The main application stays intact."""
import ctypes
import sys
import tkinter as tk
import screeninfo

from ..common import constants
from .capture_guard import apply_capture_guard

TRANSPARENT_KEY = "#010203"
VALID_RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANK_SHORT = {"Ace": "A", "Jack": "J", "Queen": "Q", "King": "K"}
RANK_FULL = {"A": "Ace", "J": "Jack", "Q": "Queen", "K": "King"}
WIDTH, HEIGHT = 1000, 310

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008


class MiniTableOverlay:
    """A single click-through Toplevel with clickable card chips and corrections."""
    def __init__(self, parent, on_correct_card=None, on_close=None):
        self.parent = parent
        self.on_correct_card = on_correct_card or (lambda *_args, **_kwargs: None)
        self.on_close = on_close
        self.win = tk.Toplevel(parent)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT_KEY)
        if sys.platform == "win32":
            self.win.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        self.canvas = tk.Canvas(self.win, width=WIDTH, height=HEIGHT,
                                bg=TRANSPARENT_KEY, bd=0, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.count_label = self.canvas  # compatibility for the former text-only panel
        self.opacity = float(constants.UI.get("round_opacity", .88))
        self.editing = False
        self.enabled = True
        self._capture_hidden = False
        self.affinity_ok = False
        self.hwnd = None
        self._last_state = None
        self._running = False
        self._selected_id = None
        self._selected_round = None
        self._selected_suit = None
        self._drag_origin = None
        self.win.geometry(f"{WIDTH}x{HEIGHT}+{int(constants.UI.get('round_x', 60))}+{int(constants.UI.get('round_y', 60))}")
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.win.update_idletasks()
        if sys.platform == "win32":
            try:
                self.hwnd = ctypes.windll.user32.GetAncestor(self.win.winfo_id(), 2)
                self._apply_win_styles()
                self.affinity_ok = apply_capture_guard(self.hwnd)
            except (AttributeError, OSError):
                self.affinity_ok = False
        self._draw()
        self._show_noactivate()
        self._topmost_job = self.win.after(1500, self._keep_topmost)

    def _apply_win_styles(self):
        if not self.hwnd:
            return
        user32 = ctypes.windll.user32
        get_style = user32.GetWindowLongPtrW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style = user32.SetWindowLongPtrW
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        style = get_style(self.hwnd, GWL_EXSTYLE)
        set_style(self.hwnd, GWL_EXSTYLE,
                  style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE |
                  WS_EX_LAYERED | WS_EX_TOPMOST)
        set_pos = user32.SetWindowPos
        set_pos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_uint]
        set_pos.restype = ctypes.c_bool
        show = user32.ShowWindow
        show.argtypes = [ctypes.c_void_p, ctypes.c_int]
        show.restype = ctypes.c_bool

    def _show_noactivate(self):
        if not self.enabled or self._capture_hidden or not self.win.winfo_exists():
            return
        if self.hwnd:
            ctypes.windll.user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            ctypes.windll.user32.SetWindowPos(self.hwnd, ctypes.c_void_p(-1),
                                              0, 0, 0, 0, 0x13)
        else:
            self.win.deiconify()

    def _keep_topmost(self):
        if not self.win.winfo_exists():
            return
        self._show_noactivate()
        self._topmost_job = self.win.after(1500, self._keep_topmost)

    def update_from_snapshot(self, snap, running=True):
        if not self.win.winfo_exists():
            return
        self._last_state = snap or {}
        self._running = bool(running)
        round_id = int(self._last_state.get("round", 0))
        if self._selected_round is not None and self._selected_round != round_id:
            self._selected_id = None
            self._selected_suit = None
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")
        state = self._last_state or {}
        count = state.get("count", {})
        rc, tc = count.get("running", 0), count.get("true", 0.)
        pending = bool((state.get("reading") or {}).get("count_pending"))
        uncertain = bool(count.get("count_uncertain"))
        status = " · INCIERTO" if uncertain else (" · LEYENDO" if pending else "")
        self._rect(8, 8, WIDTH-8, 48, "#111827", "#64748B")
        self._text(22, 27, f"RC {rc:+d}   TC {tc:+.1f}{status}", "#F8FAFC", 12, "w")
        dealer = state.get("dealer") or {}
        self._text(270, 27, "Crupier", "#BFDBFE", 9, "w")
        dealer_cards = state.get("dealer_cards") or []
        if dealer_cards:
            for index, card in enumerate(dealer_cards[:8]):
                self._draw_card(340+index*30, 10, card)
        elif dealer.get("card"):
            self._text(325, 27, self._rank(dealer.get("card")), "#F8FAFC", 10, "w")
        advice = self._advice_text(state)
        self._rect(WIDTH-310, 13, WIDTH-18, 43,
                   "#7F1D1D" if uncertain else "#14532D", "#475569")
        self._text(WIDTH-164, 28, advice, "#F8FAFC", 9, "center")
        self._rect(8, 58, WIDTH-8, 166, "#0F172A", "#334155")
        groups = state.get("card_groups") or []
        for group in groups:
            self._draw_group(group)
        if not groups:
            self._text(WIDTH/2, 108, "Esperando cartas reconocidas", "#94A3B8", 11, "center")
        close_tag = "close"
        c.create_text(WIDTH-22, 28, text="×", fill="#CBD5E1", font=("Segoe UI", 13, "bold"), tags=(close_tag,))
        c.tag_bind(close_tag, "<Button-1>", lambda _event: self._close_by_user())
        if self._selected_id:
            self._draw_picker(state)
        reading = state.get("reading") or {}
        active = bool(self._running and (reading.get("present")
                      or groups or state.get("dealer_cards")))
        self.win.attributes("-alpha", self.opacity if active else min(.30, self.opacity))

    def _draw_group(self, group):
        sid = str(group.get("seat_id", "?"))
        try:
            order = int(sid[1:]) if sid.startswith("J") else 4
        except ValueError:
            order = 4
        slot = min(6, max(0, order-1))
        x1 = WIDTH - 18 - (slot+1)*137
        x2 = WIDTH - 18 - slot*137 - 3
        y1, y2 = 68, 156
        ambiguous = bool(group.get("is_ambiguous"))
        hero = bool(group.get("is_hero"))
        color = "#F59E0B" if ambiguous else ("#22C55E" if hero else "#475569")
        self._rect(x1, y1, x2, y2, "#172417" if hero else "#1E293B", color)
        label = f"⚠ {sid} · asiento dudoso" if ambiguous else (f"★ {sid} (Vos)" if hero else sid)
        self._text((x1+x2)/2, y1+13, label, color if not ambiguous else "#FDE68A", 8, "center")
        if group.get("has_split"):
            self._text(x2-8, y1+13, "DIV", "#FDE68A", 7, "e")
        for index, card in enumerate(group.get("cards", [])):
            col, row = index % 4, index // 4
            x, y = x1+8+col*31, y1+25+row*38
            self._draw_card(x, y, card)

    def _draw_card(self, x, y, card):
        identity = str(card.get("card_id", ""))
        rank = card.get("rank")
        suit = SUIT_SYMBOLS.get(card.get("suit"), "")
        unknown = rank is None
        fill, edge, fg = (("#7C2D12", "#F97316", "#FFEDD5") if unknown else
                          ("#F8FAFC", "#EAB308", "#0F172A") if card.get("ambiguous_seat") else
                          ("#F8FAFC", "#38BDF8", "#0F172A"))
        tag = f"card:{identity}"
        self.canvas.create_rectangle(x, y, x+27, y+34, fill=fill, outline=edge, width=2,
                                     tags=(tag,))
        self.canvas.create_text(x+13.5, y+17, text=("?" if unknown else self._rank(rank))+suit,
                                fill=fg, font=("Segoe UI", 8, "bold"), tags=(tag,))
        self.canvas.tag_bind(tag, "<Button-1>",
                             lambda _event, cid=identity: self._select_card(cid))

    def _draw_picker(self, state):
        x1, y1, x2, y2 = 12, 174, WIDTH-12, HEIGHT-8
        self._rect(x1, y1, x2, y2, "#0B1220", "#38BDF8")
        self._text(x1+10, y1+14, "Corregir carta · vistos en shoe", "#E2E8F0", 8, "w")
        seen = state.get("seen_by_rank") or {}
        step = (x2-x1-20)/13
        for index, rank in enumerate(VALID_RANKS):
            bx = x1+10+index*step
            tag = f"rank:{rank}"
            full = RANK_FULL.get(rank, rank)
            amount = seen.get(full, 0)
            self._rect(bx, y1+25, bx+step-3, y1+63, "#1E293B", "#64748B")
            self.canvas.create_text(bx+(step-3)/2, y1+37, text=rank, fill="#F8FAFC",
                                    font=("Segoe UI", 9, "bold"), tags=(tag,))
            self.canvas.create_text(bx+(step-3)/2, y1+53, text=f"({amount})", fill="#7DD3FC",
                                    font=("Consolas", 7), tags=(tag,))
            self.canvas.tag_bind(tag, "<Button-1>",
                lambda _event, val=rank: self._emit("set_rank", new_rank=val))
        sy = y1+77
        self._text(x1+8, sy+10, "Palo", "#94A3B8", 8, "w")
        sx = x1+54
        for code, symbol in SUIT_SYMBOLS.items():
            tag = f"suit:{code}"
            self._rect(sx, sy, sx+28, sy+22, "#1D4ED8" if self._selected_suit == code else "#1E293B", "#475569")
            self.canvas.create_text(sx+14, sy+11, text=symbol, fill="#F8FAFC", tags=(tag,))
            self.canvas.tag_bind(tag, "<Button-1>", lambda _event, val=code: self._toggle_suit(val))
            sx += 32
        self._button_text(x1+198, sy+11, "«no era carta»", "discard", "#7F1D1D")
        self._text(x1+350, sy+11, "Cambiar jugador", "#CBD5E1", 8, "center")
        sx = x1+421
        for number in range(1, 8):
            self._button_text(sx, sy+11, f"J{number}", f"seat:J{number}", "#1E293B", width=34)
            sx += 38

    def _button_text(self, x, y, text, tag, color, width=132):
        self._rect(x-width/2, y-11, x+width/2, y+11, color, "#64748B")
        self.canvas.create_text(x, y, text=text, fill="#F8FAFC",
                                font=("Segoe UI", 8, "bold"), tags=(tag,))
        action = "not_a_card" if tag == "discard" else None
        seat = tag.split(":", 1)[1] if tag.startswith("seat:") else None
        if action:
            self.canvas.tag_bind(tag, "<Button-1>", lambda _event: self._emit(action))
        if seat:
            self.canvas.tag_bind(tag, "<Button-1>",
                                 lambda _event, val=seat: self._emit("change_seat", new_seat_id=val))

    def _rect(self, x1, y1, x2, y2, fill, outline):
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=1)

    def _text(self, x, y, text, color, size, anchor):
        self.canvas.create_text(x, y, text=text, fill=color,
                                font=("Segoe UI", size, "bold"), anchor=anchor)

    @staticmethod
    def _rank(rank):
        value = RANK_SHORT.get(rank, str(rank))
        return value

    @staticmethod
    def _advice_text(state):
        if state.get("count", {}).get("count_uncertain"):
            return "SIN CONSEJO · lectura dudosa"
        mine = next((seat for seat in state.get("seats", []) if seat.get("mine")), None)
        if mine and mine.get("advice_withdrawn_reason"):
            return "SIN CONSEJO · lectura dudosa"
        return (mine or {}).get("optimal") or (mine or {}).get("advice") or "Esperando mano propia"

    def _canvas_click(self, event):
        if 8 <= event.y <= 48 and event.x < 240:
            self._drag_origin = (event.x_root-self.win.winfo_x(),
                                 event.y_root-self.win.winfo_y())

    def _drag_move(self, event):
        if self.editing and self._drag_origin:
            x = event.x_root-self._drag_origin[0]
            y = event.y_root-self._drag_origin[1]
            self.win.geometry(f"{WIDTH}x{HEIGHT}{x:+d}{y:+d}")

    def _select_card(self, identity):
        self._selected_id = identity
        self._selected_round = int((self._last_state or {}).get("round", 0))
        self._draw()

    def _toggle_suit(self, suit):
        self._selected_suit = None if self._selected_suit == suit else suit
        self._draw()

    def _emit(self, action, new_rank=None, new_seat_id=None):
        if not self._selected_id:
            return
        self.on_correct_card(self._selected_id, self._selected_round, action,
                             new_rank, self._selected_suit, new_seat_id)
        self._selected_id = None
        self._selected_suit = None
        self._draw()

    def set_opacity(self, opacity):
        self.opacity = max(.25, min(1., float(opacity)))
        constants.UI["round_opacity"] = self.opacity
        self.win.attributes("-alpha", self.opacity)

    def set_editing(self, enabled):
        self.editing = bool(enabled)

    def _show_noactivate(self):
        self.enabled = True
        self._capture_hidden = False
        self._show_window()

    def _show_window(self):
        if not self.win.winfo_exists() or self._capture_hidden:
            return
        if self.hwnd:
            ctypes.windll.user32.ShowWindow(self.hwnd, 4)
            ctypes.windll.user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x13)
        else:
            self.win.deiconify()

    def hide(self):
        self.enabled = False
        self.win.withdraw()

    def ensure_on_screen(self):
        self.win.update_idletasks()
        x, y = self.win.winfo_x(), self.win.winfo_y()
        width, height = self.win.winfo_width(), self.win.winfo_height()
        monitors = list(screeninfo.get_monitors())
        if any(x >= m.x and y >= m.y and x+width <= m.x+m.width
               and y+height <= m.y+m.height for m in monitors):
            return
        monitor = next((m for m in monitors if getattr(m, "is_primary", False)),
                       monitors[0] if monitors else None)
        if monitor:
            self.move_to(monitor.x+30, monitor.y+60)

    def move_to(self, x, y):
        self.win.geometry(f"{WIDTH}x{HEIGHT}{int(x):+d}{int(y):+d}")

    def remember_position(self):
        constants.UI.update(round_x=self.win.winfo_x(), round_y=self.win.winfo_y())

    def set_capture_hidden(self, hidden):
        self._capture_hidden = bool(hidden)
        if hidden:
            self.win.withdraw()
        elif self.enabled:
            self._show_window()
        self.win.update_idletasks()

    def winfo_exists(self):
        return self.win.winfo_exists()

    def winfo_x(self):
        return self.win.winfo_x()

    def winfo_y(self):
        return self.win.winfo_y()

    def winfo_width(self):
        return self.win.winfo_width()

    def geometry(self, value=None):
        return self.win.geometry(value) if value is not None else self.win.geometry()

    def update(self):
        return self.win.update()

    def attributes(self, *args):
        return self.win.attributes(*args)

    def _drag_start(self, event):
        self._drag_origin = (event.x_root-self.win.winfo_x(),
                             event.y_root-self.win.winfo_y())

    def _close_by_user(self):
        self.close()
        if self.on_close:
            self.on_close()

    def close(self):
        if self.win.winfo_exists():
            try:
                self.win.after_cancel(self._topmost_job)
            except tk.TclError:
                pass
            self.remember_position()
            self.win.destroy()


# Import compatibility for older callers; both names now create the same widget.
RoundOverlay = MiniTableOverlay
