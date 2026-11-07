"""Per-profile settings tests. No Hermes install needed: outside Hermes,
settings come from the process environment."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings  # noqa: E402


def test_profiles_sign_with_their_own_name(tmp_path, monkeypatch):
    monkeypatch.delenv("MINDHALL_AGENT_NAME", raising=False)
    assert settings.resolve(tmp_path / ".hermes" / "profiles" / "money")["agent"] == "Hermes Money"
    assert settings.resolve(tmp_path / ".hermes" / "profiles" / "mindhall-demo")["agent"] == "Hermes Mindhall Demo"


def test_default_profile_signs_as_plain_hermes(tmp_path, monkeypatch):
    monkeypatch.delenv("MINDHALL_AGENT_NAME", raising=False)
    cfg = settings.resolve(tmp_path / ".hermes")
    assert cfg["profile"] == "default"
    assert cfg["agent"] == "Hermes"


def test_settings_override_name_and_mind(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDHALL_AGENT_NAME", "Hermes Time")
    monkeypatch.setenv("MINDHALL_DB", str(tmp_path / "time.db"))
    cfg = settings.resolve(tmp_path / ".hermes")
    assert cfg["agent"] == "Hermes Time"
    assert cfg["db"] == tmp_path / "time.db"


def test_unknown_profile_falls_back_to_defaults(monkeypatch):
    monkeypatch.delenv("MINDHALL_AGENT_NAME", raising=False)
    monkeypatch.delenv("MINDHALL_DB", raising=False)
    cfg = settings.resolve(None)
    assert cfg == {"profile": "default", "db": settings.DEFAULT_DB, "agent": "Hermes"}


def test_profile_names_map_to_their_home_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "hermes_root", lambda: tmp_path / ".hermes")
    assert settings.home_for_profile("default") == tmp_path / ".hermes"
    assert settings.home_for_profile("money") == tmp_path / ".hermes" / "profiles" / "money"
    assert settings.home_for_profile("../etc") == tmp_path / ".hermes"


def test_session_profiles_survive_a_restart_and_compaction(tmp_path):
    path = tmp_path / "sessions.json"
    first = settings.SessionProfiles(path)
    first.set("s1", "money")
    first.carry_over("s1-compacted", "s1")
    second = settings.SessionProfiles(path)  # gateway restarted
    assert second.get("s1") == "money"
    assert second.get("s1-compacted") == "money"
    assert second.get("unknown") is None


def test_session_profiles_keep_only_the_most_recent(tmp_path):
    sp = settings.SessionProfiles(tmp_path / "sessions.json", keep=3)
    for i in range(5):
        sp.set(f"s{i}", "money")
    assert sp.get("s0") is None and sp.get("s4") == "money"
