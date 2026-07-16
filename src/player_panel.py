
# character/stats sheet. opened from the 'player' hud tab — takes the inventory's
# bottom-left slot (the two are mutually exclusive; opening one closes the other),
# NOT a centered modal like settings/exchange.
#
# display-only: shows the four skill tracks (level + total xp) and the derived
# stats they drive (hp, damage, mine speed, crop yield), read live off the
# player's 'skills' component through skills.py. laid out as a scrollable
# spreadsheet — a frozen column-header row over a ScrollList grid with cell
# borders, zebra banding, and gray group-header rows.

import pygame as pg

from ui import NineSliceSkin, ScrollList
from ui_theme import (
    COLOR_ROW_STRIPE, COLOR_TAB_INACTIVE_BG, COLOR_TEXT_BODY, COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY, MODAL_INNER_MARGIN, PANEL_SKIN_CORNER, PANEL_SKIN_FILE,
    PANEL_SKIN_SCALE, get_font,
)
import skills


PANEL_W = 480
INNER = MODAL_INNER_MARGIN    # inset that clears the 9-slice rail (shared value)
MARGIN = 16                   # gap from the screen's bottom-left corner

TITLE_H = 34
HEADER_H = 26                 # frozen column-header row, above the viewport
CELL_H = 30                   # one spreadsheet row
VISIBLE_ROWS = 7              # viewport height in rows; extra rows scroll
CELL_PAD = 8                  # left text inset inside a cell

PANEL_H = INNER + TITLE_H + HEADER_H + VISIBLE_ROWS * CELL_H + INNER

GRID = (72, 58, 44)                 # cell borders
HEADER_BG = COLOR_TAB_INACTIVE_BG   # column-header + group-header fill
# (label, fraction of the usable width). usable width excludes the scrollbar
# gutter so the rightmost cell never slides under the thumb.
COLS = (('Attribute', 0.50), ('Value', 0.26), ('Detail', 0.24))


