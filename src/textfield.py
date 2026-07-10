
# a single-line editable text field with the affordances people expect from
# any OS text box: a real caret you can place with the mouse or arrow keys,
# shift/drag selection, key-repeat on held keys, and OS clipboard copy / cut /
# paste / select-all. it exists because pygame gives you none of this — the
# old hand-rolled fields only appended event.unicode and could edit the end of
# the string. reusable: the title-screen server field and the dev console both
# drive one of these.
#
# the OWNER is responsible for focus. on a mouse click it calls focus() when
# the click lands in the field and blur() otherwise, then forwards every event
# to handle_event(). handle_event() ignores everything while unfocused, and
# returns False for keys it doesn't consume (Enter, Esc, Tab) so the owner can
# act on them (submit, cancel, autocomplete). focus() scopes key-repeat to the
# field so held keys don't machine-gun gameplay KEYDOWN handlers.

import pygame as pg

_PAD_X = 8            # inner horizontal padding, px
_BLINK_PERIOD = 60   # frames for one caret blink cycle (~1s at 60fps)
_BLINK_ON = 30       # caret is drawn while the blink counter is under this

# default palette — matches the dark server field on the title screen. owners
# can override via the constructor to fit a different panel.
_C_TEXT = (235, 235, 235)
_C_FIELD = (18, 20, 24)
_C_BORDER = (90, 100, 115)
_C_ACTIVE = (235, 200, 120)
_C_SEL = (70, 92, 140)


_scrap_ready = False


def _scrap_init() -> bool:
    # pygame.scrap needs a display before it will init; do it lazily so the
    # widget stays self-contained (the dev console lives inside a running game,
    # the title field in the launcher — both already have a window by now).
    global _scrap_ready
    if _scrap_ready:
        return True
    try:
        pg.scrap.init()
        _scrap_ready = True
    except Exception:
        pass
    return _scrap_ready


def _clip_copy(text: str) -> None:
    if not text or not _scrap_init():
        return
    try:
        pg.scrap.put(pg.SCRAP_TEXT, text.encode('utf-8'))
    except Exception:
        pass


def _clip_paste() -> str:
    if not _scrap_init():
        return ''
    try:
        raw = pg.scrap.get(pg.SCRAP_TEXT)
    except Exception:
        return ''
    if not raw:
        return ''
    try:
        s = raw.decode('utf-8')
    except UnicodeDecodeError:
        s = raw.decode('utf-8', 'ignore')
    # windows hands back a null-terminated buffer; a single-line field also
    # wants only the first line of any multi-line paste.
    s = s.replace('\x00', '')
    return s.splitlines()[0] if s else ''


