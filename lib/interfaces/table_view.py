"""Draw TablePresentation data without engine or historical seat indices."""
from copy import deepcopy
import math
import tkinter as tk

from PIL import Image, ImageTk, ImageDraw, ImageFont

from . import scaling
from ..common import constants
from ..logic import cards as cardlib

C = constants.COLORS


def seat_positions(w, h, n, card_w, card_h):
    """Card-top anchors on the original lower arc, centered for one player."""
    if n <= 0:
        return []
    s = card_h / float(constants.CARD_RENDER_SIZE[1])
    cx = w / 2.0
    anchor_y = 110.0 * s
    span = math.radians(110.0)
    label_stack = (168.0 + 12.0) * s
    fit_h = h - anchor_y - card_h - label_stack
    fit_w = (w / 2.0 - 100.0 * s) / math.sin(span / 2)
    radius = max(min(fit_h, fit_w), 150.0 * s)
    step = span / (n - 1) if n > 1 else 0.0
    out = []
    for i in range(n):
        theta = 0.0 if n == 1 else -span / 2 + i * step
        x = cx + radius * math.sin(theta)
        y = anchor_y + radius * math.cos(theta)
        y = min(y, h - card_h - label_stack)
        out.append((x, y))
    return out


class TableView:
    def __init__(self, parent, on_card_click=None, on_dealer_click=None,
                 on_split_click=None, on_seat_name_click=None,
                 on_dealer_extra_click=None, on_advice_click=None,
                 on_card_identity_click=None):
        # Only the identity callback is bound. The other arguments remain
        # accepted for source compatibility but are never used for corrections.
        self.on_card_identity_click = on_card_identity_click
        self.on_card_click = on_card_click
        self.on_dealer_click = on_dealer_click
        self.on_split_click = on_split_click
        self.on_seat_name_click = on_seat_name_click
        self.on_dealer_extra_click = on_dealer_extra_click
        self.on_advice_click = on_advice_click
        self.canvas = tk.Canvas(parent, bg=C["bg_canvas"], highlightthickness=0)
        self._image_cache = {}
        self._master_cache = {}
        self._preload_gen = 0
        self._last_snapshot = None  # compatibility name: a presentation
        self._resize_job = None
        self._preview_item = None
        self._preview_photo = None
        self._preview_close_btn = None
        self._mode = "individual"
        self.dealer = self._new_hand("Crupier")
        self.dealer_title = self.dealer["name"]
        self.status_label = self._label("")
        self.mode_label = self._label("")
        self.seats = []
        self._make_seats()
        self.canvas.bind("<Configure>", self._on_resize)
        scaling.on_change(self._rescale)

    def _label(self, text, font=None):
        return tk.Label(self.canvas, text=text, font=font or constants.FONT_BODY_BOLD,
                        bg=C["bg_canvas"], fg=C["text_on_felt"], cursor="")

    def _new_hand(self, title):
        return {"id": "hero" if title == "Yo" else "dealer", "cards": [],
                "card_keys": [], "rendered": [], "name": self._label(title, constants.FONT_SECTION),
                "empty": self._label("[sin cartas]"), "advice": self._label(""),
                "split_notice": self._label(""), "hand_titles": [],
                "hand_of": [], "split_valid": False, "data": {},
                "is_hero": title == "Yo", "is_provisional": False,
                "is_ambiguous": False}

    def _make_seats(self, players=None):
        """Keep one widget set per observed logical group; never pad seats."""
        players = players or []
        if self._mode == "individual":
            players = [{"id": "hero", "label": "Yo", "is_hero": True}]
        else:
            players = list(players[:7])
        existing = {seat["id"]: seat for seat in self.seats}
        active = []
        for player in players:
            group_id = player.get("id")
            if not isinstance(group_id, (str, int)):
                continue
            hand = existing.pop(group_id, None)
            title = "Yo" if player.get("is_hero") else str(player.get("label") or group_id)
            if hand is None:
                hand = self._new_hand(title)
                hand["id"] = group_id
            elif hand["name"].cget("text") != title:
                hand["name"].config(text=title)
            hand["is_hero"] = bool(player.get("is_hero"))
            hand["is_provisional"] = bool(player.get("is_provisional"))
            hand["is_ambiguous"] = bool(player.get("is_ambiguous"))
            if hand["is_hero"]:
                hand["name"].config(fg=C.get("success", "#22C55E"))
            elif hand["is_provisional"] or hand["is_ambiguous"]:
                hand["name"].config(fg=C.get("warning", "#F59E0B"))
            else:
                hand["name"].config(fg=C["text_on_felt"])
            if hand["is_provisional"]:
                title = f"{title} · {group_id} · PROVISIONAL"
            if hand["is_ambiguous"]:
                title = f"{title} · AMBIGUO / ?"
            hand["name"].config(text=title)
            active.append(hand)
        for hand in existing.values():
            for widget in self._hand_widgets(hand):
                widget.destroy()
        self.seats[:] = active

    @staticmethod
    def _hand_widgets(hand):
        return (hand["cards"] + hand["hand_titles"]
                + [hand[key] for key in ("name", "empty", "advice", "split_notice")])

    # ---------------------------------------------------------------- images

    def card_image(self, card_name, size=None):
        if size is None:
            size = scaling.size(constants.CARD_RENDER_SIZE)
        key = (card_name, size)
        cached = self._image_cache.get(key)
        if cached is not None:
            return cached
        photo = ImageTk.PhotoImage(self._master(card_name).resize(size, Image.LANCZOS))
        self._image_cache[key] = photo
        return photo

    def _master(self, card_name):
        """Decoded PIL masters survive DPI changes, including neutral ?."""
        master = self._master_cache.get(card_name)
        if master is None:
            rank = cardlib.rank_of(str(card_name))
            if card_name == "unknown" or (card_name == rank and rank in constants.CARD_RANKS):
                master = Image.new("RGB", (300, 436), "white")
                draw = ImageDraw.Draw(master)
                draw.rounded_rectangle((1, 1, 298, 434), radius=18, outline="#333333", width=4)
                label = ("?" if card_name == "unknown" else
                         {"Ace": "A", "Jack": "J", "Queen": "Q", "King": "K"}.get(rank, rank))
                try:
                    font = ImageFont.truetype("arial.ttf", 150)
                except OSError:
                    font = ImageFont.load_default(size=150)
                draw.text((150, 210), label, font=font, fill="#202020", anchor="mm")
            else:
                path = (constants.card_image_path(card_name) if card_name not in (None, "back")
                        else constants.CARD_BACK_IMAGE_PATH)
                if not path.exists():
                    path = constants.CARD_BACK_IMAGE_PATH
                max_size = (round(constants.DEALER_CARD_RENDER_SIZE[0] * scaling.MAX_SCALE),
                            round(constants.DEALER_CARD_RENDER_SIZE[1] * scaling.MAX_SCALE))
                master = Image.open(path).resize(max_size, Image.LANCZOS)
            self._master_cache[card_name] = master
        return master

    def preload_images(self, names=None, _index=0, _gen=None):
        """Load card images a few per idle tick so the UI never freezes."""
        if names is None:
            names = cardlib.all_card_names()
            self._preload_gen += 1
            _gen = self._preload_gen
        elif _gen != self._preload_gen:
            return
        end = min(_index + 4, len(names))
        for name in names[_index:end]:
            self.card_image(name)
            self.card_image(name, scaling.size(constants.PICKER_CARD_SIZE))
        if end < len(names):
            self.canvas.after(15, lambda: self.preload_images(names, end, _gen))

    @staticmethod
    def _visual_name(card):
        # The presenter withholds uncertain ranks; defensively keep candidate
        # ranks neutral as well. This name is visual only, never card identity.
        rank = card.get("rank")
        if card.get("state") != "confirmed" or card.get("reason") or not rank:
            return "unknown"
        rank = {"A": "Ace", "J": "Jack", "Q": "Queen", "K": "King"}.get(rank, rank)
        if rank not in constants.CARD_RANKS:
            return "unknown"
        suit = card.get("suit")
        suit = {"S": "Spades", "H": "Hearts", "D": "Diamonds", "C": "Clubs"}.get(suit, suit)
        if isinstance(suit, str) and suit.title() in constants.CARD_SUITS:
            return f"{rank} of {suit.title()}"
        return rank

    def _update_hand(self, hand, cards, presentation, *, dealer=False):
        size = scaling.size(constants.DEALER_CARD_RENDER_SIZE if dealer else constants.CARD_RENDER_SIZE)
        old = {key: (widget, rendered) for key, widget, rendered in
               zip(hand["card_keys"], hand["cards"], hand["rendered"])}
        keys = [(card.get("card_id") if isinstance(card.get("card_id"), str)
                 and card.get("card_id").strip() else ("visual", i))
                for i, card in enumerate(cards)]
        widgets, rendered_names = [], []
        original = list(zip(hand["card_keys"], hand["cards"], hand["rendered"]))
        for i, card in enumerate(cards):
            key = keys[i]
            if key in old:
                widget, rendered = old.pop(key)
            else:
                visual_name = self._visual_name(card)
                previous = original[i] if i < len(original) else None
                if previous is not None and previous[0] in old and previous[2] == visual_name:
                    old_key, widget, rendered = previous
                    old.pop(old_key)
                else:
                    widget, rendered = tk.Label(self.canvas, bg=C["bg_canvas"], bd=0, cursor=""), None
            widget.card_data = deepcopy(card)
            identity_fields = (
                "card_id", "legacy_ref", "rank", "suit", "state", "reason",
                "hand", "owner", "group_id", "round_id", "shoe_id",
                "profile_name", "mode", "resolution", "source_id", "source_ref",
            )
            widget.card_identity = {key: deepcopy(card[key])
                                    for key in identity_fields if key in card}
            for key in ("round_id", "shoe_id", "profile_name", "mode", "resolution",
                        "source_id", "source_ref"):
                if key not in widget.card_identity and key in presentation:
                    widget.card_identity[key] = deepcopy(presentation[key])
            can_correct = (callable(self.on_card_identity_click)
                           and isinstance(widget.card_identity.get("card_id"), str)
                           and bool(widget.card_identity["card_id"].strip()))
            widget.configure(cursor="hand2" if can_correct else "")
            if can_correct and not getattr(widget, "_identity_click_bound", False):
                widget.bind("<Button-1>",
                            lambda _event, target=widget: self._dispatch_card_identity(target))
                widget._identity_click_bound = True
            name = self._visual_name(card)
            if rendered != name:
                photo = self.card_image(name, size)
                widget.config(image=photo)
                widget.image = photo
            widgets.append(widget)
            rendered_names.append(name)
        for widget, _rendered in old.values():
            widget.destroy()
        hand["cards"], hand["card_keys"], hand["rendered"] = widgets, keys, rendered_names

    def _dispatch_card_identity(self, widget):
        """Send a detached physical identity; never a seat/card index."""
        identity = getattr(widget, "card_identity", None)
        if (callable(self.on_card_identity_click) and isinstance(identity, dict)
                and identity.get("card_id") is not None):
            self.on_card_identity_click(deepcopy(identity))

    # -------------------------------------------------------------- geometry

    def _on_resize(self, _event):
        if self._resize_job is not None:
            self.canvas.after_cancel(self._resize_job)
        self._resize_job = self.canvas.after(120, self._relayout)

    def _place_hand(self, hand, x, top, *, dealer=False):
        cw, ch = scaling.size(constants.DEALER_CARD_RENDER_SIZE if dealer else constants.CARD_RENDER_SIZE)
        hand["name"].place(x=x, y=top - scaling.px(30), anchor="n")
        count = len(hand["cards"])
        hand["empty"].place_forget()
        for title in hand["hand_titles"]:
            title.place_forget()
        if not count:
            hand["empty"].place(x=x, y=top, anchor="n")
        split = hand["split_valid"]
        if split:
            for i, title in enumerate(hand["hand_titles"]):
                title.place(x=x + (i * 2 - 1) * (cw + scaling.px(40)), y=top, anchor="n")
        span = hand.get("max_span", self.canvas.winfo_width() - scaling.px(40))
        step = min(scaling.px(34), max(0, (span - cw) / max(count - 1, 1)))
        depths = [0, 0]
        for i, widget in enumerate(hand["cards"]):
            if split:
                h = hand["hand_of"][i]
                n = hand["hand_of"].count(h)
                cx = x + (h * 2 - 1) * (cw + scaling.px(40))
                left = cx - (cw + (n - 1) * step) / 2
                widget.place(x=left + depths[h] * step, y=top + scaling.px(26), anchor="nw")
                depths[h] += 1
            else:
                left = x - (cw + (count - 1) * step) / 2
                widget.place(x=left + i * step, y=top, anchor="nw")
            widget.lift()
        bottom = top + (ch if count else scaling.px(24)) + (scaling.px(26) if split else 0)
        hand["split_notice"].place(x=x, y=bottom + scaling.px(8), anchor="n")
        hand["advice"].place(x=x, y=bottom + scaling.px(32), anchor="n")

    def _relayout(self):
        self._resize_job = None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 50 or h < 50:
            return
        if self._preview_item is not None:
            self.canvas.coords(self._preview_item, w / 2, h / 2)
            return
        self._place_hand(self.dealer, w / 2, scaling.px(52), dealer=True)
        if self.seats:
            cw, ch = scaling.size(constants.CARD_RENDER_SIZE)
            min_top = scaling.px(52) + scaling.size(constants.DEALER_CARD_RENDER_SIZE)[1] + scaling.px(80)
            count = len(self.seats)
            if count <= 3:
                positions = seat_positions(w, h, count, cw, ch)
                ordered_x = [point[0] for point in positions]
                for index, (seat, (x, top)) in enumerate(zip(self.seats, positions)):
                    neighbors = ([ordered_x[index] - ordered_x[index - 1]] if index else [])
                    if index + 1 < count:
                        neighbors.append(ordered_x[index + 1] - ordered_x[index])
                    seat["max_span"] = (min(neighbors) * .9 if neighbors else w - scaling.px(40))
                    self._place_hand(seat, x, max(top, min_top))
            else:
                columns = math.ceil(count / 2)
                cell_w = w / columns
                row_gap = max(scaling.px(170), (h - min_top - scaling.px(100)) / 2)
                row_tops = (min_top, min(h - ch - scaling.px(90), min_top + row_gap))
                row_counts = (columns, count - columns)
                for index, seat in enumerate(self.seats):
                    row = 0 if index < columns else 1
                    column = index if row == 0 else index - columns
                    x = cell_w * (column + .5) if row_counts[row] == columns else (
                        w * (column + .5) / row_counts[row])
                    seat["max_span"] = max(scaling.px(48), cell_w - scaling.px(20))
                    self._place_hand(seat, x, row_tops[row])
        self.status_label.config(wraplength=max(1, w - scaling.px(24)))
        self.status_label.place(x=w / 2, y=h - scaling.px(16), anchor="s")
        self.mode_label.place(x=w / 2, y=scaling.px(230), anchor="n")

    def _rescale(self):
        self._image_cache.clear()
        for hand in [self.dealer] + self.seats:
            hand["rendered"] = [None] * len(hand["rendered"])
        if self._last_snapshot is not None:
            self.update(self._last_snapshot)
        self._relayout()
        self.preload_images()

    # ---------------------------------------------------------------- update

    def update(self, presentation):
        if presentation is None:
            return
        self.remember_snapshot(presentation)
        if self._preview_item is not None:
            return
        self._mode = presentation.get("mode")
        players = presentation.get("players", []) if self._mode == "multiplayer" else []
        self._make_seats(players)
        blocked = presentation.get("status") in ("stopped", "error", "needs_calibration", "source_mismatch")
        dealer_cards = [] if blocked else presentation.get("dealer", {}).get("cards", [])
        self._update_hand(self.dealer, dealer_cards, presentation, dealer=True)
        insurance = presentation.get("insurance") or {}
        dealer_advice = (insurance.get("text", "")
                         if not blocked and insurance.get("status") == "ready"
                         else "SEGURO: SIN CONSEJO"
                         if not blocked and insurance.get("status") == "no_advice"
                         else "")
        if dealer_advice and presentation.get("count_has_pending_uncertainty"):
            dealer_advice += " · TC INCIERTO"
        self.dealer["advice"].config(text=dealer_advice)
        message = presentation.get("message", "")
        if presentation.get("group_warning"):
            message = f"{message} · {presentation['group_warning']}"
        self.status_label.config(text=message,
                                 fg=C["warning"] if presentation.get("status") in
                                 ("uncertain", "error", "needs_calibration", "source_mismatch") else C["text_on_felt"])
        self.mode_label.config(text="")
        player_by_id = {p.get("id"): p for p in players}
        for seat in self.seats:
            player = (next((p for p in presentation.get("players", [])
                            if p.get("id") == "hero"), {})
                      if self._mode == "individual" else player_by_id.get(seat["id"], {}))
            seat["data"] = deepcopy(player)
            cards = [] if blocked else player.get("cards", [])
            self._update_hand(seat, cards, presentation)
            hand_of = [card.get("hand") for card in cards]
            split = bool(player.get("has_split"))
            seat["hand_of"] = hand_of
            seat["split_valid"] = (split and bool(cards)
                                   and all(type(tag) is int and tag in (0, 1) for tag in hand_of)
                                   and set(hand_of) == {0, 1})
            if seat["split_valid"] and not seat["hand_titles"]:
                seat["hand_titles"] = [self._label("Mano 1"), self._label("Mano 2")]
            seat["split_notice"].config(text="Split incierto: etiquetas de mano incompletas"
                                        if split and cards and not seat["split_valid"] else "")
            advice = player.get("advice")
            advice_text = (advice if not blocked and player.get("is_hero")
                           and (presentation.get("advice_available") is True
                                or isinstance(player.get("hands"), list)
                                or player.get("advice_reason"))
                           and isinstance(advice, str) else "")
            if advice_text and presentation.get("count_has_pending_uncertainty"):
                advice_text += "\nTC INCIERTO"
            seat["advice"].config(text=advice_text)
        self._relayout()

    # ---------------------------------------------------------------- preview

    def show_preview(self, pil_image, on_close, actions=None):
        self.clear_preview()
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        img = pil_image.copy()
        img.thumbnail((w - 30, h - 60))
        self._preview_photo = ImageTk.PhotoImage(img)
        for hand in [self.dealer] + self.seats:
            for widget in self._hand_widgets(hand):
                widget.place_forget()
        self.status_label.place_forget()
        self.mode_label.place_forget()
        self._preview_item = self.canvas.create_image(w / 2, h / 2, image=self._preview_photo)
        self._preview_close_btn = tk.Frame(self.canvas, bg=C["bg_secondary"])
        for text, command in (actions or []):
            tk.Button(self._preview_close_btn, text=text, command=command,
                      bg=C["bg_secondary"], fg=C["text_primary"], relief="flat",
                      font=constants.FONT_SMALL, padx=6, pady=6).pack(side=tk.LEFT)
        tk.Button(self._preview_close_btn, text="Close preview", command=lambda: (self.clear_preview(), on_close()),
                  bg=C["danger"], fg="white", relief="flat", font=constants.FONT_SMALL,
                  padx=6, pady=6).pack(side=tk.LEFT)
        self._preview_close_btn.place(relx=.5, rely=1.0, y=-6, anchor="s")

    def clear_preview(self):
        if self._preview_item is not None:
            self.canvas.delete(self._preview_item)
            self._preview_item = None
            self._preview_photo = None
        if self._preview_close_btn is not None:
            self._preview_close_btn.destroy()
            self._preview_close_btn = None
        if self._last_snapshot is not None:
            self.update(self._last_snapshot)
        else:
            self._relayout()

    def remember_snapshot(self, presentation):
        """Keep detached visual data for preview/resize, never a raw snapshot."""
        if presentation != self._last_snapshot:
            self._last_snapshot = deepcopy(presentation)
