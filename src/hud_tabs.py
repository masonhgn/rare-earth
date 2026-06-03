
# right-side tab strip. each tab is a small icon tile that toggles a
# piece of ui:
#   - backpack  -> shows/hides the inventory grid
#   - settings  -> opens/closes the settings modal
#
# tabs are stacked vertically and vertically centered on the right edge
# of the screen. extra tabs can be appended by registering more
# (id, image_path, on_click) entries — the strip resizes automatically.

import pygame as pg

from resources import load_image


TAB_SIZE = 56
TAB_GAP = 10
# pixel offset between the right edge of the screen and the tab column.
RIGHT_EDGE_MARGIN = 16


class HudTab:
    def __init__(self, tab_id: str, image_path: str, on_click) -> None:
        self.id = tab_id
        self.image = pg.transform.smoothscale(load_image(image_path), (TAB_SIZE, TAB_SIZE))
        self.on_click = on_click
        self.rect = pg.Rect(0, 0, TAB_SIZE, TAB_SIZE)


class HudTabs:
    def __init__(self, screen, tabs: list) -> None:
        # screen: anything with .width / .height (typically a Screen or
        # DisplayService). tabs: list of (id, image_path, on_click).
        self._screen = screen
        self.tabs = [HudTab(t_id, path, cb) for (t_id, path, cb) in tabs]

    def _layout(self) -> None:
        # vertical column on the right edge, centered top-to-bottom so
        # a resize keeps them visually balanced.
        screen_w = self._screen.width
        screen_h = self._screen.height
        n = len(self.tabs)
        total_h = n * TAB_SIZE + max(0, n - 1) * TAB_GAP
        x = screen_w - TAB_SIZE - RIGHT_EDGE_MARGIN
        y = (screen_h - total_h) // 2
        for tab in self.tabs:
            tab.rect = pg.Rect(x, y, TAB_SIZE, TAB_SIZE)
            y += TAB_SIZE + TAB_GAP

    def render(self, surface: pg.Surface) -> None:
        self._layout()
        for tab in self.tabs:
            surface.blit(tab.image, tab.rect.topleft)

    def handle_click(self, pos: tuple[int, int]) -> bool:
        # returns True if a tab consumed the click (so the caller can
        # short-circuit other handlers).
        self._layout()
        for tab in self.tabs:
            if tab.rect.collidepoint(pos):
                tab.on_click()
                return True
        return False
