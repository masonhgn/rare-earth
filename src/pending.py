
# pending click-to-walk-then-fire actions.
#
# both "click on a breakable while too far away" and "click on a building
# while too far away" use the same pattern: plan a path, walk it, fire
# the action when the player arrives within reach. previously these
# lived as two parallel raw-tuple fields on Game with two near-identical
# resolution branches in _update. now they share a single abstraction.
#
# only one pending action can be queued at a time. when input_handler
# detects a click target that's both an openable AND a breakable
# (e.g. clicking on grass adjacent to the exchange but on an ore tile),
# the opener wins — matches the immediate-action priority in the same
# handler when the player is already in reach.


class PendingAction:
    # subclasses implement ready() and fire(). ready() reads game state
    # to decide if it's time to trigger; fire() performs the action.
    def ready(self, game) -> bool:
        raise NotImplementedError

    def fire(self, game) -> None:
        raise NotImplementedError


class BreakOnArrival(PendingAction):
    # mirrors the per-frame check that used to live inline. validates
    # the target still exists before starting the break so a drag-mining
    # pass that already broke it doesn't trigger a phantom hit.
    def __init__(self, proto, entity_id, tile) -> None:
        self.proto = proto
        self.entity_id = entity_id
        self.tile = tile

    def ready(self, game) -> bool:
        return game.world.tile_in_reach(*self.tile)

    def fire(self, game) -> None:
        if self._still_there(game):
            game.break_system.start_break(
                self.proto, self.tile, entity_id=self.entity_id,
            )

    def _still_there(self, game) -> bool:
        if self.entity_id is None:
            return game.world.overlay_at(*self.tile) is not None
        return self.entity_id in game.world.entities


class OpenOnArrival(PendingAction):
    # ready when any footprint tile of the target is within adjacency
    # distance. dispatch to the right panel happens via the registered
    # opener for the prototype's interactable kind.
    def __init__(self, target) -> None:
        self.target = target

    def ready(self, game) -> bool:
        return any(
            game.world.tile_in_reach(tx, ty, max_dist=1)
            for tx, ty in self.target.footprint()
        )

    def fire(self, game) -> None:
        game.open_modal_for_entity(self.target)
