
# animation strip loader and per-entity playback state.
#
# strip format: a single png with N frames in a horizontal row, optionally
# inset by a margin and separated by a spacing. each animation_id in
# animations.json maps to one strip.
#
# `AnimationLibrary` slices each strip once at startup and caches frames.
# `AnimationState` holds the per-entity playback cursor (current state name,
# frame index, last_update_ms). it's intentionally plain data so prototypes
# can stay frozen and immutable.

import json
import pygame as pg


class AnimationLibrary:
    def __init__(self, animations_file: str) -> None:
        with open(animations_file) as f:
            self.specs = json.load(f)
        self._frames: dict[str, list[pg.Surface]] = {}

    def load(self) -> None:
        for anim_id, spec in self.specs.items():
            self._frames[anim_id] = self._slice_strip(spec)

    def _slice_strip(self, spec: dict) -> list[pg.Surface]:
        sheet = pg.image.load(spec['file']).convert_alpha()
        frame_w, frame_h = spec['size']
        n_frames = spec['frames']
        margin = spec.get('margin', 0)
        spacing = spec.get('spacing', 0)
        frames = []
        for i in range(n_frames):
            x = margin + i * (frame_w + spacing)
            y = margin
            frame = pg.Surface((frame_w, frame_h), pg.SRCALPHA).convert_alpha()
            frame.blit(sheet, (0, 0), (x, y, frame_w, frame_h))
            frames.append(frame)
        return frames

    def get_frames(self, anim_id: str) -> list[pg.Surface]:
        return self._frames[anim_id]

    def get_fps(self, anim_id: str) -> int:
        return self.specs[anim_id].get('fps', 5)


class AnimationState:
    def __init__(self, default_state: str, states: dict[str, str]):
        # states maps a logical state name (idle, walking_right) to an anim_id
        self.states = states
        self.current_state = default_state
        self.current_frame = 0
        self.last_update_ms = 0

    def set_state(self, state: str) -> None:
        if state == self.current_state or state not in self.states:
            return
        self.current_state = state
        self.current_frame = 0

    def advance(self, library: AnimationLibrary, now_ms: int) -> pg.Surface:
        anim_id = self.states[self.current_state]
        fps = library.get_fps(anim_id)
        frame_duration_ms = 1000 // max(fps, 1)
        if now_ms - self.last_update_ms >= frame_duration_ms:
            self.last_update_ms = now_ms
            frames = library.get_frames(anim_id)
            self.current_frame = (self.current_frame + 1) % len(frames)
        return library.get_frames(anim_id)[self.current_frame]
