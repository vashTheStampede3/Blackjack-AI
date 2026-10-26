"""Mini Mesa renders and selects cards by their physical registry identity."""
from copy import deepcopy
import ctypes
import math
import sys
import tkinter as tk
import screeninfo

from ..common import constants
from .capture_guard import apply_capture_guard

TRANSPARENT_KEY = "#010203"
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣",
                "Spades": "♠", "Hearts": "♥", "Diamonds": "♦", "Clubs": "♣"}
RANK_SHORT = {"Ace": "A", "Jack": "J", "Queen": "Q", "King": "K"}
WIDTH, HEIGHT = 1000, 310


def overlay_visibility(presentation, running, editing=False, hud_on=True):
    """Pure visibility policy; reading state belongs to the shared presenter."""
    if not hud_on:
        return False, ""
    if editing:
        return True, "AJUSTE DE POSICIÓN"
    state = presentation or {}
    if not running or state.get("status") == "stopped":
        return True, "DETECCIÓN DETENIDA"
    message = state.get("message") or "Esperando cartas"
    if state.get("status") == "uncertain" and "SIN CONSEJO" not in message:
        message += " · SIN CONSEJO"
    return True, message


def card_chip_position(index, count, center=WIDTH / 2, y=80, span=WIDTH - 80):
    """A centered, bounded fan of exactly the published cards."""
    card_w, card_h = 27, 34
    step = min(34, max(0, (span - card_w) / max(count - 1, 1)))
    left = center - (card_w + max(0, count - 1) * step) / 2
    return left + index * step, y, (card_w, card_h)


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008


