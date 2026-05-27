
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

    def footprint(self) -> list[tuple[int, int]]:
        # tile coords occupied by this entity, derived from prototype.grid shape.
        base_tx, base_ty = self.tile_coord()
        rows = len(self.prototype.grid)
        cols = len(self.prototype.grid[0])
        return [(base_tx + c, base_ty + r) for r in range(rows) for c in range(cols)]

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