class TextField:
    def __init__(self, rect, font, *, text='', max_len=None, chrome=True,
                 color_text=_C_TEXT, color_field=_C_FIELD,
                 color_border=_C_BORDER, color_active=_C_ACTIVE,
                 color_sel=_C_SEL):
        self.rect = pg.Rect(rect)
        self.font = font
        self.text = text
        self.max_len = max_len
        self.caret = len(text)      # insertion index into self.text
        self.sel_anchor = None      # other end of the selection, or None
        self.focused = False
        self.scroll = 0             # px the text is shifted left when it overflows
        self._blink = 0
        self._dragging = False
        # chrome=True draws the boxed field (title screen). chrome=False renders
        # just text + caret + selection with no box or padding, for hosts that
        # own the surrounding layout (the dev console's inline prompt line).
        self.chrome = chrome
        self._pad = _PAD_X if chrome else 0
        self.color_text = color_text
        self.color_field = color_field
        self.color_border = color_border
        self.color_active = color_active
        self.color_sel = color_sel

    # --- focus (owner-driven) ---------------------------------------------

    def focus(self) -> None:
        if self.focused:
            return
        self.focused = True
        self._reset_blink()
        try:
            pg.key.set_repeat(300, 30)     # scoped on while editing
            pg.key.start_text_input()
            pg.key.set_text_input_rect(self.rect)
        except Exception:
            pass

    def blur(self) -> None:
        if not self.focused:
            return
        self.focused = False
        self.sel_anchor = None
        self._dragging = False
        try:
            pg.key.set_repeat()            # disable so gameplay keys don't repeat
            pg.key.stop_text_input()
        except Exception:
            pass

    # --- text access ------------------------------------------------------

    def set_text(self, text: str) -> None:
        self.text = text
        self.caret = len(text)
        self.sel_anchor = None
        self.scroll = 0

    def clear(self) -> None:
        self.set_text('')

    # --- event handling ---------------------------------------------------

    def handle_event(self, event) -> bool:
        # returns True if it consumed the event. only active while focused; the
        # owner decides focus on mouse-down before forwarding events here.
        if not self.focused:
            return False
        if event.type == pg.TEXTINPUT:
            self._insert(event.text)
            return True
        if event.type == pg.KEYDOWN:
            return self._on_keydown(event)
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.caret = self._x_to_index(event.pos[0])
                self.sel_anchor = None
                self._dragging = True
                self._reset_blink()
                return True
            return False
        if event.type == pg.MOUSEBUTTONUP and event.button == 1:
            self._dragging = False
            return False
        if event.type == pg.MOUSEMOTION and self._dragging:
            if self.sel_anchor is None:
                self.sel_anchor = self.caret
            self.caret = self._x_to_index(event.pos[0])
            self._reset_blink()
            return True
        return False

    def _on_keydown(self, event) -> bool:
        key = event.key
        ctrl = event.mod & pg.KMOD_CTRL
        shift = event.mod & pg.KMOD_SHIFT

        if ctrl and key == pg.K_a:
            self.sel_anchor = 0
            self.caret = len(self.text)
            self._reset_blink()
            return True
        if ctrl and key in (pg.K_c, pg.K_x):
            if self._has_sel():
                lo, hi = self._sel_range()
                _clip_copy(self.text[lo:hi])
                if key == pg.K_x:
                    self._delete_selection()
            return True
        if ctrl and key == pg.K_v:
            self._insert(_clip_paste())
            return True

        if key == pg.K_LEFT:
            if self._has_sel() and not shift:
                self._move(self._sel_range()[0], False)
            else:
                self._move(self.caret - 1, shift)
            return True
        if key == pg.K_RIGHT:
            if self._has_sel() and not shift:
                self._move(self._sel_range()[1], False)
            else:
                self._move(self.caret + 1, shift)
            return True
        if key == pg.K_HOME:
            self._move(0, shift)
            return True
        if key == pg.K_END:
            self._move(len(self.text), shift)
            return True

        if key == pg.K_BACKSPACE:
            if not self._delete_selection() and self.caret > 0:
                self.text = self.text[:self.caret - 1] + self.text[self.caret:]
                self.caret -= 1
            self._reset_blink()
            return True
        if key == pg.K_DELETE:
            if not self._delete_selection() and self.caret < len(self.text):
                self.text = self.text[:self.caret] + self.text[self.caret + 1:]
            self._reset_blink()
            return True

        # Enter / Esc / Tab and friends fall through so the owner can act on them.
        return False

    # --- editing primitives ----------------------------------------------

    def _has_sel(self) -> bool:
        return self.sel_anchor is not None and self.sel_anchor != self.caret

    def _sel_range(self) -> tuple[int, int]:
        return (min(self.sel_anchor, self.caret), max(self.sel_anchor, self.caret))

    def _delete_selection(self) -> bool:
        if not self._has_sel():
            self.sel_anchor = None
            return False
        lo, hi = self._sel_range()
        self.text = self.text[:lo] + self.text[hi:]
        self.caret = lo
        self.sel_anchor = None
        return True

    def _insert(self, s: str) -> None:
        s = ''.join(c for c in s if c.isprintable())
        if not s:
            return
        self._delete_selection()
        if self.max_len is not None:
            room = self.max_len - len(self.text)
            if room <= 0:
                return
            s = s[:room]
        self.text = self.text[:self.caret] + s + self.text[self.caret:]
        self.caret += len(s)
        self.sel_anchor = None
        self._reset_blink()

    def _move(self, new_caret: int, extend: bool) -> None:
        new_caret = max(0, min(len(self.text), new_caret))
        if extend:
            if self.sel_anchor is None:
                self.sel_anchor = self.caret
        else:
            self.sel_anchor = None
        self.caret = new_caret
        self._reset_blink()

    def _reset_blink(self) -> None:
        self._blink = 0

    # --- geometry ---------------------------------------------------------

    def _x_to_index(self, screen_x: int) -> int:
        # nearest caret gap to an absolute screen x. O(n^2) in width lookups but
        # n is a short address / command line, so it's cheap.
        target = screen_x - (self.rect.x + self._pad) + self.scroll
        if target <= 0:
            return 0
        for i in range(1, len(self.text) + 1):
            w = self.font.size(self.text[:i])[0]
            if w >= target:
                prev = self.font.size(self.text[:i - 1])[0]
                return i if (target - prev) > (w - target) else i - 1
        return len(self.text)

    def _update_scroll(self) -> None:
        inner_w = self.rect.width - 2 * self._pad
        caret_x = self.font.size(self.text[:self.caret])[0]
        if caret_x - self.scroll > inner_w:
            self.scroll = caret_x - inner_w
        if caret_x - self.scroll < 0:
            self.scroll = caret_x
        max_scroll = max(0, self.font.size(self.text)[0] - inner_w)
        self.scroll = max(0, min(self.scroll, max_scroll))

    # --- drawing ----------------------------------------------------------

    def draw(self, surface) -> None:
        self._update_scroll()
        if self.chrome:
            pg.draw.rect(surface, self.color_field, self.rect, border_radius=4)
            border = self.color_active if self.focused else self.color_border
            pg.draw.rect(surface, border, self.rect, width=2, border_radius=4)

        prev_clip = surface.get_clip()
        surface.set_clip(self.rect.inflate(-2 * self._pad, 0))
        base_x = self.rect.x + self._pad - self.scroll
        cy = self.rect.centery

        if self.focused and self._has_sel():
            lo, hi = self._sel_range()
            x0 = base_x + self.font.size(self.text[:lo])[0]
            x1 = base_x + self.font.size(self.text[:hi])[0]
            pg.draw.rect(surface, self.color_sel,
                         pg.Rect(x0, self.rect.y + 4, x1 - x0, self.rect.height - 8))

        txt = self.font.render(self.text, True, self.color_text)
        surface.blit(txt, txt.get_rect(midleft=(base_x, cy)))

        if self.focused and self._blink < _BLINK_ON:
            cx = base_x + self.font.size(self.text[:self.caret])[0]
            pg.draw.line(surface, self.color_text,
                         (cx, self.rect.y + 5), (cx, self.rect.bottom - 5), 1)

        surface.set_clip(prev_clip)
        self._blink = (self._blink + 1) % _BLINK_PERIOD
