
# 40-slot player inventory with drag-and-drop and stack-limit awareness.
#
# data layout: self.slots is a flat list of length INVENTORY_SLOTS where
# each entry is either None (empty) or {"item_id": str, "quantity": int}.
#
# the ui is drawn relative to the panel's top-left on screen (self.origin).
# slot_at_pixel converts a screen-space mouse position into a slot index,
# returning None if the click is outside the panel or on a slot border.
#
# items stack without limit — add_item always fills the first matching
# stack (or empty slot) and returns 0. the leftover return remains for
# api shape consistency with code that branches on full inventories.

import pygame as pg

from config import (
    INVENTORY_COLS, INVENTORY_ROWS, INVENTORY_SLOTS,
    INVENTORY_SLOT_PX, INVENTORY_BORDER_PX, INVENTORY_UI_FILE,
    INVENTORY_ICON_SIZE,
)
from resources import load_image
from ui import SlotGrid
from ui_theme import get_font
import slots as slot_ops


class Inventory:
    # client-side VIEW over the local player's PlayerInventory (data). holds
    # the panel UI; reads/writes slots through get_data() each access so it
    # always targets the current local player — no stale references across
    # respawn/load. the authoritative slots live on the 'player' component.
    def __init__(self, get_data):
        self._get_data = get_data
        self.open = False
        self.panel_image = load_image(INVENTORY_UI_FILE)
        self.rect = self.panel_image.get_rect(topleft=(0, 0))
        # origin = where the panel is drawn on screen each frame
        self.origin = (0, 0)
        self.font = get_font(15)
        # slot wells are baked into the panel art, so draw_cells=False and
        # only icons/labels get drawn on top. slot_size = pitch - 2
        # reproduces the old 2px seam between cells exactly.
        self.grid = SlotGrid(
            (0, 0, 0, 0), INVENTORY_COLS, INVENTORY_ROWS, INVENTORY_SLOT_PX - 2,
            slot_gap=2, font=self.font, draw_cells=False,
            icon_size=INVENTORY_ICON_SIZE,
        )

    # --- data proxy (to the local player's PlayerInventory) ---

    @property
    def slots(self) -> list:
        return self._get_data().slots

    def add_item(self, item_id: str, quantity: int) -> int:
        return self._get_data().add_item(item_id, quantity)

    def toggle(self) -> None:
        self.open = not self.open

    # --- slot geometry ---

    def _sync_grid(self) -> None:
        # the grid lives at origin + border; origin moves each frame
        # (screen resize / repositioning) so re-anchor before any query.
        self.grid.rect.topleft = (
            self.origin[0] + INVENTORY_BORDER_PX,
            self.origin[1] + INVENTORY_BORDER_PX,
        )

    def slot_at_pixel(self, mouse_pos: tuple[int, int]) -> int | None:
        self._sync_grid()
        return self.grid.slot_at_pixel(mouse_pos)

    # --- mouse drag/drop ---

    def handle_click(self, slot_index: int, held: dict | None) -> dict | None:
        # pick / place / merge / swap — delegated to the shared widget.
        return self.grid.handle_click(slot_index, held, self.slots)

    # --- render ---

    def render(self, surface: pg.Surface) -> None:
        if not self.open:
            return
        self.rect.topleft = self.origin
        surface.blit(self.panel_image, self.rect)
        self._sync_grid()
        self.grid.render(surface, self.slots)
