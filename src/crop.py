
# crop growth + harvest.
#
# a planted crop is a normal tile-locked editable entity carrying a 'crop'
# component {stage}. growth is day-based: CropSystem.advance_day bumps every
# crop one stage on each in-game day rollover, capped at the final (mature)
# stage. the rendered sprite tracks the stage via Entity.render_grid, so a
# single entity walks through its growth frames without swapping prototypes.
#
# harvest is stage-gated (harvest_drops): breaking a mature crop yields grain
# plus a seed back; breaking an immature one only returns the seed, so an early
# harvest costs the grain but not the seed. this module is pure over its inputs
# (no world imports) so world.break_entity can call harvest_drops without a
# cycle, and CropSystem takes the world as a constructor arg.


def _pair(spec):
    # a {"item", "quantity"} sub-spec -> (item_id, quantity), or None if absent.
    if not spec:
        return None
    return (spec['item'], spec['quantity'])


def is_mature(crop_spec: dict, stage: int) -> bool:
    return stage >= len(crop_spec['stages']) - 1


def harvest_drops(crop_spec: dict, stage: int) -> list[tuple[str, int]]:
    # what a crop drops when broken at `stage`. mature (final stage) -> the
    # harvest yield plus a seed; immature -> just the seed returned.
    drops = []
    if is_mature(crop_spec, stage):
        harvest = _pair(crop_spec.get('harvest'))
        if harvest is not None:
            drops.append(harvest)
    seed = _pair(crop_spec.get('seed'))
    if seed is not None:
        drops.append(seed)
    return drops


class CropSystem:
    def __init__(self, world) -> None:
        self.world = world

    def advance_day(self) -> None:
        # called once per in-game day rollover: grow every crop one stage,
        # capped at the final (mature) stage.
        for ent in self.world.entities_with('crop'):
            crop = ent.components['crop']
            if not is_mature(ent.prototype.crop, crop['stage']):
                crop['stage'] += 1