class MiniTableOverlay:
    """One overlay window with card correction routed through the GUI callback."""
    def __init__(self, parent, on_correct_card=None, on_close=None):
        self.parent = parent
        self.on_correct_card = on_correct_card
        self.identity_corrections_enabled = callable(on_correct_card)
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
        self._metrics = {}
        self._drawn_cards = []
        self._running = False
        self._selected_id = None
        self._selected_round = None
        self._selected_suit = None
        self._selected_ref = None
        self._selected_expected_name = None
        self._notice = ""
        self._active = False
        self._drag_origin = None
        self._pointer_start = None
        self._pressed_identity = None
        self.win.geometry(f"{WIDTH}x{HEIGHT}+{int(constants.UI.get('round_x', 60))}+{int(constants.UI.get('round_y', 60))}")
        self.canvas.bind("<ButtonPress-1>", self._pointer_press, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._pointer_release, add="+")
        self.canvas.bind("<B1-Motion>", self._drag_move, add="+")
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
        if not self.enabled or self._capture_hidden or not self._active or not self.win.winfo_exists():
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
        if self._active:
            self._show_noactivate()
        self._topmost_job = self.win.after(1500, self._keep_topmost)

    def update_from_presentation(self, presentation, *, running=True, metrics=None):
        """Receive detached UI data, never interpret engine snapshots or advice."""
        if not self.win.winfo_exists():
            return
        state = presentation or {}
        # Only scalar aggregate fields can enter the header. No physical data
        # or recommendations are retained through this optional channel.
        metrics = metrics or {}
        aggregates = {}
        if type(metrics.get("running")) is int:
            aggregates["running"] = metrics["running"]
        if type(metrics.get("true")) in (int, float):
            aggregates["true"] = metrics["true"]
        if type(metrics.get("count_uncertain")) is bool:
            aggregates["count_uncertain"] = metrics["count_uncertain"]
        if type(metrics.get("shoe_uncertainty")) is bool:
            aggregates["shoe_uncertainty"] = metrics["shoe_uncertainty"]
        if type(metrics.get("cards_seen")) is int and metrics["cards_seen"] >= 0:
            aggregates["cards_seen"] = metrics["cards_seen"]
        if type(metrics.get("decks_remaining")) in (int, float):
            aggregates["decks_remaining"] = metrics["decks_remaining"]
        changed = (state != self._last_state or bool(running) != self._running
                   or aggregates != self._metrics)
        # No selection survives while editing is disabled, including stale
        # references retained by an older UI across a round/shoe change.
        self._selected_id = self._selected_round = self._selected_ref = None
        self._selected_suit = self._selected_expected_name = None
        if not changed:
            return
        self._last_state = deepcopy(state)
        self._metrics = deepcopy(aggregates)
        self._running = bool(running)
        self._notice = overlay_visibility(state, self._running, self.editing, self.enabled)[1]
        self._draw()

    def _selection_exists(self, ref):
        """Check card and displayed source context before forwarding a click."""
        state = self._last_state or {}
        context_keys = ("round_id", "shoe_id", "profile_name", "mode", "resolution",
                        "source_id", "source_ref")
        if any(key in ref and ref[key] != state.get(key) for key in context_keys):
            return False
        cards = list((state.get("dealer") or {}).get("cards") or [])
        for player in state.get("players") or []:
            cards.extend(player.get("cards") or [])
        if ref.get("card_id") is not None:
            return any(
                card.get("card_id") == ref["card_id"]
                and card.get("state") not in ("rejected", "retired")
                and all(key not in ref or key not in card or card.get(key) == ref[key]
                        for key in context_keys)
                for card in cards)
        return False

    def _draw(self):
        self.canvas.delete("all")
        self._drawn_cards = []
        state = self._last_state or {}
        active, notice = overlay_visibility(state, self._running, self.editing, self.enabled)
        self._active = active
        blocked = not self._running or state.get("status") in (
            "stopped", "error", "needs_calibration", "source_mismatch")
        metrics = self._metrics
        rc, tc = metrics.get("running"), metrics.get("true")
        rc_text = f"{rc:+d}" if rc is not None else "—"
        tc_text = f"{tc:+.1f}" if tc is not None else "—"
        decks, seen = metrics.get("decks_remaining"), metrics.get("cards_seen")
        decks_text = f"{decks:.1f}" if type(decks) in (int, float) else "—"
        seen_text = str(seen) if type(seen) is int else "—"
        uncertain = bool(metrics.get("count_uncertain")
                         or metrics.get("shoe_uncertainty")
                         or state.get("count_has_pending_uncertainty"))
        marker = " · ⚠ Lectura dudosa" if uncertain else ""
        mode = "Individual" if state.get("mode") == "individual" else (
            "Multijugador" if state.get("mode") == "multiplayer" else "Sin calibrar")
        round_id = state.get("round_id")
        round_text = f"Ronda #{round_id}" if type(round_id) is int else "Ronda —"
        motor_state = "Activo" if self._running else "Detenido"
        self._rect(8, 8, WIDTH - 8, 52, "#111827", "#64748B")
        self._text(18, 18,
                   f"RC: {rc_text}  |  TC: {tc_text}  |  Mazo: {decks_text}  |  Vistas: {seen_text}",
                   "#F8FAFC", 9, "w")
        self._text(18, 38, f"{round_text}  |  {mode}  |  {motor_state}{marker}",
                   "#FDE68A" if uncertain else "#CBD5E1", 8, "w")
        self._text(WIDTH - 175, 28, self._advice_text(state, self._running),
                   "#F8FAFC", 8, "center")
        self._rect(8, 58, WIDTH - 8, 232, "#0F172A", "#334155")
        self._text(WIDTH / 2, 65, "Crupier", "#BFDBFE", 9, "center")
        dealer_cards = [] if blocked else (state.get("dealer") or {}).get("cards", [])
        self._draw_hand(dealer_cards, WIDTH / 2, 80)
        players = list(state.get("players", [])[:7])
        if players:
            centers = self._player_centers(len(players))
            for player, center in zip(players, centers):
                self._draw_group(player, center=center, blocked=blocked)
        elif state.get("mode") == "multiplayer":
            self._text(WIDTH / 2, 172, "Sin grupos observados", "#94A3B8", 9, "center")
        self.canvas.create_text(WIDTH - 22, 28, text="×", fill="#CBD5E1",
                                font=("Segoe UI", 13, "bold"), tags=("close",))
        self.canvas.tag_bind("close", "<Button-1>", lambda _event: self._close_by_user())
        group_warning = state.get("group_warning")
        if group_warning:
            self._text(WIDTH / 2, 238, group_warning, "#FDE68A", 9, "center")
        if notice:
            self._rect(8, 246, WIDTH - 8, 282, "#7F1D1D", "#FCA5A5")
            self._text(WIDTH / 2, 264, notice, "#FEE2E2", 9, "center")
        if active:
            self._show_window()  # capture/activation guards remain authoritative
            self.win.attributes("-alpha", self.opacity)
        else:
            self.win.withdraw()

    def _draw_hand(self, cards, center, y, span=WIDTH - 80):
        if not cards:
            self._text(center, y + 17, "[sin cartas]", "#94A3B8", 9, "center")
        for index, card in enumerate(cards):
            x, top, size = card_chip_position(index, len(cards), center, y, span)
            self._draw_card(x, top, card, size=size)

    @staticmethod
    def _player_centers(count):
        if count <= 0:
            return []
        if count == 1:
            return [WIDTH / 2]
        span = min(WIDTH - 150, 136 * (count - 1))
        left = (WIDTH - span) / 2
        return [left + span * index / (count - 1) for index in range(count)]

    def _draw_group(self, player, *, center=WIDTH / 2, blocked=False):
        """Render one published group; never infer ownership or clustering."""
        hero = bool(player.get("is_hero"))
        provisional = bool(player.get("is_provisional"))
        ambiguous = bool(player.get("is_ambiguous"))
        label = "Yo" if hero else str(player.get("label") or player.get("id") or "?")
        if provisional:
            provisional_id = player.get("group_id") or player.get("seat_id") or player.get("id")
            if str(provisional_id) not in label:
                label += f" · {provisional_id}"
            label += " · PROVISIONAL"
        if ambiguous:
            label += " · AMBIGUO / ?"
        color = "#22C55E" if hero else "#FDE68A" if provisional or ambiguous else "#CBD5E1"
        self._rect(center - 65, 130, center + 65, 224,
                   "#16351F" if hero else "#172033",
                   "#22C55E" if hero else "#F59E0B" if provisional or ambiguous else "#475569")
        self._text(center, 140, label, color, 8, "center")
        cards = [] if blocked else player.get("cards", [])
        tags = [card.get("hand") for card in cards]
        split = bool(player.get("has_split"))
        valid = (split and bool(cards)
                 and all(type(tag) is int and tag in (0, 1) for tag in tags)
                 and set(tags) == {0, 1})
        if valid:
            hand_centers = (center - 34, center + 34)
            for hand, hand_center in enumerate(hand_centers):
                self._text(hand_center, 155, f"Mano {hand + 1}", "#CBD5E1", 7, "center")
            depths, counts = [0, 0], [tags.count(0), tags.count(1)]
            # Iterate publication order, keeping the original identity on each
            # item. Positioning by explicit tags does not discover a split.
            for card, hand in zip(cards, tags):
                x, y, size = card_chip_position(depths[hand], counts[hand], hand_centers[hand], 166, 58)
                self._draw_card(x, y, card, size=size)
                depths[hand] += 1
        else:
            self._draw_hand(cards, center, 166, span=125)
            if split and cards:
                self._text(center, 218, "Split incierto", "#FDE68A", 7, "center")

    def _draw_card(self, x, y, card, size=(27, 34)):
        card_w, card_h = size
        rank = card.get("rank")
        confirmed = card.get("state") == "confirmed" and not card.get("reason") and rank is not None
        label = self._rank(rank) + SUIT_SYMBOLS.get(card.get("suit"), "") if confirmed else "?"
        fill, edge, fg = (("#F8FAFC", "#38BDF8", "#0F172A") if confirmed else
                          ("#7C2D12", "#F97316", "#FFEDD5"))
        tag = f"physical-card-{len(self._drawn_cards)}"
        item = self.canvas.create_rectangle(x, y, x + card_w, y + card_h,
                                             fill=fill, outline=edge, width=2,
                                             tags=(tag,))
        self.canvas.create_text(x + card_w / 2, y + card_h / 2, text=label,
                                fill=fg, font=("Segoe UI", 7, "bold"), tags=(tag,))
        identity_fields = (
            "card_id", "legacy_ref", "rank", "suit", "state", "reason", "owner",
            "hand", "group_id", "round_id", "shoe_id", "profile_name", "mode",
            "resolution", "source_id", "source_ref",
        )
        identity = {key: deepcopy(card[key]) for key in identity_fields if key in card}
        self._drawn_cards.append({"item": item, "tag": tag,
                                  "card": deepcopy(card), "identity": identity})
        if (getattr(self, "identity_corrections_enabled", False)
                and callable(self.on_correct_card)
                and isinstance(identity.get("card_id"), str)
                and identity["card_id"].strip()):
            self.canvas.tag_bind(tag, "<ButtonPress-1>",
                                 lambda _event, ref=deepcopy(identity): self._remember_pressed_identity(ref))

    def _remember_pressed_identity(self, identity):
        self._pressed_identity = deepcopy(identity)

    def _identity_at_pointer(self, event):
        try:
            current = self.canvas.find_withtag("current")
            tags = self.canvas.gettags(current[0]) if current else ()
        except (AttributeError, tk.TclError, IndexError):
            return None
        for record in self._drawn_cards:
            if record.get("tag") in tags:
                return deepcopy(record["identity"])
        return None

    def _pointer_press(self, event):
        self._pointer_start = (event.x_root, event.y_root)
        self._pressed_identity = self._identity_at_pointer(event)
        self._drag_origin = ((event.x_root - self.win.winfo_x(),
                              event.y_root - self.win.winfo_y())
                             if self.editing and 8 <= event.y <= 48 and event.x < 240
                             else None)

    def _pointer_release(self, event):
        start = self._pointer_start
        identity = self._pressed_identity
        self._pointer_start = self._pressed_identity = self._drag_origin = None
        if start is None or math.hypot(event.x_root - start[0], event.y_root - start[1]) >= 5:
            return
        released = self._identity_at_pointer(event)
        if (identity is not None and released is not None
                and identity.get("card_id") == released.get("card_id")):
            self._dispatch_identity(identity)

    def _dispatch_identity(self, identity):
        if (getattr(self, "identity_corrections_enabled", False)
                and callable(self.on_correct_card)
                and self._selection_exists(identity)):
            self.on_correct_card(deepcopy(identity))

    def _rect(self, x1, y1, x2, y2, fill, outline):
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=1)

    def _text(self, x, y, text, color, size, anchor):
        self.canvas.create_text(x, y, text=text, fill=color,
                                font=("Segoe UI", size, "bold"), anchor=anchor)

    @staticmethod
    def _rank(rank):
        return RANK_SHORT.get(rank, str(rank))

    @staticmethod
    def _advice_text(presentation, running=True):
        state = presentation or {}
        blocked = state.get("status") in (
            "stopped", "error", "needs_calibration", "source_mismatch")
        if (not running or blocked
                or state.get("status") not in ("ready", "uncertain")):
            return "SIN CONSEJO"
        heroes = [p for p in state.get("players", []) if p.get("is_hero")]
        parts = []
        if (len(heroes) == 1 and not heroes[0].get("is_ambiguous")
                and len(heroes[0].get("cards") or []) >= 2):
            hero = heroes[0]
            advice = hero.get("advice")
            if (isinstance(advice, str) and advice.strip()
                    and (state.get("advice_available") is True
                         or isinstance(hero.get("hands"), list)
                         or hero.get("advice_reason"))):
                parts.append(advice.strip())
        insurance = state.get("insurance") or {}
        dealer_cards = (state.get("dealer") or {}).get("cards") or []
        upcard = dealer_cards[0] if dealer_cards else {}
        if (upcard.get("state") == "confirmed" and upcard.get("rank") in ("Ace", "A")
                and insurance.get("status") == "ready"):
            if type(insurance.get("take")) is bool:
                parts.append("SEGURO: SÍ" if insurance["take"] else "SEGURO: NO")
            elif insurance.get("text"):
                parts.append(str(insurance["text"]))
        return " · ".join(parts) if parts else "SIN CONSEJO"

    def _drag_move(self, event):
        if self.editing and self._drag_origin:
            if (self._pointer_start is None
                    or math.hypot(event.x_root - self._pointer_start[0],
                                  event.y_root - self._pointer_start[1]) < 5):
                return
            x = event.x_root-self._drag_origin[0]
            y = event.y_root-self._drag_origin[1]
            self.win.geometry(f"{WIDTH}x{HEIGHT}{x:+d}{y:+d}")

    def set_opacity(self, opacity):
        self.opacity = max(.25, min(1., float(opacity)))
        constants.UI["round_opacity"] = self.opacity
        self.win.attributes("-alpha", self.opacity)

    def set_editing(self, enabled):
        self.editing = bool(enabled)
        self._notice = overlay_visibility(self._last_state, self._running,
                                          self.editing, self.enabled)[1]
        self._draw()

    def _show_window(self):
        if not self.enabled or not self.win.winfo_exists() or self._capture_hidden or not self._active:
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
        if not self.win.winfo_exists():
            return
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
        self.enabled = False
        self._active = False
        if self.win.winfo_exists():
            try:
                self.win.after_cancel(self._topmost_job)
            except tk.TclError:
                pass
            self.remember_position()
            self.win.destroy()


# Import compatibility for older callers; both names now create the same widget.
RoundOverlay = MiniTableOverlay
