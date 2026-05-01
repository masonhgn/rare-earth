# Place & Break — Implementation Tickets

Self-contained, step-by-step instructions for adding tile-locked entity placement and removal to the game. Two tickets here; the rest of the feature (input wiring, hover highlight, click handlers) will be filed in a follow-up document once these land.

Implement in order. Each ticket is meant to be reviewable on its own.

## Already done (don't redo)

- `Entity.id` defaults to `uuid.uuid4()` when no `entity_id` is supplied — `src/entity.py:31`.
- `data/entities/rock.json` has `tile_locked: true`.

---

## Ticket 1 — World tile occupancy index

**Goal.** `World` can answer "what entity is at tile (tx, ty)?" in O(1) and refuses to place an entity on a tile already occupied by another tile-locked entity.

**File touched.** `src/world.py`

**Why this ticket exists, and why it's first.** Click-targeting, placement, and removal all depend on this lookup. Landing the data-layer change on its own keeps it reviewable before any input/render code piles on top.

### Edits

#### 1. Initialize the index in `World.__init__`

After the existing `self.entities = {}` line, add:

```python
self.tile_index = {}
```

Order matters slightly: `tile_index` must exist *before* `self.spawn_player()` runs, since `spawn_player` goes through `add_entity`. The player is `tile_locked=False` so it won't actually touch the index, but you don't want a future change to introduce a `tile_locked=True` spawn that hits an `AttributeError`.

Final shape of `__init__`:
```python
def __init__(self):
    self.map_grid = None
    self.generate_random_map(['grass'], 30, 30)
    self.entities = {}
    self.tile_index = {}
    self.spawn_player()
```

#### 2. Replace `add_entity`

```python
def add_entity(self, entity):
    if entity.prototype.tile_locked:
        footprint = self._entity_footprint(entity)
        for tile in footprint:
            if tile in self.tile_index:
                raise ValueError(f"tile {tile} already occupied")
        for tile in footprint:
            self.tile_index[tile] = entity.id
    self.entities[entity.id] = entity
```

Notes on the design:
- Two passes over `footprint`: first checks for any collision, second writes. This keeps `tile_index` consistent if a multi-tile entity overlaps an existing one — without the split, you'd half-write before raising and corrupt the index.
- Player flows through the `if` branch unchanged (`tile_locked=False`), so it never enters `tile_index`.
- Raising `ValueError` is intentional. Callers (the right-click handler in a later ticket) will check `tile_index` *before* calling and avoid the exception path; the raise exists to catch programmer error if anyone bypasses the check.

#### 3. Add `remove_entity`

```python
def remove_entity(self, entity_id):
    entity = self.entities.pop(entity_id, None)
    if entity is None:
        return
    if entity.prototype.tile_locked:
        for tile in self._entity_footprint(entity):
            self.tile_index.pop(tile, None)
```

`pop(..., None)` (rather than `del`) makes both removal paths idempotent — calling `remove_entity` twice is a no-op rather than a `KeyError`.

#### 4. Add `_entity_footprint` helper

```python
def _entity_footprint(self, entity):
    base_tx = int(entity.world_x) // TILE_LENGTH
    base_ty = int(entity.world_y) // TILE_LENGTH
    rows = len(entity.prototype.grid)
    cols = len(entity.prototype.grid[0])
    return [(base_tx + c, base_ty + r) for r in range(rows) for c in range(cols)]
```

The `int(...)` cast: `move_continuous` makes `world_x`/`world_y` floats over time. Tile-locked entities currently sit at integer pixel positions, but if a float ever sneaks in, `200.0 // 64` gives `3.0` (float) and `(3.0, 3.0) != (3, 3)` as dict keys — silent miss. Cheap to defend.

`TILE_LENGTH` is already imported via `from config import *`, so no import changes.

#### 5. Add `get_entity_at_tile`

```python
def get_entity_at_tile(self, tx, ty):
    entity_id = self.tile_index.get((tx, ty))
    if entity_id is None:
        return None
    return self.entities.get(entity_id)
```

Returns `None` for unoccupied tiles or stale ids; never raises.

### Verification

Create a throwaway file at `src/scratch_test.py`:

