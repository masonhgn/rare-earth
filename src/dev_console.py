
# in-game developer console.
#
# a small command prompt toggled with the backtick (`) key, for dev testing.
# the console is generic: the HOST (single-player Game today; the net client
# later) injects a command table {name: (handler, usage)} where
# handler(args: list[str]) -> str returns one line of feedback. so the same
# console UI can drive direct-mutation commands in single-player and, later,
# intent-sending commands over the network.

import pygame as pg

from ui_theme import get_font


_MAX_LOG = 6        # recent output lines kept on screen
_LINE_PAD = 2
_MARGIN = 10


class DevConsole:
    def __init__(self, commands: dict) -> None:
        # commands: {name: (handler, usage_str)}
        self.commands = commands
        self.open = False
        self.input = ''
        self.log: list[str] = []

    def toggle(self) -> None:
        self.open = not self.open
        if not self.open:
            self.input = ''

    # --- input: called for every KEYDOWN while open (swallows game controls) ---

    def handle_key(self, event) -> None:
        key = event.key
        if key in (pg.K_BACKQUOTE, pg.K_ESCAPE):
            self.toggle()
            return
        if key in (pg.K_RETURN, pg.K_KP_ENTER):
            self._run(self.input.strip())
            self.input = ''
            return
        if key == pg.K_BACKSPACE:
            self.input = self.input[:-1]
            return
        ch = event.unicode
        if ch and ch.isprintable() and ch != '`':
            self.input += ch

    def _run(self, text: str) -> None:
        if not text:
            return
        self._out('> ' + text)
        parts = text.split()
        name, args = parts[0], parts[1:]
        entry = self.commands.get(name)
        if entry is None:
            self._out(f'unknown command: {name}   (try "help")')
            return
        handler, _usage = entry
        try:
            result = handler(args)
        except Exception as exc:   # a dev command must never crash the game
            result = f'error: {exc}'
        if result:
            self._out(result)

    def _out(self, line: str) -> None:
        self.log.append(line)
        del self.log[:-_MAX_LOG]

    # --- render (bottom-of-screen bar) ---

    def render(self, surface: pg.Surface) -> None:
        if not self.open:
            return
        font = get_font(18)
        line_h = font.get_height() + _LINE_PAD
        lines = self.log[-_MAX_LOG:] + ['> ' + self.input + '_']
        h = line_h * len(lines) + 2 * _LINE_PAD + 4
        w = surface.get_width()
        y0 = surface.get_height() - h
        panel = pg.Surface((w, h), pg.SRCALPHA)
        panel.fill((0, 0, 0, 205))
        surface.blit(panel, (0, y0))
        y = y0 + _LINE_PAD + 2
        for i, line in enumerate(lines):
            is_input = i == len(lines) - 1
            color = (235, 235, 180) if is_input else (200, 200, 200)
            surface.blit(font.render(line, True, color), (_MARGIN, y))
            y += line_h
