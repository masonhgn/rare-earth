
# main game class. owns the subsystems, wires them together, and runs the
# update/render loop. the heavy lifting is delegated out:
#   - world layer queueing + break visuals -> WorldRenderer (render.py)
#   - screen-space overlays (hud, minimap, tooltip, held item) -> HudOverlay (hud.py)
#   - player path-follow / facing / bounds / collision -> movement.py
#   - break / drag-mining state machine -> BreakSystem (breaking.py)
#
# render order each frame: WorldRenderer.flush() queues + flushes the world
# layers (terrain -> overlay -> shadow -> dropped -> entity -> player ->
# highlight), then the screen-space ui is drawn directly on top.

import pygame as pg

from config import TILE_LENGTH, TITLE
from world import World
from render import Screen, Minimap, WorldRenderer, MapView
from inventory import Inventory
from hud import Hud, HudOverlay
from ui_theme import get_font
from settings import load_settings, save_settings
from breaking import BreakSystem
from factory import FactorySystem, FactoryPanel
from mob import MobSystem
from combat import CombatSystem
from exchange import ExchangePanel
from settings_panel import SettingsPanel
from hud_tabs import HudTabs
from display import DisplayService
from spot_market import SpotMarket
from contracts import ContractSystem
from clock import DayClock
from save_state import save_game, load_game
import movement
import input_handler
import worldgen


# how long the black "YOU DIED" screen holds before the player respawns.
DEATH_SCREEN_SEC = 2.0


