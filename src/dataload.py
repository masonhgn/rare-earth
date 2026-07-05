
# generic json-dict -> dataclass loader.
#
# maps a plain dict (parsed json) onto a dataclass by field name, so adding a
# field to a prototype is a one-line dataclass change with no matching edit in
# a hand-written loader. two conveniences make it fit the frozen prototypes:
#   - list values become tuples (recursively), since the frozen prototypes use
#     tuples for grid/drops/sprite_size/hitbox/... to stay hashable.
#   - unknown json keys are warned about and skipped rather than crashing, so a
#     stale or mistyped field in a data file is loud but non-fatal.
# `aliases` renames a json key onto a differently-named field (e.g. the item
# json's "image" -> ItemPrototype.image_path).

import dataclasses


def _to_tuple(value):
    # recursively convert lists to tuples; dicts + scalars pass through so
    # nested specs (animation/machine/mob dicts) keep their shape.
    if isinstance(value, list):
        return tuple(_to_tuple(v) for v in value)
    return value


def from_dict(cls, raw: dict, *, aliases: dict | None = None):
    aliases = aliases or {}
    field_names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {}
    for key, value in raw.items():
        name = aliases.get(key, key)
        if name not in field_names:
            print(f"warning: unknown key {key!r} in data for {cls.__name__} (ignored)")
            continue
        kwargs[name] = _to_tuple(value)
    return cls(**kwargs)
