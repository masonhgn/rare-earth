
# factory / machine subsystem.
#
# FactorySystem.tick() advances every entity whose prototype has a `machine`
# spec: when idle it scans known recipes against the current input_slots
# contents, consumes inputs and starts the first matching recipe; when a
# recipe finishes (duration elapsed) it deposits outputs into output_slots.
# state lives on the Entity instance (machine_state dict) so processing
# continues even when the panel is closed.
#
# FactoryPanel is the UI: blits the asset, draws input/output slot grids
# + a header (recipe icon, name, progress bar), and routes click/drag/drop
# through the same held_item mechanism the inventory uses. output slots
# are take-only — holding an item over an output slot is a no-op.

import pygame as pg

from config import TILE_LENGTH
from item import format_quantity, get_item_icon, load_item, load_recipe
from resources import load_image
import slots as slot_ops


# panel art is 1254x1254. scaled down to fit on screen.
PANEL_SIZE = 600
PANEL_FILE = 'src/data/sprites/ui/factory_interface.png'

# header rect inside the rendered panel — where we draw the active recipe
# icon, name, and progress bar. measured from the source art header band.
HEADER_RECT = pg.Rect(40, 30, 520, 130)

# slot grids: 4 columns x 5 rows on each side. positions measured from the
# source art (slots are ~92x96 at 118-120 pitch in 1254px), scaled by 600/1254.
GRID_COLS = 4
GRID_ROWS = 5
SLOTS_PER_GRID = GRID_COLS * GRID_ROWS  # 20
SLOT_SIZE = 44
SLOT_PITCH = 57
INPUT_GRID_ORIGIN = (38, 228)
OUTPUT_GRID_ORIGIN = (345, 228)
# icon size that fits inside a slot with a small margin
SLOT_ICON_SIZE = 32


# ---------------------------------------------------------------------------
# FactorySystem — per-frame tick
# ---------------------------------------------------------------------------

class FactorySystem:
    def __init__(self, world):
        self.world = world

    def tick(self) -> None:
        now_ms = pg.time.get_ticks()
        for entity in self.world.entities_with('machine'):
            ms = entity.components['machine']
            if ms['current_recipe'] is None:
                self._try_start_recipe(entity, ms, now_ms)
            else:
                self._maybe_finish_recipe(ms, now_ms)

    # --- internals ---

    def _try_start_recipe(self, entity, ms, now_ms: int) -> None:
        for recipe_id in entity.prototype.machine.get('recipes', []):
            recipe = load_recipe(recipe_id)
            if _inputs_satisfied(ms['input_slots'], recipe.inputs):
                _consume_inputs(ms['input_slots'], recipe.inputs)
                ms['current_recipe'] = recipe_id
                ms['started_ms'] = now_ms
                return

    def _maybe_finish_recipe(self, ms, now_ms: int) -> None:
        recipe = load_recipe(ms['current_recipe'])
        if now_ms - ms['started_ms'] < recipe.duration_ms:
            return
        # ready to deposit; if outputs can't fit, leave the job done-but-pending
        # so we don't lose the result.
        if not _outputs_fit(ms['output_slots'], recipe.outputs):
            return
        _deposit_outputs(ms['output_slots'], recipe.outputs)
        ms['current_recipe'] = None


# ---------------------------------------------------------------------------
# recipe slot helpers — thin shims over slot_ops keyed by (item, qty)
# tuples so we don't construct intermediate dicts.
# ---------------------------------------------------------------------------

def _inputs_satisfied(input_slots, required) -> bool:
    return all(
        slot_ops.count(input_slots, item_id) >= qty
        for item_id, qty in required
    )


def _consume_inputs(input_slots, required) -> None:
    for item_id, qty in required:
        slot_ops.take(input_slots, item_id, qty)


def _outputs_fit(output_slots, outputs) -> bool:
    return slot_ops.can_add_all(output_slots, outputs)


def _deposit_outputs(output_slots, outputs) -> None:
    for item_id, qty in outputs:
        slot_ops.add(output_slots, item_id, qty)


