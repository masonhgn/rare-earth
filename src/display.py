
# display facade: owns the settings dict + screen, applies display-mode
# changes, and notifies dependents (currently inventory anchor) when the
# surface resizes.
#
# extracted so SettingsPanel and HudTabs don't have to hold a full Game
# reference just to flip the display mode or re-anchor a panel after a
# resize. consumers receive a narrow object with exactly the operations
# they need.

from settings import save_settings, DISPLAY_MODES


class DisplayService:
    def __init__(self, settings: dict, screen, on_resize=None) -> None:
        self.settings = settings
        self.screen = screen
        # called immediately after a successful resize. lets the owner
        # re-anchor things that depend on screen dimensions (inventory
        # panel, hud anchors, etc.).
        self._on_resize = on_resize or (lambda: None)

    @property
    def screen_size(self) -> tuple[int, int]:
        return (self.screen.width, self.screen.height)

    @property
    def current_mode(self) -> str:
        return self.settings.get('display_mode', 'windowed')

    def set_mode(self, mode: str) -> None:
        # no-op when already in that mode — avoids re-opening the surface
        # for nothing, which on some platforms briefly flashes the window.
        if mode == self.current_mode:
            return
        self.settings['display_mode'] = mode
        save_settings(self.settings)
        self.screen.resize(
            self.settings['screen_width'],
            self.settings['screen_height'],
            display_mode=mode,
        )
        self._on_resize()

    def cycle_mode(self) -> None:
        try:
            idx = DISPLAY_MODES.index(self.current_mode)
        except ValueError:
            idx = 0
        self.set_mode(DISPLAY_MODES[(idx + 1) % len(DISPLAY_MODES)])
