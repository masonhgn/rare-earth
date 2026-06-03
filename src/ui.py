
# ui primitives: 9-slice panel skin, tab strip, scroll list, button.
#
# the exchange panel composes these — no per-tab baked image. lets us
# render any panel at any size with scrollable content overlaid.
#
# design notes:
#   - 9-slice corners are fixed-pixel; edges and center are scaled (not
#     tiled) so the corner art stays crisp while the middle stretches.
#   - widgets are stateless drawables — Panel holds the state; each
#     widget exposes render() + handle_click() / handle_scroll() and
#     returns a small signal (e.g. clicked index) for the caller to act on.

import pygame as pg

from resources import load_image
from ui_theme import (
    COLOR_BUTTON_BG, COLOR_BUTTON_BG_DISABLED, COLOR_BUTTON_BORDER,
    COLOR_BUTTON_TEXT_DISABLED, COLOR_SCROLLBAR_THUMB, COLOR_SCROLLBAR_TRACK,
    COLOR_SLOT_BG, COLOR_SLOT_BORDER, COLOR_TAB_ACTIVE_BG,
    COLOR_TAB_ACTIVE_BORDER, COLOR_TAB_INACTIVE_BG, COLOR_TAB_INACTIVE_BORDER,
    COLOR_TAB_INACTIVE_TEXT, COLOR_TEXT_BODY, COLOR_TEXT_PRIMARY,
)


class NineSliceSkin:
    # loads a panel image and slices it into 9 sub-surfaces. render(rect)
    # paints the panel at any size by holding the 4 corner pieces fixed
    # and scaling the 4 edges + center.
    #
    # `scale` downsamples the source on load so the visible border art
    # appears thinner without changing the panel's outer dimensions. e.g.
    # scale=0.5 halves the border thickness and lets the middle stretch
    # over a wider area before the corners dominate.
    def __init__(self, path: str, corner_size: int, *, scale: float = 1.0) -> None:
        raw = load_image(path)
        if scale != 1.0:
            sw, sh = raw.get_size()
            raw = pg.transform.smoothscale(raw, (int(sw * scale), int(sh * scale)))
            corner_size = max(1, int(corner_size * scale))
        self.source = raw
        self.corner = corner_size
        sw, sh = self.source.get_size()
        c = corner_size
        # pre-extract sub-surfaces. subsurface() shares pixels with the
        # source so this is cheap and avoids per-frame allocations.
        self._tl = self.source.subsurface(pg.Rect(0, 0, c, c))
        self._tr = self.source.subsurface(pg.Rect(sw - c, 0, c, c))
        self._bl = self.source.subsurface(pg.Rect(0, sh - c, c, c))
        self._br = self.source.subsurface(pg.Rect(sw - c, sh - c, c, c))
        self._top = self.source.subsurface(pg.Rect(c, 0, sw - 2 * c, c))
        self._bot = self.source.subsurface(pg.Rect(c, sh - c, sw - 2 * c, c))
        self._left = self.source.subsurface(pg.Rect(0, c, c, sh - 2 * c))
        self._right = self.source.subsurface(pg.Rect(sw - c, c, c, sh - 2 * c))
        self._mid = self.source.subsurface(pg.Rect(c, c, sw - 2 * c, sh - 2 * c))

    def render(self, surface: pg.Surface, rect: pg.Rect) -> None:
        c = self.corner
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        mid_w = max(1, w - 2 * c)
        mid_h = max(1, h - 2 * c)

        # corners (fixed size)
        surface.blit(self._tl, (x, y))
        surface.blit(self._tr, (x + w - c, y))
        surface.blit(self._bl, (x, y + h - c))
        surface.blit(self._br, (x + w - c, y + h - c))

        # edges (scaled along one axis)
        surface.blit(pg.transform.scale(self._top, (mid_w, c)), (x + c, y))
        surface.blit(pg.transform.scale(self._bot, (mid_w, c)), (x + c, y + h - c))
        surface.blit(pg.transform.scale(self._left, (c, mid_h)), (x, y + c))
        surface.blit(pg.transform.scale(self._right, (c, mid_h)), (x + w - c, y + c))

        # center (scaled both axes)
        surface.blit(pg.transform.scale(self._mid, (mid_w, mid_h)), (x + c, y + c))


