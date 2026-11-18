import pytest

from bridge.device import CommandError
from bridge.keys import Key, chord


def test_a_single_key():
    assert chord("enter") == [Key(0x0D)]
    assert chord("Escape") == [Key(0x1B)]
    assert chord("f5") == [Key(0x74)]
    assert chord("f24") == [Key(0x87)]


def test_letters_and_digits_are_their_capitals():
    assert chord("a") == [Key(ord("A"))]
    assert chord("7") == [Key(ord("7"))]


def test_a_chord_holds_modifiers_in_order():
    assert chord("ctrl+shift+t") == [Key(0x11), Key(0x10), Key(ord("T"))]
    assert chord(" Alt + F4 ") == [Key(0x12), Key(0x73)]


def test_arrows_and_the_windows_key_are_extended():
    assert chord("up") == [Key(0x26, extended=True)]
    assert chord("win") == [Key(0x5B, extended=True)]
    assert chord("win+d") == [Key(0x5B, extended=True), Key(ord("D"))]


def test_punctuation_is_left_to_the_layout():
    assert chord("ctrl+/") == [Key(0x11), Key(None, char="/")]


def test_plus_and_minus_spelled_out_zoom():
    assert chord("ctrl+plus") == chord("ctrl+=")
    assert chord("ctrl+minus") == chord("ctrl+-")


@pytest.mark.parametrize("text", ["", "+", "ctrl+", "ctrl++", "a+b", "ctrl+ctrl+a", "hyper", "ctrl+é", "lock"])
def test_not_keys(text):
    with pytest.raises(CommandError) as raised:
        chord(text)
    assert raised.value.code == "invalid_command"
