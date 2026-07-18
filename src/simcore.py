
# headless authoritative simulation core — the server's brain.
#
# constructs the World + gameplay systems and advances them with tick(dt), with
# NO rendering, input, camera, fonts, or display. the (future) server process
# runs this; the client (Game) renders + sends intents and will shed its own
# copies of these systems as it becomes a thin client.
#
# NOTE: factory craft progress is now dt-accumulated (elapsed_ms), but break /
# combat / mob cosmetic timing still reads pg.time.get_ticks(), so the host must
# have called pygame.init() (no display needed). Fully replacing that with a
# fixed-step clock (so the sim runs without pygame at all) is still deferred.

import pygame as pg

from config import TILE_LENGTH
from world import World
from breaking import BreakSystem
from combat import CombatSystem
from factory import FactorySystem
from mob import MobSystem
from spot_market import SpotMarket
from contracts import ContractSystem
from crop import CropSystem
from clock import DayClock
import movement
import worldgen
import save_state


class SimCore:
    def __init__(self, seed_default: bool = True) -> None:
        self.world = World()
        self.spot_market = SpotMarket()
        # no minimap/camera on the server; tile-change events will be networked.
        self.break_system = BreakSystem(self.world)
        self.combat = CombatSystem(self.world)
        self.factory_system = FactorySystem(self.world)
        self.mob_system = MobSystem(self.world, self.break_system, self.combat)
        self.contract_system = ContractSystem(self.world)
        # crop growth: one stage per in-game day. previously only single-player
        # advanced crops; hosting it here grows them for every client too.
        self.crop_system = CropSystem(self.world)
        self.day_clock = DayClock()
        self.day_clock.on_rollover = self._on_day_rollover
        if seed_default:
            self.seed()

    def _on_day_rollover(self, new_day: int) -> None:
        # settle due contracts + grow every planted crop one stage.
        self.contract_system.settle_day_rollover(new_day)
        self.crop_system.advance_day()

    def seed(self) -> None:
        worldgen.seed_world(self.world)

    def save(self, path: str | None = None) -> None:
        save_state.save_world(self, path or save_state.SERVER_SAVE_PATH)

    def load(self, path: str | None = None) -> bool:
        # True if a saved world was loaded. re-binds the day-rollover callback,
        # since load replaces the DayClock.
        if save_state.load_world(self, path or save_state.SERVER_SAVE_PATH):
            self.day_clock.on_rollover = self._on_day_rollover
            return True
        return False

    def tick(self, dt: float) -> None:
        # one authoritative world step. player movement arrives as intents and
        # is applied separately (Phase 3); this advances everything else.
        movement.separate_living(self.world)
        self.day_clock.tick(dt)
        self.spot_market.tick(dt)
        self.break_system.tick(dt)
        self.factory_system.tick(dt)
        self.mob_system.tick(dt)
        self.combat.tick(pg.time.get_ticks())
        # player death/respawn is paced by the server (it freezes dead players
        # for the death screen, then respawns) — see server.GameServer.
