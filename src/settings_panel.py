
# settings modal. opens with ESC (when no other panel is open), lets the
# player switch display mode and trigger a manual save. composed from
# the same ui primitives as the exchange so the visual style stays
# consistent.
#
# game ref is held directly because settings actions need to reach
# across many subsystems (display, save_state, settings dict). a
# narrower set of callbacks would just be game-shaped indirection.

import pygame as pg

from settings import DISPLAY_MODES
from ui import NineSliceSkin, Button, draw_button
from ui_theme import (
    COLOR_BUTTON_BORDER, COLOR_TAB_ACTIVE_BG, COLOR_BUTTON_BG,
    COLOR_TAB_ACTIVE_BORDER, COLOR_TEXT_BODY,
    COLOR_TEXT_MUTED, COLOR_TEXT_PRIMARY, MODAL_HEADER_H,
    MODAL_INNER_MARGIN, PANEL_SKIN_CORNER, PANEL_SKIN_FILE,
    PANEL_SKIN_SCALE, get_font,
)


PANEL_W, PANEL_H = 760, 420

# layout matches ui_theme so a tweak there propagates to every modal.
INNER_MARGIN = MODAL_INNER_MARGIN
HEADER_H = MODAL_HEADER_H

# display-mode buttons (one row)
MODE_BUTTON_H = 40
MODE_BUTTON_GAP = 8
MODE_LABELS = {
    'windowed': 'Windowed',
    'fullscreen': 'Fullscreen',
    'borderless': 'Borderless',
}

# save + quit buttons (centered, single line each)
ACTION_BUTTON_W = 200
ACTION_BUTTON_H = 44
ACTION_BUTTON_GAP = 18

# small ack message after a save, faded over this many ms
SAVE_ACK_MS = 1500


