
# character/stats sheet. opened from the 'player' hud tab — takes the inventory's
# bottom-left slot (the two are mutually exclusive; opening one closes the other),
# NOT a centered modal like settings/exchange.
#
# display-only: shows the four skill tracks (level + total xp) and the derived
# stats they drive (hp, damage, mine speed, crop yield), read live off the
# player's 'skills' component through skills.py. uses the shared 9-slice pane
# art so it matches the other windows, sized compact to fit its content.

import pygame as pg

from ui import NineSliceSkin
from ui_theme import (
    COLOR_TEXT_BODY, COLOR_TEXT_MUTED, COLOR_TEXT_PRIMARY, MODAL_INNER_MARGIN,
    PANEL_SKIN_CORNER, PANEL_SKIN_FILE, PANEL_SKIN_SCALE, get_font,
)
import skills


PANEL_W = 480
INNER = MODAL_INNER_MARGIN    # inset that clears the 9-slice rail (shared value)
MARGIN = 16                   # gap from the screen's bottom-left corner

TITLE_H = 34
ROW_H = 30                    # one skill: name + "Lv N   xp" on a single line
STATS_GAP = 10
STATS_LABEL_H = 28
STAT_ROW_H = 28
# derived so the bottom rail gets the same inset as the top. the +10 matches the
# divider-to-label gap drawn in _draw_stats.
PANEL_H = (INNER + TITLE_H + len(skills.SKILLS) * ROW_H
           + STATS_GAP + 10 + STATS_LABEL_H + 4 * STAT_ROW_H + INNER)

DIVIDER = (90, 90, 108)


class PlayerPanel:
    def __init__(self) -> None:
        self.open = False
        self.skin = NineSliceSkin(PANEL_SKIN_FILE, PANEL_SKIN_CORNER, scale=PANEL_SKIN_SCALE)
        self.font_title = get_font(24)
        self.font = get_font(18)
        self.origin: tuple[int, int] = (0, 0)
        self.rect = pg.Rect(0, 0, PANEL_W, PANEL_H)

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
        sk = player.skills

        title = self.font_title.render('Player', True, COLOR_TEXT_PRIMARY)
        surface.blit(title, title.get_rect(midtop=(ox + PANEL_W // 2, oy + INNER)))

        y = oy + INNER + TITLE_H
        for name in skills.SKILLS:
            self._draw_skill_row(surface, x, y, w, name, sk)
            y += ROW_H

        self._draw_stats(surface, x, y, w, player, sk)

    def _draw_skill_row(self, surface, x, y, w, name, sk) -> None:
        level = skills.level_of(sk, name)
        xp = int(sk.get(name, 0))
        surface.blit(self.font.render(skills.display_name(name), True, COLOR_TEXT_BODY), (x, y))
        value = self.font.render(f'Lv {level}   {xp:,} xp', True, COLOR_TEXT_PRIMARY)
        surface.blit(value, value.get_rect(topright=(x + w, y)))

    def _draw_stats(self, surface, x, y, w, player, sk) -> None:
        y += STATS_GAP
        pg.draw.line(surface, DIVIDER, (x, y), (x + w, y), 1)
        y += 10
        surface.blit(self.font.render('Stats', True, COLOR_TEXT_MUTED), (x, y))
        y += STATS_LABEL_H

        lo, hi = skills.damage_range_for(skills.level_of(sk, 'combat'))
        mine_pct = round(skills.break_time_scale(skills.level_of(sk, 'mining')) * 100)
        rows = [
            ('Health', f'{player.health}/{player.max_health}'),
            ('Damage', f'{lo}–{hi}'),
            ('Mine speed', f'{mine_pct}%'),
            ('Crop yield', f'+{skills.yield_bonus(skills.level_of(sk, "farming"))}'),
        ]
        for label, value in rows:
            surface.blit(self.font.render(label, True, COLOR_TEXT_MUTED), (x, y))
            val = self.font.render(value, True, COLOR_TEXT_BODY)
            surface.blit(val, val.get_rect(topright=(x + w, y)))
            y += STAT_ROW_H
