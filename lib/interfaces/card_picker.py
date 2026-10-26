"""Modal card-selection dialog: 4 suit rows x 13 rank columns, sized to fit."""

from copy import deepcopy
import tkinter as tk

from ..common import constants
from . import scaling

C = constants.COLORS

_SUIT_SYMBOLS = {"Spades": "♠", "Hearts": "♥", "Diamonds": "♦", "Clubs": "♣"}
_SUIT_CODES = {"Spades": "S", "Hearts": "H", "Diamonds": "D", "Clubs": "C"}
_SUIT_NAMES = {"S": "Spades", "H": "Hearts", "D": "Diamonds", "C": "Clubs",
               "♠": "Spades", "♥": "Hearts", "♦": "Diamonds", "♣": "Clubs"}
_RANK_LABELS = {"Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}
_RANK_NAMES = {"J": "Jack", "Q": "Queen", "K": "King", "A": "Ace"}


def _compact(name):
    """'King of Hearts' -> 'K♥' for the hand-selector labels."""
    rank, _, suit = str(name).partition(" of ")
    short = rank if rank.isdigit() else rank[:1]
    return short + _SUIT_SYMBOLS.get(suit, "")


class CardPicker(tk.Toplevel):
    """Calls on_select(card_name) — or on_select(None) for 'remove card'.

    With `split_info` ({"is_split": True, "hands": [[h0 names], [h1 names]]})
    a Hand 1 / Hand 2 selector appears and the callback becomes
    on_select(card_name, hand_idx) instead; without it the single-argument
    callback is preserved."""

    def __init__(self, parent, title, image_lookup, on_select, allow_remove=True,
                 split_info=None, card_identity=None, on_close=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=C["bg_primary"])
        self.resizable(False, False)
        if getattr(parent, "state", lambda: "normal")() not in ("iconic", "withdrawn"):
            self.transient(parent)
        self.grab_set()
        self._on_select = on_select
        self._hand_var = None
        self._card_identity = deepcopy(card_identity) if isinstance(card_identity, dict) else None
        self._on_close = on_close
        self._closed = False
        self._allow_remove = allow_remove
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Label(self, text=title, font=constants.FONT_SECTION,
                          bg=C["bg_primary"], fg=C["text_primary"])
        header.pack(pady=(14, 8))

        if self._card_identity is not None:
            self._build_identity_controls()
            self.update_idletasks()
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
            self.attributes("-topmost", True)
            self.lift()
            self.focus_force()
            return

        if split_info and split_info.get("is_split"):
            hands = (split_info.get("hands") or [[], []])[:2]
            while len(hands) < 2:
                hands.append([])
            # Default to the shorter hand — the one most likely owed a card.
            self._hand_var = tk.IntVar(
                value=0 if len(hands[0]) <= len(hands[1]) else 1)
            hand_row = tk.Frame(self, bg=C["bg_primary"])
            hand_row.pack(fill=tk.X, padx=14, pady=(0, 6))
            tk.Label(hand_row, text="Add to:", font=constants.FONT_BODY,
                     bg=C["bg_primary"], fg=C["text_secondary"]
                     ).pack(side=tk.LEFT, padx=(0, 8))
            for h, hand in enumerate(hands):
                cards_text = " ".join(_compact(n) for n in hand) or "empty"
                tk.Radiobutton(hand_row, text=f"Hand {h + 1} — {cards_text}",
                               variable=self._hand_var, value=h,
                               font=constants.FONT_BODY, bg=C["bg_primary"],
                               fg=C["text_primary"], selectcolor=C["bg_secondary"],
                               activebackground=C["bg_primary"],
                               activeforeground=C["text_primary"]
                               ).pack(side=tk.LEFT, padx=4)

        grid = tk.Frame(self, bg=C["bg_primary"])
        grid.pack(padx=14, pady=4)

        suit_symbols = _SUIT_SYMBOLS
        for row, suit in enumerate(constants.CARD_SUITS):
            color = C["danger"] if suit in ("Hearts", "Diamonds") else C["text_primary"]
            tk.Label(grid, text=f"{suit_symbols[suit]}", font=(constants.FONT_FAMILY, 14, "bold"),
                     bg=C["bg_primary"], fg=color, width=2).grid(row=row, column=0, padx=(0, 6))
            for col, rank in enumerate(constants.CARD_RANKS, start=1):
                name = f"{rank} of {suit}"
                photo = image_lookup(name, scaling.size(constants.PICKER_CARD_SIZE))
                btn = tk.Button(grid, image=photo, relief="flat", bd=1,
                                bg=C["bg_secondary"], activebackground=C["border"],
                                cursor="hand2", command=lambda n=name: self._choose(n))
                btn.image = photo
                btn.grid(row=row, column=col, padx=1, pady=2)

        footer = tk.Frame(self, bg=C["bg_primary"])
        footer.pack(fill=tk.X, padx=14, pady=(8, 14))
        tk.Button(footer, text="Cancel", command=self._cancel,
                  bg=C["text_secondary"], fg="white", relief="flat", cursor="hand2",
                  font=constants.FONT_BODY, padx=18, pady=7).pack(side=tk.LEFT)
        if allow_remove:
            tk.Button(footer, text="Remove card", command=lambda: self._choose(None),
                      bg=C["danger"], fg="white", relief="flat", cursor="hand2",
                      font=constants.FONT_BODY, padx=18, pady=7).pack(side=tk.RIGHT)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _choose(self, name):
        # Read the selector before destroy so the variable is still live.
        hand_idx = self._hand_var.get() if self._hand_var is not None else None
        self._close()
        if hand_idx is not None:
            self._on_select(name, hand_idx)
        else:
            self._on_select(name)

    def _build_identity_controls(self):
        card = self._card_identity
        current_rank = _RANK_NAMES.get(str(card.get("rank")), card.get("rank"))
        if current_rank not in constants.CARD_RANKS:
            current_rank = None
        current_suit_name = _SUIT_NAMES.get(card.get("suit"), card.get("suit"))
        if current_suit_name not in constants.CARD_SUITS:
            current_suit_name = None
        self._current_rank = current_rank
        self._needs_confirmation = card.get("state") != "confirmed" or bool(card.get("reason"))
        self._current_suit = _SUIT_CODES.get(current_suit_name) if current_suit_name else None
        self._chosen_rank = current_rank
        self._chosen_suit = None  # None means preserve the physical card's current suit.

        rank_label = _RANK_LABELS.get(current_rank, current_rank or "?")
        suit_label = (_SUIT_SYMBOLS[current_suit_name] if current_suit_name else "palo desconocido")
        group = card.get("group_id") or card.get("owner") or "grupo sin identificar"
        group = {"hero": "Yo", "dealer": "Crupier"}.get(group, group)
        hand = card.get("hand")
        hand_label = f"Mano {hand + 1}" if type(hand) is int and hand >= 0 else "mano sin etiqueta"
        details = (f"Carta actual: {rank_label} {suit_label}    "
                   f"Grupo: {group} / {hand_label}\n"
                   f"card_id: {card.get('card_id') or 'se asigna al guardar'}")
        tk.Label(self, text=details, justify=tk.LEFT, anchor="w",
                 font=constants.FONT_BODY, bg=C["bg_primary"],
                 fg=C["text_primary"]).pack(fill=tk.X, padx=14, pady=(0, 8))

        tk.Label(self, text="Rango", font=constants.FONT_BODY_BOLD,
                 bg=C["bg_primary"], fg=C["text_primary"]).pack(anchor="w", padx=14)
        ranks = tk.Frame(self, bg=C["bg_primary"])
        ranks.pack(padx=12, pady=(3, 8))
        self._rank_buttons = {}
        for index, rank in enumerate(constants.CARD_RANKS):
            label = _RANK_LABELS.get(rank, rank)
            btn = tk.Button(ranks, text=label, width=3, cursor="hand2",
                            command=lambda value=rank: self._set_rank(value))
            btn.grid(row=index // 7, column=index % 7, padx=2, pady=2)
            self._rank_buttons[rank] = btn
        self._refresh_rank_buttons()

        tk.Label(self, text="Palo (opcional)", font=constants.FONT_BODY_BOLD,
                 bg=C["bg_primary"], fg=C["text_primary"]).pack(anchor="w", padx=14)
        suits = tk.Frame(self, bg=C["bg_primary"])
        suits.pack(padx=12, pady=(3, 9))
        self._suit_buttons = {}
        for index, suit in enumerate(constants.CARD_SUITS):
            code = _SUIT_CODES[suit]
            btn = tk.Button(suits, text=f"{code}  {_SUIT_SYMBOLS[suit]}", width=8,
                            cursor="hand2", command=lambda value=code: self._set_suit(value))
            btn.grid(row=0, column=index, padx=3)
            self._suit_buttons[code] = btn
        self._keep_suit_button = tk.Button(
            suits, text="Mantener", command=lambda: self._set_suit(None),
            cursor="hand2", relief="sunken")
        self._keep_suit_button.grid(row=0, column=4, padx=3)

        footer = tk.Frame(self, bg=C["bg_primary"])
        footer.pack(fill=tk.X, padx=14, pady=(7, 14))
        self._save_button = tk.Button(
            footer, text="Guardar Corrección", command=self._save_identity_correction,
            bg=C["success"], fg="white", relief="flat", cursor="hand2",
            font=constants.FONT_BODY_BOLD, padx=10, pady=7)
        self._save_button.pack(side=tk.LEFT)
        if getattr(self, "_allow_remove", True):
            tk.Button(footer, text="Descartar Falsa Detección",
                      command=self._discard_identity_card, bg=C["danger"], fg="white",
                      relief="flat", cursor="hand2", font=constants.FONT_BODY,
                      padx=8, pady=7).pack(side=tk.LEFT, padx=6)
        tk.Button(footer, text="Cancelar", command=self._cancel,
                  bg=C["text_secondary"], fg="white", relief="flat",
                  cursor="hand2", font=constants.FONT_BODY,
                  padx=10, pady=7).pack(side=tk.RIGHT)
        self._refresh_save_button()

    def _set_rank(self, rank):
        self._chosen_rank = rank
        self._refresh_rank_buttons()
        self._refresh_save_button()

    def _set_suit(self, suit):
        self._chosen_suit = suit
        for code, button in self._suit_buttons.items():
            button.configure(relief="sunken" if code == suit else "raised")
        self._keep_suit_button.configure(relief="sunken" if suit is None else "raised")
        self._refresh_save_button()

    def _refresh_rank_buttons(self):
        for rank, button in self._rank_buttons.items():
            button.configure(relief="sunken" if rank == self._chosen_rank else "raised")

    def _refresh_save_button(self):
        rank_changed = self._chosen_rank != self._current_rank
        suit_changed = (self._chosen_suit is not None
                        and self._chosen_suit != self._current_suit)
        self._save_button.configure(
            state=(tk.NORMAL if self._chosen_rank is not None
                   and (rank_changed or suit_changed or getattr(self, "_needs_confirmation", False)) else tk.DISABLED))

    def _save_identity_correction(self):
        if self._chosen_rank is None:
            return
        if (self._chosen_rank == self._current_rank
                and (self._chosen_suit is None or self._chosen_suit == self._current_suit)
                and not getattr(self, "_needs_confirmation", False)):
            return
        identity = deepcopy(self._card_identity)
        rank, suit = self._chosen_rank, self._chosen_suit
        self._close()
        self._on_select("set_rank", rank, suit, identity)

    def _discard_identity_card(self):
        identity = deepcopy(self._card_identity)
        self._close()
        self._on_select("not_a_card", None, None, identity)

    def _cancel(self):
        self._close()

    def _close(self):
        if self._closed:
            return
        self._closed = True
        self.destroy()
        if callable(self._on_close):
            self._on_close()
