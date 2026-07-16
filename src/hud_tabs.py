
# top-right tab strip. each tab is a small icon tile that toggles a
# piece of ui:
#   - player    -> character/stats sheet
#   - backpack  -> shows/hides the inventory grid
#   - settings  -> opens/closes the settings modal
#
# tabs sit in a horizontal row tucked under the minimap, right-aligned to its
# edge. extra tabs can be appended by registering more (id, image_path,
# on_click) entries — the row grows leftward automatically.

import pygame as pg

from resources import load_image


TAB_SIZE = 36
TAB_GAP = 6
# anchor under the top-right minimap. mirrors Minimap.BOX_PX (200) + its
# padding (12) + 2px backdrop, right-aligned with the minimap's right edge.
_MINIMAP_RIGHT_PAD = 12
_MINIMAP_BOTTOM = _MINIMAP_RIGHT_PAD + 200 + 2
TOP_MARGIN = _MINIMAP_BOTTOM + 8
RIGHT_EDGE_MARGIN = _MINIMAP_RIGHT_PAD


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
        # horizontal row under the minimap, right-aligned to its edge; the row
        # grows leftward as tabs are added so the last one stays at the corner.
        screen_w = self._screen.width
        n = len(self.tabs)
        total_w = n * TAB_SIZE + max(0, n - 1) * TAB_GAP
        x = screen_w - RIGHT_EDGE_MARGIN - total_w
        y = TOP_MARGIN
        for tab in self.tabs:
            tab.rect = pg.Rect(x, y, TAB_SIZE, TAB_SIZE)
            x += TAB_SIZE + TAB_GAP

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