# ---------------------------------------------------------------------------
# FactoryPanel — UI
# ---------------------------------------------------------------------------

class FactoryPanel:
    def __init__(self):
        self.open = False
        self.entity: 'Entity | None' = None
        # scale once at construction; the asset is bigger than the rendered
        # size so smoothscale produces cleaner edges than nearest-neighbor.
        raw = load_image(PANEL_FILE)
        self.panel_image = pg.transform.smoothscale(raw, (PANEL_SIZE, PANEL_SIZE))
        self.font = pg.font.Font(None, 22)
        self.font_small = pg.font.Font(None, 16)
        # set in open_for() so input_handler can collidepoint before render
        self.origin: tuple[int, int] = (0, 0)
        self.rect = pg.Rect(0, 0, PANEL_SIZE, PANEL_SIZE)

    def open_for(self, entity, screen_size: tuple[int, int]) -> None:
        self.entity = entity
        self.open = True
        x = (screen_size[0] - PANEL_SIZE) // 2
        y = (screen_size[1] - PANEL_SIZE) // 2
        self.origin = (x, y)
        self.rect = pg.Rect(x, y, PANEL_SIZE, PANEL_SIZE)

    def close(self) -> None:
        self.open = False
        self.entity = None

    def hit(self, mouse_pos: tuple[int, int]) -> bool:
        # whether the click landed on an interactive part of the panel.
        # factory deliberately treats panel-background clicks as
        # "outside" so they fall through to world handling (closes the
        # panel + walks). matches the long-standing UX.
        return (
            self.open
            and self.entity is not None
            and self.slot_at_pixel(mouse_pos) is not None
        )

    # --- slot geometry ---

    def _slot_topleft(self, grid_origin: tuple[int, int], slot_index: int) -> tuple[int, int]:
        col = slot_index % GRID_COLS
        row = slot_index // GRID_COLS
        ox, oy = grid_origin
        return (
            self.origin[0] + ox + col * SLOT_PITCH,
            self.origin[1] + oy + row * SLOT_PITCH,
        )

    def slot_at_pixel(self, mouse_pos: tuple[int, int]) -> tuple[str, int] | None:
        # returns (kind, slot_index) where kind is 'input' or 'output', or None.
        for kind, origin in (('input', INPUT_GRID_ORIGIN), ('output', OUTPUT_GRID_ORIGIN)):
            ox, oy = origin
            rel_x = mouse_pos[0] - self.origin[0] - ox
            rel_y = mouse_pos[1] - self.origin[1] - oy
            if rel_x < 0 or rel_y < 0:
                continue
            col = rel_x // SLOT_PITCH
            row = rel_y // SLOT_PITCH
            if col >= GRID_COLS or row >= GRID_ROWS:
                continue
            # reject the gap between cells
            if rel_x % SLOT_PITCH >= SLOT_SIZE:
                continue
            if rel_y % SLOT_PITCH >= SLOT_SIZE:
                continue
            return (kind, int(row * GRID_COLS + col))
        return None

    # --- mouse interaction ---

    def handle_click(self, mouse_pos: tuple[int, int], held: dict | None) -> dict | None:
        # returns the new held_item (without screen_pos — caller adds that).
        slot_info = self.slot_at_pixel(mouse_pos)
        if slot_info is None:
            return held  # click on panel background: no change
        kind, idx = slot_info
        ms = self.entity.machine_state
        if kind == 'output':
            return self._click_output(ms['output_slots'], idx, held)
        return self._click_input(ms['input_slots'], idx, held)

    def _click_output(self, output_slots, idx: int, held: dict | None) -> dict | None:
        # outputs are take-only: holding an item over them is ignored.
        if held is not None:
            return held
        slot = output_slots[idx]
        if slot is None:
            return None
        output_slots[idx] = None
        return slot

    def _click_input(self, input_slots, idx: int, held: dict | None) -> dict | None:
        # same drag/drop semantics as Inventory.handle_click for input slots.
        slot = input_slots[idx]
        if held is None:
            if slot is None:
                return None
            input_slots[idx] = None
            return slot
        if slot is None or slot['item_id'] == held['item_id']:
            if slot is None:
                input_slots[idx] = {'item_id': held['item_id'], 'quantity': held['quantity']}
            else:
                slot['quantity'] += held['quantity']
            return None
        # different item: swap
        new_held = slot
        input_slots[idx] = {'item_id': held['item_id'], 'quantity': held['quantity']}
        return new_held

    # --- render ---

    def render(self, surface: pg.Surface, screen_size: tuple[int, int]) -> None:
        if not self.open or self.entity is None:
            return
        # re-center each frame (handles screen resize cheaply)
        x = (screen_size[0] - PANEL_SIZE) // 2
        y = (screen_size[1] - PANEL_SIZE) // 2
        self.origin = (x, y)
        self.rect = pg.Rect(x, y, PANEL_SIZE, PANEL_SIZE)
        surface.blit(self.panel_image, self.origin)

        ms = self.entity.machine_state
        self._draw_slot_contents(surface, ms['input_slots'], INPUT_GRID_ORIGIN)
        self._draw_slot_contents(surface, ms['output_slots'], OUTPUT_GRID_ORIGIN)
        self._draw_header(surface, ms)

    def _draw_slot_contents(self, surface, slots, origin) -> None:
        for i, slot in enumerate(slots):
            if slot is None:
                continue
            proto = load_item(slot['item_id'])
            icon = get_item_icon(proto, size=SLOT_ICON_SIZE)
            pos = self._slot_topleft(origin, i)
            # center icon inside the slot cell
            icon_pos = (
                pos[0] + (SLOT_SIZE - icon.get_width()) // 2,
                pos[1] + (SLOT_SIZE - icon.get_height()) // 2,
            )
            surface.blit(icon, icon_pos)
            if slot['quantity'] > 1:
                label = self.font_small.render(format_quantity(slot['quantity']), True, (255, 255, 255))
                label_rect = label.get_rect(bottomright=(pos[0] + SLOT_SIZE - 2, pos[1] + SLOT_SIZE - 2))
                surface.blit(label, label_rect)

    def _draw_header(self, surface, ms) -> None:
        header_x = self.origin[0] + HEADER_RECT.x
        header_y = self.origin[1] + HEADER_RECT.y
        header_screen = pg.Rect(header_x, header_y, HEADER_RECT.width, HEADER_RECT.height)

        if ms['current_recipe'] is None:
            label = self.font.render('Idle', True, (210, 210, 210))
            surface.blit(label, label.get_rect(center=header_screen.center))
            return

        recipe = load_recipe(ms['current_recipe'])
        # show the first output's icon + name + progress bar
        out_id = recipe.outputs[0][0]
        out_proto = load_item(out_id)
        icon = get_item_icon(out_proto, size=SLOT_ICON_SIZE)

        icon_x = header_x + 16
        icon_y = header_y + (HEADER_RECT.height - icon.get_height()) // 2
        surface.blit(icon, (icon_x, icon_y))

        name_x = icon_x + icon.get_width() + 12
        name_y = header_y + 14
        name_label = self.font.render(out_proto.name, True, (240, 240, 230))
        surface.blit(name_label, (name_x, name_y))

        now_ms = pg.time.get_ticks()
        progress = min(1.0, max(0.0, (now_ms - ms['started_ms']) / recipe.duration_ms))
        bar_x = name_x
        bar_y = name_y + name_label.get_height() + 10
        bar_w = header_x + HEADER_RECT.width - 16 - bar_x
        bar_h = 12
        pg.draw.rect(surface, (35, 25, 20), pg.Rect(bar_x, bar_y, bar_w, bar_h))
        fill_w = int(bar_w * progress)
        if fill_w > 0:
            pg.draw.rect(surface, (255, 200, 90), pg.Rect(bar_x, bar_y, fill_w, bar_h))
