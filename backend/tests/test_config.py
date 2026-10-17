import pytest

from clearinghouse import config

FAKE = {name: f"FAKEVALUE_{i}_zq9x" for i, name in enumerate(config.KEY_NAMES)}


@pytest.fixture
def no_env_file(monkeypatch, tmp_path):
    """Isolate from the real repo .env and real environment."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "nonexistent.env")
    for name in config.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_key_status_only_set_or_missing(no_env_file, monkeypatch):
    for name in ("EIA_API_KEY", "ERCOT_USERNAME"):
        monkeypatch.setenv(name, FAKE[name])
    status = config.key_status()
    assert set(status) == set(config.KEY_NAMES)
    assert set(status.values()) <= {"set", "missing"}
    assert status["EIA_API_KEY"] == "set"
    assert status["ERCOT_USERNAME"] == "set"
    assert status["CHALK_CLIENT_ID"] == "missing"
    for value in FAKE.values():
        assert value not in repr(status)


def test_real_env_status_is_names_only():
    # Uses the real .env if present; values must never appear in the output.
    status = config.key_status()
    assert set(status.values()) <= {"set", "missing"}


def test_cli_and_repr_never_leak_values(no_env_file, monkeypatch, capsys):
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)
    config.main()
    out = capsys.readouterr().out
    settings = config.get_settings()
    rendered = out + repr(settings) + str(settings) + repr(config.key_status())
    for value in FAKE.values():
        assert value not in rendered
    for name in config.KEY_NAMES:
        assert f"{name}: set" in out


def test_env_file_loaded(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("EIA_API_KEY=FAKEFROMFILE_abc\nCHALK_CLIENT_ID=\n")
    for name in config.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    settings = config.load_settings(env)
    assert settings.key_status()["EIA_API_KEY"] == "set"
    assert settings.key_status()["CHALK_CLIENT_ID"] == "missing"
    assert "FAKEFROMFILE_abc" not in repr(settings)


def test_chalk_configured_requires_all(no_env_file, monkeypatch):
    assert config.chalk_configured() is False
    monkeypatch.setenv("CHALK_CLIENT_ID", "x")
    monkeypatch.setenv("CHALK_CLIENT_SECRET", "y")
    assert config.chalk_configured() is False
    monkeypatch.setenv("CHALK_ENVIRONMENT", "   ")
    assert config.chalk_configured() is False
    monkeypatch.setenv("CHALK_ENVIRONMENT", "z")
    assert config.chalk_configured() is True