class Game:
    def __init__(self):
        pg.init()
        pg.font.init()
        pg.display.set_caption(TITLE)

        self.settings = load_settings()
        self.screen = Screen(
            self.settings['screen_width'],
            self.settings['screen_height'],
            display_mode=self.settings['display_mode'],
        )

        self.world = World()
        # the inventory VIEW renders/edits the local player's data, which now
        # lives on the player entity's 'player' component (per-player). the
        # getter re-resolves each access, so it survives respawn/load.
        self.inventory = Inventory(get_data=lambda: self.world.get_player().inventory)
        self.hud = Hud()
        self.minimap = Minimap(self.world)
        # full-screen whole-world map overlay (Tab). builds its own surface;
        # the corner minimap is local (the area around the player).
        self.map_view = MapView(self.world)
        self.hud.visible = self.settings.get('show_hud', True)
        # break state, drag-mining mode, particle effects all live here. the
        # tile-changed callback refreshes the (client-only) minimap on mining.
        self.break_system = BreakSystem(self.world, on_tile_changed=self.minimap.update_cell)
        # combat: health, damage, floating numbers + over-head health bars.
        self.combat = CombatSystem(self.world)
        # queues the world layers + break visuals onto the renderer each frame.
        self.world_renderer = WorldRenderer(self.screen, self.world, self.break_system)
        # factory system + modal panel ui for machine entities.
        self.factory_system = FactorySystem(self.world)
        self.factory_panel = FactoryPanel()
        # mob ai: wander + chase-the-player for entities with a 'mob' component.
        # takes break_system (dust on landing) + combat (damage the player).
        self.mob_system = MobSystem(self.world, self.break_system, self.combat)
        # global spot market: per-item prices walking on a 5s real-time
        # tick. constructed before ExchangePanel so the panel can hold a
        # ref to it for sell/buy.
        self.spot_market = SpotMarket()
        # contract system: settles active contracts on day rollover.
        self.contract_system = ContractSystem(self.world)
        # global day clock. on_rollover triggers contract settlement +
        # autosave; constructed here so we can pass it to ExchangePanel.
        self.day_clock = DayClock()
        self.day_clock.on_rollover = self._on_day_rollover
        # exchange panel (modal, three tabs). day_clock ref is needed at
        # accept-contract time to stamp due_day.
        self.exchange_panel = ExchangePanel(self.spot_market, self.inventory, self.day_clock)
        # display facade: panel + hud_tabs go through this for screen
        # ops, so they don't need a full Game reference.
        self.display = DisplayService(self.settings, self.screen, self._position_inventory)
        # settings modal: ESC opens it, contains display-mode switcher
        # and save/quit buttons.
        self.settings_panel = SettingsPanel(
            self.display,
            on_save=lambda: save_game(self),
            on_quit=self.stop,
        )
        # hud tabs anchored to the right edge — quick toggles for the
        # inventory and the settings modal.
        self.hud_tabs = HudTabs(self.screen, [
            ('inventory', 'src/data/sprites/ui/tabs/backpack.png', self.inventory.toggle),
            ('settings', 'src/data/sprites/ui/tabs/settings.png', self._toggle_settings),
        ])
        # screen-space overlays (diagnostics, day counter, minimap, hover
        # tooltip, held item) — reads display state off this Game.
        self.hud_overlay = HudOverlay(self)

        # ui state. held_item (the drag cursor) lives on the local player's
        # 'player' component now — see the Game.held_item property below.
        self.hover_pos: tuple[int, int] = (0, 0)

        # click marker: yellow X drawn at the click world pos, fading out
        # over a short window (handled by WorldRenderer). None when idle.
        self.click_marker: tuple[tuple[float, float], int] | None = None

        # at most one pending click-to-walk action is queued at a time.
        # populated by input_handler when the player clicks something
        # they can't reach yet; pending_action.fire() runs the moment
        # ready() returns True (typically when the player walks within
        # range). cleared on WASD preempt or after firing.
        self.pending_action = None

        # frame loop state
        self.clock = pg.time.Clock()
        self.dt = 0.0
        self.running = False

        # death screen: on player death, hold a paused black "YOU DIED" screen
        # for DEATH_SCREEN_SEC, then respawn.
        self.dying = False
        self.death_timer = 0.0


        # restore prior session from disk if a save exists; otherwise
        # seed the world with the default factory + starter drops.
        if load_game(self):
            # load_game replaces day_clock, so re-bind the rollover hook.
            self.day_clock.on_rollover = self._on_day_rollover
        else:
            self._seed_world()
        self._position_inventory()

    # --- per-player cursor: held_item lives on the local player's component ---

    @property
    def held_item(self):
        return self.world.get_player().held_item

    @held_item.setter
    def held_item(self, value) -> None:
        self.world.get_player().held_item = value

    # --- setup helpers ---

    def _seed_world(self) -> None:
        # default world contents (factory, exchange, boards, a goblin, pickups),
        # shared with the headless server via worldgen so they can't drift.
        worldgen.seed_world(self.world, self.spot_market)

    def close_factory_panel(self) -> None:
        # closes the panel AND deposits any cursor-held item back into the
        # player inventory. without this, walking away from the factory while
        # holding an item leaves it stuck in held_item — subsequent world
        # clicks then drop the invisible item and never reach the walk logic.
        self._deposit_held_item()
        self.factory_panel.close()

    def open_factory_panel(self, entity) -> None:
        # close any other modal first so only one panel is up at a time.
        if self.exchange_panel.open:
            self.close_exchange_panel()
        self.factory_panel.open_for(entity, (self.screen.width, self.screen.height))
        self.inventory.open = True

    def open_exchange_panel(self, entity) -> None:
        if self.factory_panel.open:
            self.close_factory_panel()
        self.exchange_panel.open_for(entity, (self.screen.width, self.screen.height))
        self.inventory.open = True

    def close_exchange_panel(self) -> None:
        # mirror close_factory_panel — return any cursor-held item to the
        # inventory (overflow to floor) so it doesn't get stranded.
        self._deposit_held_item()
        self.exchange_panel.close()

    def open_modal_for_entity(self, entity) -> None:
        # dispatch by the prototype's interactable kind. callers don't
        # need to know which panel matches which entity type, and adding
        # a new interactable is one entry in this dict.
        kind = entity.prototype.interactable
        opener = {
            'factory': self.open_factory_panel,
            'exchange': self.open_exchange_panel,
        }.get(kind)
        if opener is not None:
            opener(entity)

    def _deposit_held_item(self) -> None:
        # return any cursor-held stack to the inventory (overflow to a
        # floor drop). shared between close_factory_panel and save-on-quit.
        if self.held_item is None:
            return
        leftover = self.inventory.add_item(
            self.held_item['item_id'], self.held_item['quantity'],
        )
        if leftover > 0:
            player = self.world.get_player()
            sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
            cx = player.world_x + sw / 2
            cy = player.world_y + sh / 2
            self.world.spawn_dropped_item(self.held_item['item_id'], leftover, (cx, cy))
        self.held_item = None

    def _position_inventory(self) -> None:
        # bottom-left corner with a small margin. (top-left would collide
        # with the HUD overlay.) keep rect in sync with origin so
        # collidepoint checks (used to suppress world clicks/highlight
        # behind the panel) work without waiting for the next render pass.
        panel_h = self.inventory.panel_image.get_height()
        x = 16
        y = self.screen.height - panel_h - 16
        self.inventory.origin = (x, y)
        self.inventory.rect.topleft = (x, y)

    # --- display lifecycle ---

    def toggle_fullscreen(self) -> None:
        # cycles windowed -> fullscreen -> borderless -> windowed via
        # the display facade. name kept as toggle_* because F2 has been
        # "the display key" since before borderless existed.
        self.display.cycle_mode()

    def _toggle_settings(self) -> None:
        # called from the settings hud tab. open if closed, close if open.
        if self.settings_panel.open:
            self.settings_panel.close()
        else:
            self.settings_panel.open_panel(self.display.screen_size)

    def toggle_map(self) -> None:
        # Tab toggles the full-screen world map. opening it closes any other
        # modal and returns a cursor-held item so nothing is stranded behind it.
        if self.dying:
            return
        if self.map_view.open:
            self.map_view.close()
            return
        if self.factory_panel.open:
            self.close_factory_panel()
        if self.exchange_panel.open:
            self.close_exchange_panel()
        if self.settings_panel.open:
            self.settings_panel.close()
        self._deposit_held_item()
        self.map_view.open = True

    def start_attack(self, facing: str = 'right') -> None:
        # play the one-shot sword swing on the player, facing left or right.
        # normal movement facing resumes on its own once it finishes (see
        # _update). damage is TBD — animation only.
        if self.map_view.open:
            return
        player = self.world.get_player()
        if player.anim is not None:
            state = 'attacking_left' if facing == 'left' else 'attacking_right'
            player.anim.play_once(state, pg.time.get_ticks())

    def player_attack(self, target) -> None:
        # the player hits `target`: swing facing it, knock it back, and deal
        # damage. called for an in-range click (or on arrival after walking).
        if self.map_view.open:
            return
        player = self.world.get_player()
        psw = (player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH))[0]
        tsw = (target.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH))[0]
        facing = 'left' if (target.world_x + tsw / 2) < (player.world_x + psw / 2) else 'right'
        self.start_attack(facing)
        movement.knock_back(player, target)
        self.combat.hit(target, pg.time.get_ticks())

    def _respawn_player(self, player) -> None:
        # on death: drop the whole inventory where the player fell, then
        # respawn at the middle of the map at full health.
        sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        death_pos = (player.world_x + sw / 2, player.world_y + sh / 2)
        inv = player.inventory
        for slot in inv.slots:
            if slot is not None:
                self.world.spawn_dropped_item(slot['item_id'], slot['quantity'], death_pos)
        inv.slots = [None] * len(inv.slots)
        # respawn centered on the map, full health, transient state cleared.
        player.world_x = self.world.width * TILE_LENGTH / 2 - sw / 2
        player.world_y = self.world.height * TILE_LENGTH / 2 - sh / 2
        player.health = player.max_health
        player.last_damage_ms = None
        player.knockback_x = player.knockback_y = 0.0
        player.path = []
        self.pending_action = None
        # snap the camera to the respawn point now (it already followed the
        # death spot earlier this frame) so there's no one-frame jump.
        self.screen.camera.follow((player.world_x, player.world_y), sprite_size=(sw, sh))

    # --- main loop ---

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        while self.running:
            self.dt = self.clock.tick(self.settings['fps_cap']) / 1000.0

            input_handler.event_loop(self)
            if not self.running:
                break

            self._update()
            self._render()
            pg.display.flip()

        # close the factory panel before snapshotting so any cursor-held
        # item is returned to inventory rather than vanishing on save.
        if self.factory_panel.open:
            self.close_factory_panel()
        self._deposit_held_item()
        save_game(self)
        save_settings({**self.settings, 'show_hud': self.hud.visible})
        pg.quit()

    def stop(self) -> None:
        self.running = False

    # --- per-frame update ---

    def _on_day_rollover(self, new_day: int) -> None:
        # settle any forward contracts whose due_day has arrived. fires
        # before autosave so the resolved outcomes are what gets written.
        self.contract_system.settle_day_rollover(new_day)
        # autosave on every day boundary. quick, since the save is a
        # single json blob and the day clock only ticks once per ~120s.
        save_game(self)

    def _update(self) -> None:
        # death screen: hold a paused black "YOU DIED" screen, then respawn.
        if self.dying:
            self.death_timer -= self.dt
            if self.death_timer <= 0.0:
                self._respawn_player(self.world.get_player())
                self.dying = False
            return
        # the full-screen map pauses the world: skip all simulation + input
        # while it's open. event_loop still runs, so Tab/Esc can close it.
        if self.map_view.open:
            return
        # untangle any overlapping living bodies first (e.g. a mob spawned on
        # the player) so nothing is stuck before movement runs this frame.
        movement.separate_living(self.world)
        self.day_clock.tick(self.dt)
        self.spot_market.tick(self.dt)
        player = self.world.get_player()
        dx, dy = input_handler.poll_movement(player, self.dt)
        moved_dx = moved_dy = 0.0
        if dx or dy:
            # manual WASD preempts any active path. per-axis collision (solids
            # + other living things) so the player slides instead of sticking
            # or overlapping.
            player.path = []
            self.pending_action = None
            moved_dx, moved_dy = movement.move_axis(self.world, player, dx, dy)
        elif player.path:
            moved_dx, moved_dy = movement.follow_path(player, self.world, self.dt)
        # knockback impulse (from being hit) layered on top of normal movement;
        # kick up dust where the player lands once the impulse runs out.
        if movement.apply_knockback(self.world, player, self.dt):
            hb = player.hitbox_rect()
            self.break_system.spawn_dust((hb.centerx, hb.bottom), pg.time.get_ticks())
        movement.clamp_player_to_bounds(self.world)

        # one canonical place to update the player's facing/animation state,
        # using the *actual* movement vector applied this frame. while a
        # one-shot attack swing is mid-play, leave it alone; normal facing
        # resumes automatically the frame after the swing finishes.
        anim = player.anim
        if anim is None or not anim.oneshot or anim.finished:
            movement.update_player_animation(player, moved_dx, moved_dy)

        # pending click-to-walk action (break or open). fires the moment
        # the player is within range; both kinds clear the path so the
        # player stops walking after the action runs.
        if self.pending_action is not None and self.pending_action.ready(self):
            self.pending_action.fire(self)
            player.path = []
            self.pending_action = None

        # auto-close: if a panel is open and the player has wandered out of
        # adjacency (manual WASD or path mid-walk), close it. each panel
        # close path returns any cursor-held item to the inventory.
        if self.factory_panel.open and self.factory_panel.entity is not None:
            machine = self.factory_panel.entity
            if not any(self.world.tile_in_reach(tx, ty, max_dist=1) for tx, ty in machine.footprint()):
                self.close_factory_panel()
        if self.exchange_panel.open and self.exchange_panel.entity is not None:
            ent = self.exchange_panel.entity
            if not any(self.world.tile_in_reach(tx, ty, max_dist=1) for tx, ty in ent.footprint()):
                self.close_exchange_panel()

        # camera follows the player, accounting for sprite size so the player
        # appears centered (not anchored at top-left).
        sprite_size = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        self.screen.camera.follow((player.world_x, player.world_y), sprite_size=sprite_size)

        # auto-pickup drops whose rect overlaps the player's hitbox rect.
        picked = self.world.collect_dropped_in_rect(player.hitbox_rect())
        for drop in picked:
            leftover = self.inventory.add_item(drop.item_id, drop.quantity)
            if leftover > 0:
                # inventory full: spit the leftover back onto the ground
                self.world.spawn_dropped_item(drop.item_id, leftover, drop.world_pos)

        # break/drag-mining state machine + particle physics
        self.break_system.tick(self.dt)
        # advance any in-progress machine recipes
        self.factory_system.tick()
        # advance mob ai (wander/chase) using the up-to-date player position
        self.mob_system.tick(self.dt)
        # age out floating damage numbers
        self.combat.tick(pg.time.get_ticks())
        # player death: kick off the YOU DIED screen; the respawn (drop loot +
        # recenter) fires when it finishes, in the dying branch above.
        if player.health is not None and player.health <= 0:
            self.dying = True
            self.death_timer = DEATH_SCREEN_SEC

    # --- render ---

    def _render(self) -> None:
        if self.dying:
            self._render_death_screen()
            return
        self.screen.clear()
        cam = self.screen.camera
        culling = self.screen.culling

        # world layers + break visuals, queued and flushed in LAYERS order.
        # everything world-space draws into the offscreen world surface (via
        # the camera), which present_world() then scales onto the display by
        # the zoom factor.
        self.click_marker = self.world_renderer.flush(cam, culling, self.click_marker)
        # combat overlays (over-head health bars + floating damage numbers)
        # sit above the world but below the screen-space ui — so they scale
        # with the world, they draw onto the world surface, not the display.
        self.combat.render_world(self.screen.world_surface, cam, culling, pg.time.get_ticks())
        # scale the zoomed world onto the display before the native-res ui.
        self.screen.present_world()

        # screen-space ui drawn directly on top of the presented world.
        self.hud_overlay.render_base()
        self.inventory.render(self.screen.surface)
        self.hud_tabs.render(self.screen.surface)
        self.factory_panel.render(self.screen.surface, (self.screen.width, self.screen.height))
        self.exchange_panel.render(self.screen.surface, (self.screen.width, self.screen.height))
        self.settings_panel.render(self.screen.surface, (self.screen.width, self.screen.height))
        self.map_view.render(self.screen.surface, (self.screen.width, self.screen.height), self.screen.camera)
        self.hud_overlay.render_cursor()

    def _render_death_screen(self) -> None:
        # full black screen with big red YOU DIED text, held for DEATH_SCREEN_SEC.
        surf = self.screen.surface
        surf.fill((0, 0, 0))
        label = get_font(72).render('YOU DIED', True, (170, 30, 30))
        surf.blit(label, label.get_rect(center=(self.screen.width // 2, self.screen.height // 2)))
