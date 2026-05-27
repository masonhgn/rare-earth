
# diagnostic overlay: fps, frame time, entity count. toggled by F3.

import pygame as pg


class Hud:
    def __init__(self):
        self.visible = True
        self.font = pg.font.Font(None, 20)

    def toggle(self) -> None:
        self.visible = not self.visible

    def render(self, surface: pg.Surface, *, fps: float, frame_ms: float, n_entities: int, n_dropped: int) -> None:
        if not self.visible:
            return
        lines = [
            f'fps {fps:.0f}  frame {frame_ms:.1f}ms',
            f'entities {n_entities}  drops {n_dropped}',
            'wasd move  b inventory  lmb break  rmb place  f2 fullscreen  f3 hud',
        ]
        y = 6
        for line in lines:
            label = self.font.render(line, True, (235, 235, 235))
            # 1-px black drop shadow for readability over any background
            shadow = self.font.render(line, True, (0, 0, 0))
            surface.blit(shadow, (7, y + 1))
            surface.blit(label, (6, y))
            y += label.get_height() + 2
