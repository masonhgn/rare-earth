
# entity instance: a placed instance of a prototype with position, id,
# and (if animated) playback state.

import uuid
import pygame as pg
from config import TILE_LENGTH
from animation import AnimationState


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

        # per-entity component states keyed by name. each system that
        # ticks entities (FactorySystem, ContractSystem) iterates entities
        # carrying its component via world.entities_with(name).
        #
        # shapes:
        #   'machine':  {input_slots, output_slots, current_recipe, started_ms}
        #   'exchange': {drop_box, board, active}
        self.components: dict[str, dict] = {}
        if prototype.machine is not None:
            self.components['machine'] = {
                'input_slots': [None] * prototype.machine['input_slots'],
                'output_slots': [None] * prototype.machine['output_slots'],
                'current_recipe': None,
                'started_ms': 0,
            }
        if prototype.exchange is not None:
            self.components['exchange'] = {
                'drop_box': [None] * prototype.exchange['drop_box_slots'],
                'board': [],
                'active': [],
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

    @property
    def machine_state(self) -> dict | None:
        # legacy accessor — kept so existing call sites stay readable.
        return self.components.get('machine')

    @property
    def exchange_state(self) -> dict | None:
        return self.components.get('exchange')

    def move_continuous(self, dx: float, dy: float) -> None:
        if self.prototype.tile_locked:
            return
        self.world_x += dx
        self.world_y += dy

    def move_discrete(self, tx: int, ty: int) -> None:
        if not self.prototype.tile_locked:
            return
        self.world_x += tx * TILE_LENGTH
        self.world_y += ty * TILE_LENGTH

    def tile_coord(self) -> tuple[int, int]:
        # floor-divide *before* int(): for negative coords, int(-0.5) == 0
        # but the player is logically in tile -1 (which spans [-64, 0)).
        # doing // first then int() gives the correct floor.
        return (int(self.world_x // TILE_LENGTH), int(self.world_y // TILE_LENGTH))

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