class TabStrip:
    # horizontal tab buttons. the caller reads .active to know which
    # tab content to render. handle_click switches and returns the new
    # active index (or None if the click missed).
    def __init__(self, rect, labels: list[str], font: pg.font.Font, *, active: int = 0) -> None:
        self.rect = pg.Rect(rect)
        self.labels = labels
        self.font = font
        self.active = active
        n = len(labels)
        tab_w = self.rect.width // n
        # last tab absorbs the rounding remainder so we don't get a gap
        self.tab_rects = []
        for i in range(n):
            x = self.rect.x + i * tab_w
            w = tab_w if i < n - 1 else self.rect.width - i * tab_w
            self.tab_rects.append(pg.Rect(x, self.rect.y, w, self.rect.height))

    def render(self, surface: pg.Surface) -> None:
        for i, (label, r) in enumerate(zip(self.labels, self.tab_rects)):
            is_active = i == self.active
            bg = COLOR_TAB_ACTIVE_BG if is_active else COLOR_TAB_INACTIVE_BG
            border = COLOR_TAB_ACTIVE_BORDER if is_active else COLOR_TAB_INACTIVE_BORDER
            pg.draw.rect(surface, bg, r, border_radius=6)
            pg.draw.rect(surface, border, r, width=2, border_radius=6)
            color = COLOR_TEXT_PRIMARY if is_active else COLOR_TAB_INACTIVE_TEXT
            text = self.font.render(label, True, color)
            surface.blit(text, text.get_rect(center=r.center))

    def handle_click(self, pos: tuple[int, int]) -> int | None:
        for i, r in enumerate(self.tab_rects):
            if r.collidepoint(pos):
                self.active = i
                return i
        return None


class Button:
    def __init__(self, rect, label: str, font: pg.font.Font, *, enabled: bool = True) -> None:
        self.rect = pg.Rect(rect)
        self.label = label
        self.font = font
        self.enabled = enabled

    def render(self, surface: pg.Surface) -> None:
        if self.enabled:
            bg, txt = COLOR_BUTTON_BG, COLOR_TEXT_BODY
        else:
            bg, txt = COLOR_BUTTON_BG_DISABLED, COLOR_BUTTON_TEXT_DISABLED
        pg.draw.rect(surface, bg, self.rect, border_radius=5)
        pg.draw.rect(surface, COLOR_BUTTON_BORDER, self.rect, width=2, border_radius=5)
        text = self.font.render(self.label, True, txt)
        surface.blit(text, text.get_rect(center=self.rect.center))

    def contains(self, pos: tuple[int, int]) -> bool:
        return self.enabled and self.rect.collidepoint(pos)


class SlotGrid:
    # cols x rows of clickable inventory slots. mirrors Inventory's
    # drag/drop semantics so the same held_item mechanic works for the
    # drop box, factory inputs (future migration), etc.
    #
    # callers pass the slot list to render() and handle_click() — slots
    # live on whichever owner (inventory, exchange entity, machine) so
    # the widget stays purely visual.
    def __init__(self, rect, cols: int, rows: int, slot_size: int, *,
                 slot_gap: int = 6, font: pg.font.Font | None = None) -> None:
        self.rect = pg.Rect(rect)
        self.cols = cols
        self.rows = rows
        self.slot_size = slot_size
        self.slot_gap = slot_gap
        self.pitch = slot_size + slot_gap
        self.font = font or pg.font.Font(None, 16)

    def total_size(self) -> tuple[int, int]:
        # cumulative width/height including all slots + gaps. handy when
        # the owner wants to center the grid inside a larger region.
        w = self.cols * self.slot_size + (self.cols - 1) * self.slot_gap
        h = self.rows * self.slot_size + (self.rows - 1) * self.slot_gap
        return (w, h)

    def _slot_topleft(self, idx: int) -> tuple[int, int]:
        col = idx % self.cols
        row = idx // self.cols
        return (
            self.rect.x + col * self.pitch,
            self.rect.y + row * self.pitch,
        )

    def slot_at_pixel(self, pos: tuple[int, int]) -> int | None:
        rel_x = pos[0] - self.rect.x
        rel_y = pos[1] - self.rect.y
        if rel_x < 0 or rel_y < 0:
            return None
        col = rel_x // self.pitch
        row = rel_y // self.pitch
        if col >= self.cols or row >= self.rows:
            return None
        # reject the gap between cells so a click in the seam doesn't
        # land on the wrong slot.
        if rel_x % self.pitch >= self.slot_size:
            return None
        if rel_y % self.pitch >= self.slot_size:
            return None
        return int(row * self.cols + col)

    def render(self, surface: pg.Surface, slots: list) -> None:
        # late imports keep ui.py free of game-domain deps at import time.
        from item import load_item, get_item_icon, format_quantity
        for i in range(self.cols * self.rows):
            x, y = self._slot_topleft(i)
            cell = pg.Rect(x, y, self.slot_size, self.slot_size)
            pg.draw.rect(surface, COLOR_SLOT_BG, cell, border_radius=4)
            pg.draw.rect(surface, COLOR_SLOT_BORDER, cell, width=2, border_radius=4)
            if i >= len(slots):
                continue
            slot = slots[i]
            if slot is None:
                continue
            proto = load_item(slot['item_id'])
            icon_size = self.slot_size - 6
            icon = get_item_icon(proto, size=icon_size)
            ix = x + (self.slot_size - icon.get_width()) // 2
            iy = y + (self.slot_size - icon.get_height()) // 2
            surface.blit(icon, (ix, iy))
            if slot['quantity'] > 1:
                label = self.font.render(
                    format_quantity(slot['quantity']), True, (255, 255, 255),
                )
                label_rect = label.get_rect(
                    bottomright=(x + self.slot_size - 3, y + self.slot_size - 3),
                )
                surface.blit(label, label_rect)

    def handle_click(self, idx: int | None, held: dict | None, slots: list) -> dict | None:
        # mirrors Inventory.handle_click — pick / place / merge / swap.
        # mutates `slots` in place; returns the new held_item.
        if idx is None or idx < 0 or idx >= len(slots):
            return held
        slot = slots[idx]
        if held is None:
            if slot is None:
                return None
            slots[idx] = None
            return slot
        if slot is None or slot['item_id'] == held['item_id']:
            if slot is None:
                slots[idx] = {'item_id': held['item_id'], 'quantity': held['quantity']}
            else:
                slot['quantity'] += held['quantity']
            return None
        # different item: swap
        new_held = slot
        slots[idx] = {'item_id': held['item_id'], 'quantity': held['quantity']}
        return new_held