class SettingsPanel:
    def __init__(self, display, on_save, on_quit) -> None:
        # display: DisplayService — read current mode, switch + re-anchor.
        # on_save / on_quit: zero-arg callbacks. panel doesn't know about
        # Game or save_state directly anymore.
        self.display = display
        self.on_save = on_save
        self.on_quit = on_quit
        self.open = False
        self.skin = NineSliceSkin(PANEL_SKIN_FILE, PANEL_SKIN_CORNER, scale=PANEL_SKIN_SCALE)
        self.font = get_font(20)
        self.font_big = get_font(24)
        self.font_small = get_font(16)
        self.origin: tuple[int, int] = (0, 0)
        self.rect = pg.Rect(0, 0, PANEL_W, PANEL_H)
        # ack tick lets the panel briefly show "saved" after a save click
        self._save_ack_ms: int | None = None

    # --- lifecycle ---

    def open_panel(self, screen_size: tuple[int, int]) -> None:
        self.open = True
        self._reposition(screen_size)

    def close(self) -> None:
        self.open = False
        self._save_ack_ms = None

    def _reposition(self, screen_size: tuple[int, int]) -> None:
        x = (screen_size[0] - PANEL_W) // 2
        y = (screen_size[1] - PANEL_H) // 2
        self.origin = (x, y)
        self.rect = pg.Rect(x, y, PANEL_W, PANEL_H)

    # --- input ---

    def hit(self, mouse_pos: tuple[int, int]) -> bool:
        return self.open and self.rect.collidepoint(mouse_pos)

    def handle_click(self, mouse_pos: tuple[int, int]) -> None:
        # mode buttons
        for mode, rect in self._mode_button_rects().items():
            if rect.collidepoint(mouse_pos):
                self._apply_mode(mode)
                return
        # save button
        if self._save_button_rect().collidepoint(mouse_pos):
            self.on_save()
            self._save_ack_ms = pg.time.get_ticks()
            return
        # quit button — host main loop is responsible for the actual
        # shutdown sequence (autosave, pg.quit, etc.).
        if self._quit_button_rect().collidepoint(mouse_pos):
            self.on_quit()
            return

    # --- mutations ---

    def _apply_mode(self, mode: str) -> None:
        # DisplayService handles resize + inventory re-anchor internally;
        # we just refresh our own panel rect against the (possibly new)
        # screen size after the switch.
        self.display.set_mode(mode)
        self._reposition(self.display.screen_size)

    # --- render ---

    def render(self, surface: pg.Surface, screen_size: tuple[int, int]) -> None:
        if not self.open:
            return
        self._reposition(screen_size)
        self.skin.render(surface, self.rect)

        # title
        x, y = self.origin
        title = self.font_big.render('Settings', True, COLOR_TEXT_PRIMARY)
        surface.blit(title, (x + INNER_MARGIN, y + INNER_MARGIN + 2))

        # "Display Mode" caption + button row
        section_y = y + INNER_MARGIN + HEADER_H + 16
        caption = self.font.render('Display Mode', True, COLOR_TEXT_MUTED)
        surface.blit(caption, (x + INNER_MARGIN, section_y))
        for mode, rect in self._mode_button_rects().items():
            is_active = self.display.current_mode == mode
            color = COLOR_TAB_ACTIVE_BG if is_active else COLOR_BUTTON_BG
            border = COLOR_TAB_ACTIVE_BORDER if is_active else COLOR_BUTTON_BORDER
            draw_button(surface, rect, MODE_LABELS[mode], self.font,
                        bg=color, border=border, text_color=COLOR_TEXT_BODY)

        # "Save Game" caption + button
        save_section_y = section_y + 32 + MODE_BUTTON_H + 28
        caption = self.font.render('Save Game', True, COLOR_TEXT_MUTED)
        surface.blit(caption, (x + INNER_MARGIN, save_section_y))
        save_rect = self._save_button_rect()
        Button(save_rect, 'Save Now', self.font_big).render(surface)

        # save ack fades out a moment after a click for feedback
        if self._save_ack_ms is not None:
            age = pg.time.get_ticks() - self._save_ack_ms
            if age >= SAVE_ACK_MS:
                self._save_ack_ms = None
            else:
                alpha = int(255 * (1 - age / SAVE_ACK_MS))
                ack = self.font_small.render('saved.', True, (200, 240, 170))
                ack.set_alpha(alpha)
                surface.blit(ack, ack.get_rect(
                    midtop=(save_rect.centerx, save_rect.bottom + 6),
                ))

        # "Quit Game" button. main loop's shutdown path autosaves before
        # pg.quit, so no extra ack here.
        quit_rect = self._quit_button_rect()
        Button(quit_rect, 'Quit Game', self.font_big).render(surface)


    # --- geometry helpers ---

    def _mode_button_rects(self) -> dict[str, pg.Rect]:
        x, y = self.origin
        section_y = y + INNER_MARGIN + HEADER_H + 16 + 24
        # divide the interior width into n equal-ish button slots
        n = len(DISPLAY_MODES)
        avail = PANEL_W - 2 * INNER_MARGIN - (n - 1) * MODE_BUTTON_GAP
        btn_w = avail // n
        out: dict[str, pg.Rect] = {}
        for i, mode in enumerate(DISPLAY_MODES):
            bx = x + INNER_MARGIN + i * (btn_w + MODE_BUTTON_GAP)
            out[mode] = pg.Rect(bx, section_y, btn_w, MODE_BUTTON_H)
        return out

    def _save_button_rect(self) -> pg.Rect:
        x, y = self.origin
        save_section_y = y + INNER_MARGIN + HEADER_H + 16 + 24 + MODE_BUTTON_H + 28 + 24
        bx = x + (PANEL_W - ACTION_BUTTON_W) // 2
        return pg.Rect(bx, save_section_y, ACTION_BUTTON_W, ACTION_BUTTON_H)

    def _quit_button_rect(self) -> pg.Rect:
        # stacked under the save button. ACTION_BUTTON_GAP separates the
        # two so a misclick on save doesn't accidentally quit.
        save = self._save_button_rect()
        return pg.Rect(
            save.x, save.bottom + ACTION_BUTTON_GAP,
            ACTION_BUTTON_W, ACTION_BUTTON_H,
        )
