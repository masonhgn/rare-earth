
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
from item import format_quantity, get_item_icon, load_item
from resources import load_image
import slots as slot_ops


class Inventory:
    def __init__(self):
        self.slots: list[dict | None] = [None] * INVENTORY_SLOTS
        self.open = False
        self.panel_image = load_image(INVENTORY_UI_FILE)
        self.rect = self.panel_image.get_rect(topleft=(0, 0))
        # origin = where the panel is drawn on screen each frame
        self.origin = (0, 0)
        self.font = pg.font.Font(None, 15)

    def toggle(self) -> None:
        self.open = not self.open

    # --- slot geometry ---

    def _slot_topleft(self, slot_index: int) -> tuple[int, int]:
        col = slot_index % INVENTORY_COLS
        row = slot_index // INVENTORY_COLS
        ox, oy = self.origin
        x = ox + INVENTORY_BORDER_PX + col * INVENTORY_SLOT_PX
        y = oy + INVENTORY_BORDER_PX + row * INVENTORY_SLOT_PX
        return (x, y)

    def slot_at_pixel(self, mouse_pos: tuple[int, int]) -> int | None:
        # return slot index under the mouse, or None if outside / on a border.
        # the inner area starts at origin + INVENTORY_BORDER_PX. each slot is
        # INVENTORY_SLOT_PX wide; we treat the last 2px of each slot as border.
        mx, my = mouse_pos
        ox, oy = self.origin
        rel_x = mx - ox - INVENTORY_BORDER_PX
        rel_y = my - oy - INVENTORY_BORDER_PX
        if rel_x < 0 or rel_y < 0:
            return None
        col = rel_x // INVENTORY_SLOT_PX
        row = rel_y // INVENTORY_SLOT_PX
        if col >= INVENTORY_COLS or row >= INVENTORY_ROWS:
            return None
        # reject the 2px border between slots (last 2px of each slot cell)
        if rel_x % INVENTORY_SLOT_PX >= INVENTORY_SLOT_PX - 2:
            return None
        if rel_y % INVENTORY_SLOT_PX >= INVENTORY_SLOT_PX - 2:
            return None
        return int(row * INVENTORY_COLS + col)

    # --- mutation ---

    def add_item(self, item_id: str, quantity: int) -> int:
        # shared slot logic — returns leftover that didn't fit (>0 only
        # when slots are full of mismatched items).
        return slot_ops.add(self.slots, item_id, quantity)

    def add_to_slot(self, item_id: str, quantity: int, slot_index: int) -> int:
        # add into a specific slot. no stack cap: returns leftover only for
        # the mismatch case, which callers (handle_click) route to a swap.
        slot = self.slots[slot_index]
        if slot is None:
            self.slots[slot_index] = {'item_id': item_id, 'quantity': quantity}
            return 0
        if slot['item_id'] != item_id:
            return quantity
        slot['quantity'] += quantity
        return 0

    def take_from_slot(self, slot_index: int) -> dict | None:
        # remove and return the entire stack in `slot_index`, or None if empty.
        slot = self.slots[slot_index]
        self.slots[slot_index] = None
        return slot

    # --- mouse drag/drop ---

    def handle_click(self, slot_index: int, held: dict | None) -> dict | None:
        # returns the new mouse_held_item (or None if nothing held after the click).
        #
        # cases:
        #   not holding, slot empty   -> noop, hand stays empty
        #   not holding, slot has X   -> pick up X, slot becomes empty
        #   holding A, slot empty     -> drop A into slot
        #   holding A, slot has A     -> merge into slot (no cap)
        #   holding A, slot has B     -> swap (A goes to slot, B comes to hand)
        slot = self.slots[slot_index]

        if held is None:
            if slot is None:
                return None
            return self.take_from_slot(slot_index)

        # empty slot OR same-item slot: same path. always consumes the
        # entire held stack since there's no cap.
        if slot is None or slot['item_id'] == held['item_id']:
            self.add_to_slot(held['item_id'], held['quantity'], slot_index)
            return None

        # different item: swap
        new_held = slot
        self.slots[slot_index] = {'item_id': held['item_id'], 'quantity': held['quantity']}
        return new_held

    # --- render ---

    def render(self, surface: pg.Surface) -> None:
        if not self.open:
            return
        self.rect.topleft = self.origin
        surface.blit(self.panel_image, self.rect)
        for i, slot in enumerate(self.slots):
            if slot is None:
                continue
            proto = load_item(slot['item_id'])
            # force the inventory size so slots stay uniform regardless of
            # per-item icon_size overrides or the larger world-drop default.
            img = get_item_icon(proto, size=INVENTORY_ICON_SIZE)
            pos = self._slot_topleft(i)
            surface.blit(img, pos)
            if slot['quantity'] > 1:
                label = self.font.render(format_quantity(slot['quantity']), True, (255, 255, 255))
                label_rect = label.get_rect(
                    bottomright=(pos[0] + img.get_width(), pos[1] + img.get_height())
                )
                surface.blit(label, label_rect)