class ScrollList:
    # viewport with mouse-wheel scrolling. owns the offset; callers
    # supply total_rows and a render_row(surface, index, row_rect)
    # callback so each list can lay out its own row contents.
    # constant width the scrollbar + its track padding consume on the
    # right edge of the viewport. exposed via `content_width` so row
    # layouts don't have to hardcode the same number themselves.
    SCROLLBAR_PAD = 22

    def __init__(self, rect, row_height: int) -> None:
        self.rect = pg.Rect(rect)
        self.row_height = row_height
        self.scroll_offset = 0

    @property
    def content_width(self) -> int:
        # row layouts should anchor right-edge widgets to this so they
        # don't disappear underneath the scrollbar.
        return self.rect.width - self.SCROLLBAR_PAD

    def _max_scroll(self, total_rows: int) -> int:
        content_h = total_rows * self.row_height
        return max(0, content_h - self.rect.height)

    def render(self, surface: pg.Surface, total_rows: int, render_row) -> None:
        # clamp first so a row removal doesn't leave us scrolled past the end
        self.scroll_offset = max(0, min(self.scroll_offset, self._max_scroll(total_rows)))
        prev_clip = surface.get_clip()
        surface.set_clip(self.rect)

        first = int(self.scroll_offset // self.row_height)
        for i in range(first, total_rows):
            y = self.rect.y + i * self.row_height - self.scroll_offset
            if y >= self.rect.bottom:
                break
            row_rect = pg.Rect(self.rect.x, y, self.rect.width, self.row_height)
            render_row(surface, i, row_rect)

        surface.set_clip(prev_clip)

        # scrollbar only visible when content overflows the viewport
        content_h = total_rows * self.row_height
        if content_h > self.rect.height:
            self._draw_scrollbar(surface, content_h)

    def _draw_scrollbar(self, surface: pg.Surface, content_h: int) -> None:
        bar_w = 8
        margin = 4
        track = pg.Rect(self.rect.right - bar_w - margin, self.rect.y + margin,
                        bar_w, self.rect.height - 2 * margin)
        pg.draw.rect(surface, COLOR_SCROLLBAR_TRACK, track, border_radius=3)
        # thumb height proportional to viewport / content
        thumb_h = max(20, int(track.height * self.rect.height / content_h))
        max_off = max(1, content_h - self.rect.height)
        thumb_y = track.y + int((self.scroll_offset / max_off) * (track.height - thumb_h))
        thumb = pg.Rect(track.x, thumb_y, bar_w, thumb_h)
        pg.draw.rect(surface, COLOR_SCROLLBAR_THUMB, thumb, border_radius=3)

    def handle_scroll(self, amount: int) -> None:
        # one wheel notch moves ~1 row worth of pixels. positive = up.
        self.scroll_offset -= amount * self.row_height

    def row_at_pixel(self, pos: tuple[int, int]) -> int | None:
        if not self.rect.collidepoint(pos):
            return None
        rel_y = pos[1] - self.rect.y + self.scroll_offset
        return int(rel_y // self.row_height)
