from config import ENTITIES_DIR
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class EntityPrototype:
    grid: list[list[str]] #the makeup of tiles for the entity
    tile_locked: bool #is this entity locked to a tile?
    editable: bool #can the player place or destroy this?
    speed: float | None = None #not everything has a walk speed

_cache: dict[str, EntityPrototype] = {} #this prevents loading the same entities repeatedly

def load_prototype(name: str) -> EntityPrototype:
    if name not in _cache:
        with open(f"{ENTITIES_DIR}/{name}.json") as f:
            _cache[name] = EntityPrototype(**json.load(f))
    return _cache[name]