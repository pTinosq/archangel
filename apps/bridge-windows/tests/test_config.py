import json

from bridge.config import Relay, Settings, Store


def test_missing_settings_are_the_defaults_named_after_the_computer(tmp_path, monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "DESKTOP-4F2K")
    settings = Store(tmp_path / "bridge.json").load()
    assert settings == Settings(device="desktop-4f2k")
    assert settings.relay == Relay()


def test_settings_round_trip_with_the_secret_protected(tmp_path):
    store = Store(tmp_path / "bridge.json", protect=lambda data: data[::-1], unprotect=lambda data: data[::-1])
    settings = Settings("dev", Relay("ws://mac:8080/ws/device", "dev-token"), Relay("wss://relay", "t"), "desk", "1234")
    store.save(settings)
    assert store.load() == settings
    assert "1234" not in (tmp_path / "bridge.json").read_text()


def test_the_relay_is_the_current_modes():
    settings = Settings("dev", Relay("ws://dev"), Relay("wss://prod"))
    assert settings.relay == Relay("ws://dev")
    assert settings.with_relay(Relay("ws://new")).dev == Relay("ws://new")


def test_no_secret_is_saved_as_none(tmp_path):
    Store(tmp_path / "bridge.json").save(Settings(device="desk"))
    assert json.loads((tmp_path / "bridge.json").read_text())["secret"] == ""


def test_logging_carries_on_where_it_cant_write(tmp_path):
    import logging

    from bridge.app import start_logging

    blocked = tmp_path / "file"
    blocked.write_text("")
    start_logging(blocked / "logs" / "bridge.log")  # a file where the folder should be
    logging.getLogger("bridge").info("still fine")