```python
from world import World
from entity import Entity
from prototype import load_prototype

w = World()
rock = Entity(load_prototype("rock"), (5 * 64, 7 * 64))
w.add_entity(rock)

assert (5, 7) in w.tile_index, "rock not registered in tile_index"
assert w.get_entity_at_tile(5, 7) is rock, "lookup failed"

w.remove_entity(rock.id)
assert (5, 7) not in w.tile_index, "remove_entity did not clear tile"
assert w.get_entity_at_tile(5, 7) is None, "lookup should miss after remove"

# Collision: two rocks at the same tile must raise on the second.
a = Entity(load_prototype("rock"), (10 * 64, 10 * 64))
b = Entity(load_prototype("rock"), (10 * 64, 10 * 64))
w.add_entity(a)
try:
    w.add_entity(b)
    assert False, "expected ValueError on overlap"
except ValueError:
    pass

# Player should never enter tile_index.
assert all(eid != "player" for eid in w.tile_index.values()), "player leaked into tile_index"

print("ok")
```

Run from the project root:

```
python src/scratch_test.py
```

Expected output: `ok`. Delete `src/scratch_test.py` after a green run — don't commit it.

---

## Ticket 2 — Render placed entities each frame

**Goal.** Non-player entities in `World.entities` are drawn each frame, between the map and the player. Player still draws last so it stays on top of anything beneath it.

**File touched.** `src/game.py`

**Depends on.** Ticket 1. The verification spawns a `tile_locked=True` rock through the new `add_entity`, which needs `tile_index` to exist.

### Edits

In `Game.start`'s render block, the current code is:

```python
self.screen.render(self.world.map_grid, 0, 0, vc)

self.screen.render(player.grid, player.world_x, player.world_y, vc)
```

Wait — actually it's `player.prototype.grid` after the refactor. Confirm by reading your current `game.py`. Either way, insert an entity loop between map and player so the rendering order becomes **map → other entities → player**:

```python
self.screen.render(self.world.map_grid, 0, 0, vc)

for entity in self.world.entities.values():
    if entity is player:
        continue
    self.screen.render(entity.prototype.grid, entity.world_x, entity.world_y, vc)

self.screen.render(player.prototype.grid, player.world_x, player.world_y, vc)
```

A few choices worth knowing:

- **`is player` over `entity.id == "player"`.** Object identity is robust against id renames or accidental reuse. `player` is already captured at the top of `start()` via `player = self.world.get_player()`, so `is` is cheap.
- **No depth sort.** `dict.values()` iterates in insertion order in modern Python, which is good enough until you have overlapping multi-tile entities at different y-coordinates that need to occlude each other. That's a real concern for tall/foreshortened sprites; not yet a concern at 1×1 ground rocks. Add depth sorting when it becomes visible, not before.
- **`World.get_entity_rendering_order` stub stays untouched.** It's empty in `world.py` and unused. Don't fill it in speculatively — when depth sorting is needed, that'll be its own ticket.

### Verification

To see a rendered entity before any input wiring exists, hardcode a temporary spawn in `World.__init__`, immediately after `self.spawn_player()`:

```python
# TEMP — remove after Ticket 2 verification.
from prototype import load_prototype
self.add_entity(Entity(load_prototype("rock"), (4 * 64, 4 * 64)))
```

(Remember `from entity import Entity` and `from prototype import load_prototype` — `Entity` is already imported at the top of `world.py`. The local `from prototype import load_prototype` is fine inside `__init__`, or hoist to the top alongside the existing one.)

Run:

```
python src/main.py
```

Expected: a rock tile visible at world `(256, 256)`, roughly upper-left of where the player spawns. Walk over it — the player should pass through (no collision in v1, by design — collision is its own future ticket).

**Once you've seen the rock render, delete the temp spawn lines.** Later tickets will introduce rocks via right-click and a permanent hardcoded one would just clutter the test world.

---

## What's next

Once Tickets 1 and 2 are merged and the temp rock spawn is removed, the next ticket file will cover:

- Screen↔world coordinate conversion (inverse of `Screen._world_to_screen`).
- Hover tracking and reach proximity check on `MOUSEMOTION`.
- Hover highlight rendering.
- Left-click break, right-click place wiring.

Ask for that file when you're ready for it.
