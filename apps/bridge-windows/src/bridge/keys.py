"""Key names and chords ("ctrl+shift+t") to Windows virtual-key codes. Pure, so it's tested anywhere."""

from dataclasses import dataclass

from bridge.device import CommandError
from bridge.protocol import ErrorCode


@dataclass(frozen=True)
class Key:
    vk: int | None
    """None: a punctuation `char`, whose key depends on the keyboard layout."""
    char: str = ""
    extended: bool = False


_MODIFIERS = {
    "ctrl": Key(0x11),
    "control": Key(0x11),
    "alt": Key(0x12),
    "shift": Key(0x10),
    "win": Key(0x5B, extended=True),
    "windows": Key(0x5B, extended=True),
}
_NAMED = {
    "enter": Key(0x0D),
    "return": Key(0x0D),
    "esc": Key(0x1B),
    "escape": Key(0x1B),
    "tab": Key(0x09),
    "space": Key(0x20),
    "backspace": Key(0x08),
    "delete": Key(0x2E, extended=True),
    "del": Key(0x2E, extended=True),
    "insert": Key(0x2D, extended=True),
    "up": Key(0x26, extended=True),
    "down": Key(0x28, extended=True),
    "left": Key(0x25, extended=True),
    "right": Key(0x27, extended=True),
    "home": Key(0x24, extended=True),
    "end": Key(0x23, extended=True),
    "pageup": Key(0x21, extended=True),
    "pgup": Key(0x21, extended=True),
    "pagedown": Key(0x22, extended=True),
    "pgdn": Key(0x22, extended=True),
    "menu": Key(0x5D, extended=True),
    "printscreen": Key(0x2C, extended=True),
    "volumeup": Key(0xAF, extended=True),
    "volumedown": Key(0xAE, extended=True),
    "mute": Key(0xAD, extended=True),
    "playpause": Key(0xB3, extended=True),
    **{f"f{n}": Key(0x6F + n) for n in range(1, 25)},
}
_PUNCTUATION = set("`-=[]\\;',./")
_SPELLED = {"plus": "=", "minus": "-"}
"""Models write "ctrl+plus" to zoom in: apps take ctrl+= for it, as "+" is shift+= on the keyboard."""
LOCK = "lock"
"""Not a key: locks the PC."""


def chord(text: str) -> list[Key]:
    """The keys to hold, in order, the last one pressed while the rest are held: "ctrl+shift+t"."""
    names = [name.strip().lower() for name in text.split("+")]
    if any(not name for name in names):  # "ctrl++" too: zoom in is ctrl+=
        raise CommandError(ErrorCode.INVALID_COMMAND, f"Not a key: {text!r}")
    *held, last = names
    for name in held:
        if name not in _MODIFIERS:
            raise CommandError(ErrorCode.INVALID_COMMAND, f"Only ctrl, alt, shift and win can be held: {text!r}")
    if len(set(held)) != len(held):
        raise CommandError(ErrorCode.INVALID_COMMAND, f"A modifier twice: {text!r}")
    return [_MODIFIERS[name] for name in held] + [_key(last, text)]


def _key(name: str, text: str) -> Key:
    name = _SPELLED.get(name, name)
    if name in _NAMED:
        return _NAMED[name]
    if name in _MODIFIERS:  # pressed alone: "win" opens Start
        return _MODIFIERS[name]
    if len(name) == 1 and ("a" <= name <= "z" or "0" <= name <= "9"):
        return Key(ord(name.upper()))
    if len(name) == 1 and name in _PUNCTUATION:
        return Key(None, char=name)
    raise CommandError(ErrorCode.INVALID_COMMAND, f"Unknown key {name!r} in {text!r}")