class PlayerPanel:
    def __init__(self) -> None:
        self.open = False
        self.skin = NineSliceSkin(PANEL_SKIN_FILE, PANEL_SKIN_CORNER, scale=PANEL_SKIN_SCALE)
        self.font_title = get_font(24)
        self.font = get_font(18)
        self.font_small = get_font(15)
        self.origin: tuple[int, int] = (0, 0)
        self.rect = pg.Rect(0, 0, PANEL_W, PANEL_H)
        # grid rect is repositioned each frame; built once so scroll offset persists.
        self.grid = ScrollList(pg.Rect(0, 0, 0, 0), CELL_H)
        self._rows: list[tuple] = []

    # --- lifecycle ---

    def open_panel(self) -> None:
        self.open = True

    def close(self) -> None:
        self.open = False

    def toggle(self) -> None:
        self.open = not self.open

    # --- input ---

    def hit(self, mouse_pos: tuple[int, int]) -> bool:
        # display-only: a click anywhere on the panel is swallowed (no controls).
        return self.open and self.rect.collidepoint(mouse_pos)

    def handle_scroll(self, mouse_pos: tuple[int, int], amount: int) -> bool:
        if self.open and self.grid.rect.collidepoint(mouse_pos):
            self.grid.handle_scroll(amount)
            return True
        return False

    # --- render ---

    def _reposition(self, screen_size: tuple[int, int]) -> None:
        # bottom-left corner, matching where the inventory panel anchors.
        self.origin = (MARGIN, screen_size[1] - PANEL_H - MARGIN)
        self.rect = pg.Rect(self.origin[0], self.origin[1], PANEL_W, PANEL_H)

    def render(self, surface: pg.Surface, screen_size: tuple[int, int], player) -> None:
        if not self.open or player is None or player.skills is None:
            return
        self._reposition(screen_size)
        self.skin.render(surface, self.rect)

        ox, oy = self.origin
        x = ox + INNER
        w = PANEL_W - 2 * INNER

        title = self.font_title.render('Player', True, COLOR_TEXT_PRIMARY)
        surface.blit(title, title.get_rect(midtop=(ox + PANEL_W // 2, oy + INNER)))

        self._rows = self._build_rows(player, player.skills)
        header_y = oy + INNER + TITLE_H
        self._draw_header(surface, x, header_y, w)

        self.grid.rect = pg.Rect(x, header_y + HEADER_H, w, VISIBLE_ROWS * CELL_H)
        self.grid.render(surface, len(self._rows), self._render_row)
        # outer frame around the whole sheet (header + viewport)
        frame = pg.Rect(x, header_y, w, HEADER_H + VISIBLE_ROWS * CELL_H)
        pg.draw.rect(surface, GRID, frame, 1)

    def _build_rows(self, player, sk) -> list[tuple]:
        # each row is ('head', title) or ('data', attribute, value, detail).
        rows: list[tuple] = [('head', 'Skills')]
        for name in skills.SKILLS:
            level = skills.level_of(sk, name)
            xp = int(sk.get(name, 0))
            rows.append(('data', skills.display_name(name), f'Lv {level}', f'{xp:,} xp'))
        rows.append(('head', 'Stats'))
        lo, hi = skills.damage_range_for(skills.level_of(sk, 'combat'))
        mine_pct = round(skills.break_time_scale(skills.level_of(sk, 'mining')) * 100)
        rows.append(('data', 'Health', f'{player.health}/{player.max_health}', ''))
        rows.append(('data', 'Damage', f'{lo}–{hi}', ''))
        rows.append(('data', 'Mine speed', f'{mine_pct}%', ''))
        rows.append(('data', 'Crop yield', f'+{skills.yield_bonus(skills.level_of(sk, "farming"))}', ''))
        return rows

    def _col_rects(self, x: int, y: int, w: int, h: int) -> list[pg.Rect]:
        usable = w - ScrollList.SCROLLBAR_PAD
        rects, cx = [], x
        for _, frac in COLS:
            cw = int(usable * frac)
            rects.append(pg.Rect(cx, y, cw, h))
            cx += cw
        return rects

    def _draw_header(self, surface, x, y, w) -> None:
        pg.draw.rect(surface, HEADER_BG, pg.Rect(x, y, w, HEADER_H))
        for (label, _), cell in zip(COLS, self._col_rects(x, y, w, HEADER_H)):
            txt = self.font_small.render(label, True, COLOR_TEXT_MUTED)
            surface.blit(txt, txt.get_rect(midleft=(cell.x + CELL_PAD, cell.centery)))
            pg.draw.line(surface, GRID, (cell.right, y), (cell.right, y + HEADER_H), 1)

    def _render_row(self, surface, i, row_rect) -> None:
        kind = self._rows[i][0]
        if kind == 'head':
            pg.draw.rect(surface, HEADER_BG, row_rect)
            label = self._rows[i][1]
            txt = self.font.render(label, True, COLOR_TEXT_PRIMARY)
            surface.blit(txt, txt.get_rect(midleft=(row_rect.x + CELL_PAD, row_rect.centery)))
        else:
            if i % 2 == 1:                       # zebra banding
                band = pg.Surface((row_rect.width, row_rect.height), pg.SRCALPHA)
                band.fill(COLOR_ROW_STRIPE)
                surface.blit(band, row_rect.topleft)
            cells = self._col_rects(row_rect.x, row_rect.y, row_rect.width, row_rect.height)
            _, name, val, detail = self._rows[i]
            colors = (COLOR_TEXT_BODY, COLOR_TEXT_PRIMARY, COLOR_TEXT_MUTED)
            for cell, text, color in zip(cells, (name, val, detail), colors):
                if not text:
                    continue
                surf = self.font.render(text, True, color)
                surface.blit(surf, surf.get_rect(midleft=(cell.x + CELL_PAD, cell.centery)))
            for cell in cells[:-1]:              # vertical column separators
                pg.draw.line(surface, GRID, (cell.right, row_rect.y), (cell.right, row_rect.bottom), 1)
        pg.draw.line(surface, GRID, (row_rect.x, row_rect.bottom - 1),
                     (row_rect.x + row_rect.width, row_rect.bottom - 1), 1)
