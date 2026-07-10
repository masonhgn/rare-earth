
# in-game developer console.
#
# a small command prompt toggled with the backtick (`) key, for dev testing.
# the console is generic: the HOST (single-player Game today; the net client
# later) injects a command table {name: (handler, usage)} where
# handler(args: list[str]) -> str returns one line of feedback. so the same
# console UI can drive direct-mutation commands in single-player and, later,
# intent-sending commands over the network.

import pygame as pg

from ui_theme import get_mono_font
from textfield import TextField


_INPUT_COLOR = (235, 235, 180)
_MAX_LOG = 6        # recent output lines kept on screen
_MAX_SUGGEST = 6    # autocomplete rows shown at once
_LINE_PAD = 2
_MARGIN = 10


class DevConsole:
    def __init__(self, commands: dict) -> None:
        # commands: {name: (handler, usage_str)}
        self.commands = commands
        self.open = False
        # chromeless field: the console owns the prompt / panel layout; the
        # field supplies the caret, selection, key-repeat and clipboard.
        self.field = TextField(pg.Rect(0, 0, 10, 20), get_mono_font(18),
                               chrome=False, color_text=_INPUT_COLOR)
        self.log: list[str] = []
        self._sel = 0   # highlighted autocomplete row (index into suggestions)

    def toggle(self) -> None:
        self.open = not self.open
        if self.open:
            self.field.focus()
        else:
            self.field.blur()
            self.field.clear()
            self._sel = 0

    # --- input: host forwards KEYDOWN + TEXTINPUT here while open ---

    def handle_event(self, event) -> None:
        if event.type == pg.KEYDOWN:
            key = event.key
            if key in (pg.K_BACKQUOTE, pg.K_ESCAPE):
                self.toggle()
                return
            if key == pg.K_TAB:
                # autocomplete: fill in the highlighted suggestion + a trailing
                # space so the next keystroke starts the arguments.
                suggestions, _ = self._completions()
                if suggestions:
                    self.field.set_text(suggestions[self._sel % len(suggestions)] + ' ')
                    self._sel = 0
                return
            if key in (pg.K_UP, pg.K_DOWN):
                # move the highlight through the suggestion list.
                suggestions, _ = self._completions()
                if suggestions:
                    step = -1 if key == pg.K_UP else 1
                    self._sel = (self._sel + step) % len(suggestions)
                return
            if key in (pg.K_RETURN, pg.K_KP_ENTER):
                self._run(self.field.text.strip())
                self.field.clear()
                self._sel = 0
                return
        # everything else — caret moves, backspace/delete, selection, clipboard,
        # and TEXTINPUT insertion — is the field's; reset the suggestion
        # highlight whenever the text actually changed.
        before = self.field.text
        self.field.handle_event(event)
        if self.field.text != before:
            self._sel = 0

    def _completions(self) -> tuple[list[str], str | None]:
        # what to show beneath the log while typing. WHILE still on the command
        # word (no space yet): the command names matching the prefix (capped),
        # which Up/Down highlight and Tab completes. ONCE a command is chosen (a
        # space follows a known name, or the word is a unique exact match): its
        # usage string instead, to guide the arguments. only one is non-empty.
        text = self.field.text
        if not text:
            return [], None
        if ' ' in text:
            entry = self.commands.get(text.split(' ', 1)[0])
            return [], (entry[1] if entry else None)
        matches = sorted(n for n in self.commands if n.startswith(text))
        if matches == [text]:   # uniquely + fully typed: show its usage
            return [], self.commands[text][1]
        return matches[:_MAX_SUGGEST], None

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
        font = get_mono_font(18)
        line_h = font.get_height() + _LINE_PAD
        log_lines = self.log[-_MAX_LOG:]
        suggestions, usage = self._completions()
        sel = self._sel % len(suggestions) if suggestions else 0

        # panel spans the log + any suggestion rows / usage hint + the input.
        n_rows = len(log_lines) + len(suggestions) + (1 if usage else 0) + 1
        h = line_h * n_rows + 2 * _LINE_PAD + 4
        w = surface.get_width()
        y0 = surface.get_height() - h
        panel = pg.Surface((w, h), pg.SRCALPHA)
        panel.fill((0, 0, 0, 205))
        surface.blit(panel, (0, y0))

        y = y0 + _LINE_PAD + 2
        for line in log_lines:
            surface.blit(font.render(line, True, (200, 200, 200)), (_MARGIN, y))
            y += line_h
        # suggestion rows: each shows the command's usage (name + args); the
        # highlighted one (Tab completes it) is brighter and marked.
        for i, name in enumerate(suggestions):
            is_sel = i == sel
            text = ('> ' if is_sel else '  ') + self.commands[name][1]
            color = (150, 235, 175) if is_sel else (120, 150, 130)
            surface.blit(font.render(text, True, color), (_MARGIN, y))
            y += line_h
        if usage:
            surface.blit(font.render('  ' + usage, True, (140, 155, 175)), (_MARGIN, y))
            y += line_h
        # prompt drawn here; the field renders the editable text (caret +
        # selection) immediately after it on the same baseline.
        prompt = font.render('> ', True, _INPUT_COLOR)
        surface.blit(prompt, prompt.get_rect(midleft=(_MARGIN, y + line_h // 2)))
        px = _MARGIN + prompt.get_width()
        self.field.rect = pg.Rect(px, y, surface.get_width() - px - _MARGIN, line_h)
        self.field.draw(surface)
