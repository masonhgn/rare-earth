
# entity instance: a placed instance of a prototype with position, id,
# and (if animated) playback state.

import uuid
import pygame as pg
from config import TILE_LENGTH, EXCHANGE_DROP_BOX_SLOTS
from animation import AnimationState
from inventory_data import PlayerInventory


class Entity:
    def __init__(self, prototype, world_pos, entity_id=None):
        self.prototype = prototype
        self.id = entity_id if entity_id is not None else str(uuid.uuid4())
        self.world_x, self.world_y = world_pos

        # per-entity animation cursor, only created if the prototype has animation
        self.anim: AnimationState | None = None
        if prototype.animation is not None:
            self.anim = AnimationState(
                default_state=prototype.animation['default_state'],
                states=prototype.animation['states'],
            )

        # active pathfinding waypoints, in order, populated by the click-to-walk
        # handler. each frame the player walks toward path[0]'s tile center,
        # popping when within ~arrival_threshold pixels. None / empty = idle.
        self.path: list[tuple[int, int]] = []

        # transient knockback impulse (px/s), set when a hit lands and decayed
        # each frame by movement.apply_knockback. not serialized.
        self.knockback_x = 0.0
        self.knockback_y = 0.0

        # health: current hp, plus when it last changed (drives the over-head
        # bar's 6s auto-hide). max comes from the prototype; None => not a
        # living/damageable entity. not serialized — resets to full on load.
        self.max_health = prototype.max_health
        self.health = prototype.max_health
        self.last_damage_ms = None   # None = never damaged (no bar shown yet)

        # per-entity component states keyed by name. each system that
        # ticks entities (FactorySystem, ContractSystem) iterates entities
        # carrying its component via world.entities_with(name).
        #
        # shapes:
        #   'machine':  {input_slots, output_slots, current_recipe, elapsed_ms}
        # forward-contract state (board/active/drop_box) is NOT here — it's
        # per-player and lives on the 'player' component below.
        self.components: dict[str, dict] = {}
        if prototype.machine is not None:
            self.components['machine'] = {
                'input_slots': [None] * prototype.machine['input_slots'],
                'output_slots': [None] * prototype.machine['output_slots'],
                'current_recipe': None,
                # dt-accumulated craft progress (ms). a plain relative counter,
                # so it serializes / networks with no wall-clock rebasing.
                'elapsed_ms': 0.0,
            }
        if prototype.mob is not None:
            # ai state for MobSystem: wander/chase + cooldown timers. the
            # walking route itself lives on self.path (shared with the
            # player's path-follow), so it isn't duplicated in here.
            self.components['mob'] = {
                'state': 'wander',       # 'wander' | 'chase'
                'repath_cd': 0.0,        # seconds until next chase re-path
                'wander_pause': 0.0,     # idle seconds between strolls
                'attack_cd': 0.0,        # seconds until next melee swing
            }
        if prototype.is_player:
            # per-player state on the 'player' component. `inventory` is the
            # headless authoritative item store (the client renders it via the
            # Inventory *view*); `held_item` is the drag cursor; `exchange` is
            # this player's own forward-contract board/active/drop box (the
            # board is generated lazily from the spot market via
            # contracts.ensure_board — empty until then).
            self.components['player'] = {
                'inventory': PlayerInventory(),
                'held_item': None,
                'exchange': {
                    'board': [],
                    'active': [],
                    'drop_box': [None] * EXCHANGE_DROP_BOX_SLOTS,
                },
            }

    @property
    def machine_state(self) -> dict | None:
        # legacy accessor — kept so existing call sites stay readable.
        return self.components.get('machine')

    @property
    def exchange_state(self) -> dict | None:
        # per-player forward-contract state, or None for non-players.
        p = self.components.get('player')
        return p['exchange'] if p else None

    @property
    def is_player(self) -> bool:
        # role check that replaces the old hardcoded `id == 'player'` tests.
        return 'player' in self.components

    @property
    def inventory(self):
        # the player's PlayerInventory item store, or None for non-players.
        p = self.components.get('player')
        return p['inventory'] if p else None

    @property
    def held_item(self):
        p = self.components.get('player')
        return p['held_item'] if p else None

    @held_item.setter
    def held_item(self, value) -> None:
        self.components['player']['held_item'] = value

    def tile_coord(self) -> tuple[int, int]:
        # floor-divide *before* int(): for negative coords, int(-0.5) == 0
        # but the player is logically in tile -1 (which spans [-64, 0)).
        # doing // first then int() gives the correct floor.
        return (int(self.world_x // TILE_LENGTH), int(self.world_y // TILE_LENGTH))

    @property
    def center(self) -> tuple[float, float]:
        # the entity's visual center in world pixels (sprite midpoint), the
        # canonical "where is this thing" point for reach / distance / aggro /
        # targeting. accounts for oversized frames (a 128x128 sprite on a 64px
        # tile). previously re-derived as a private helper in five modules.
        w, h = self.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        return (self.world_x + w / 2, self.world_y + h / 2)

    @property
    def center_tile(self) -> tuple[int, int]:
        # the tile containing the visual center (vs tile_coord, the top-left
        # tile). reach checks measure from here.
        cx, cy = self.center
        return (int(cx // TILE_LENGTH), int(cy // TILE_LENGTH))

    def _footprint_dims(self) -> tuple[int, int]:
        # (cols, rows) of tiles this entity occupies. honors footprint_size
        # if set so a single-cell grid with a 128x128 sprite can claim a
        # 2x2 footprint without splitting the image into 4 sub-sprites.
        if self.prototype.footprint_size is not None:
            return self.prototype.footprint_size
        return (len(self.prototype.grid[0]), len(self.prototype.grid))

    def footprint(self) -> list[tuple[int, int]]:
        base_tx, base_ty = self.tile_coord()
        cols, rows = self._footprint_dims()
        return [(base_tx + c, base_ty + r) for r in range(rows) for c in range(cols)]

    def collision_rect(self) -> pg.Rect:
        # tile-aligned world rect used for solid-collision tests. covers
        # the entity's full footprint, not the rendered sprite size.
        cols, rows = self._footprint_dims()
        return pg.Rect(int(self.world_x), int(self.world_y),
                       cols * TILE_LENGTH, rows * TILE_LENGTH)

    def hitbox_rect(self) -> pg.Rect:
        # rect covering the visible body of the entity. used for pickup /
        # collision tests. when prototype.hitbox is set, the rect is sized
        # to it and positioned bottom-center within the sprite frame (so
        # the character's feet sit on the rect bottom). otherwise it spans
        # the full sprite frame.
        sprite_w, sprite_h = self.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        if self.prototype.hitbox is None:
            return pg.Rect(int(self.world_x), int(self.world_y), sprite_w, sprite_h)
        hw, hh = self.prototype.hitbox
        x = self.world_x + (sprite_w - hw) / 2
        y = self.world_y + (sprite_h - hh)
        return pg.Rect(int(x), int(y), hw, hh)
