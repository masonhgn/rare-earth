
# resource / path resolution for both "run from source" and "frozen exe".
#
# the whole codebase loads assets with cwd-relative paths like
# 'src/data/sprites/...', and some of those paths are baked INSIDE the data
# files themselves (animations.json, sprites.json), so they can't be rewritten
# per call site. the simplest thing that makes every one of them resolve under
# a PyInstaller build is to chdir into the directory the bundled asset tree
# lives under — then 'src/data/...' works unchanged.
#
# writable state (saves, settings) must NOT live inside the bundle: onefile
# extracts to a temp dir that's wiped on exit, and even onedir's app folder is
# the wrong place for user data. those go next to the executable instead.

import os
import sys

FROZEN = getattr(sys, 'frozen', False)

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)


def resource_base() -> str:
    # directory the read-only asset tree (src/data/...) sits under.
    # frozen: PyInstaller's extraction dir (onefile) / _internal (onedir).
    # source: the project root.
    if FROZEN:
        return sys._MEIPASS
    return _PROJECT_ROOT


def writable_base() -> str:
    # persistent, user-writable dir for saves + settings. next to the exe when
    # frozen (portable), the project root when running from source (unchanged
    # from the old behaviour, so dev saves stay where they were).
    if FROZEN:
        return os.path.dirname(sys.executable)
    return _PROJECT_ROOT


def init() -> None:
    # call once, before any asset is loaded. makes cwd-relative 'src/data/...'
    # paths — including the ones inside the json data files — resolve against
    # the bundled assets without touching each call site.
    if FROZEN:
        os.chdir(resource_base())
