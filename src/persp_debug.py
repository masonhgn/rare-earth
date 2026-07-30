"""
temporary on-screen controls for the tier-1 perspective ground prototype.

a small panel (toggle button, draggable strength slider, -/+ nudge buttons)
wired directly to a render.Perspective. exists only so the effect can be
tuned by eye without relying on function keys (which macos eats). delete this
module and its client.py hookups once the look is dialed in.
"""

import pygame as pg

from ui_theme import get_font

# strength slider bounds; mirror the F7/F8 clamp so both paths agree.
_MIN = 0.0
_MAX = 0.8


class PerspectivePanel:
    # fixed top-left overlay operating on a shared Perspective instance. draws
    # after the world so it sits on top; click/drag routed from the client.
    W = 236
    H = 108
    PAD = 10

    def __init__(self, perspective, origin: tuple[int, int] = (16, 150)):
        self.p = perspective
        self.x, self.y = origin
        self._dragging = False
        # control rects are laid out once relative to origin (fixed panel).
        x, y = self.x, self.y
        self._toggle = pg.Rect(x + self.PAD, y + 28, self.W - 2 * self.PAD, 22)
        self._minus = pg.Rect(x + self.PAD, y + 76, 22, 22)
        self._plus = pg.Rect(x + self.W - self.PAD - 22, y + 76, 22, 22)
        self._track = pg.Rect(self._minus.right + 8, y + 82,
                              self._plus.left - self._minus.right - 16, 10)

    # --- geometry helpers ---

    @property
    def _bg(self) -> pg.Rect:
        return pg.Rect(self.x, self.y, self.W, self.H)

    def _strength_from_x(self, mx: int) -> float:
        t = (mx - self._track.left) / max(1, self._track.width)
        return round(_MIN + max(0.0, min(1.0, t)) * (_MAX - _MIN), 2)

    def _nudge(self, delta: float) -> None:
        self.p.strength = round(max(_MIN, min(_MAX, self.p.strength + delta)), 2)

    # --- input (returns True when a click is consumed) ---

    def handle_click(self, pos) -> bool:
        if not self._bg.collidepoint(pos):
            return False
        if self._toggle.collidepoint(pos):
            self.p.enabled = not self.p.enabled
        elif self._minus.collidepoint(pos):
            self._nudge(-0.02)
        elif self._plus.collidepoint(pos):
            self._nudge(0.02)
        elif self._track.inflate(0, 14).collidepoint(pos):
            self.p.strength = self._strength_from_x(pos[0])
            self._dragging = True
        return True   # swallow any click inside the panel so the world ignores it

    def handle_motion(self, pos) -> None:
        if self._dragging:
            self.p.strength = self._strength_from_x(pos[0])

    def handle_release(self) -> None:
        self._dragging = False

    # --- render ---

    def render(self, surface: pg.Surface) -> None:
        panel = pg.Surface((self.W, self.H), pg.SRCALPHA)
        panel.fill((20, 24, 30, 220))
        surface.blit(panel, (self.x, self.y))
        pg.draw.rect(surface, (90, 110, 130), self._bg, width=1)

        f = get_font(14)
        surface.blit(f.render('perspective ground (debug)', True, (210, 220, 235)),
                     (self.x + self.PAD, self.y + 8))

        # toggle
        on = self.p.enabled
        pg.draw.rect(surface, (60, 150, 90) if on else (70, 74, 82), self._toggle)
        pg.draw.rect(surface, (150, 170, 190), self._toggle, width=1)
        label = f'{"ON" if on else "OFF"}  (click to toggle)'
        txt = f.render(label, True, (240, 245, 250))
        surface.blit(txt, (self._toggle.centerx - txt.get_width() // 2,
                           self._toggle.centery - txt.get_height() // 2))

        # -/+ buttons
        for rect, sym in ((self._minus, '-'), (self._plus, '+')):
            pg.draw.rect(surface, (70, 74, 82), rect)
            pg.draw.rect(surface, (150, 170, 190), rect, width=1)
            s = f.render(sym, True, (240, 245, 250))
            surface.blit(s, (rect.centerx - s.get_width() // 2,
                            rect.centery - s.get_height() // 2))

        # slider track + knob
        pg.draw.rect(surface, (50, 54, 62), self._track, border_radius=4)
        t = (self.p.strength - _MIN) / (_MAX - _MIN) if _MAX > _MIN else 0.0
        kx = int(self._track.left + t * self._track.width)
        pg.draw.circle(surface, (230, 200, 90), (kx, self._track.centery), 7)

        val = f.render(f'strength  {self.p.strength:.2f}', True, (230, 220, 200))
        surface.blit(val, (self.x + self.PAD, self.y + 56))
