"""Per-profile settings tests. No Hermes install needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings  # noqa: E402


def make_profile(root: Path, name: str | None, env: str = "") -> Path:
    home = root / ".hermes" if name is None else root / ".hermes" / "profiles" / name
    home.mkdir(parents=True)
    if env:
        (home / ".env").write_text(env)
    return home


def test_profiles_sign_with_their_own_name(tmp_path):
    assert settings.resolve(make_profile(tmp_path, "money"))["agent"] == "Hermes Money"
    assert settings.resolve(make_profile(tmp_path, "mindhall-demo"))["agent"] == "Hermes Mindhall Demo"


def test_default_profile_signs_as_plain_hermes(tmp_path):
    cfg = settings.resolve(make_profile(tmp_path, None))
    assert cfg["profile"] == "default"
    assert cfg["agent"] == "Hermes"


def test_profile_env_sets_name_and_mind(tmp_path):
    home = make_profile(tmp_path, "time", 'export MINDHALL_AGENT_NAME="Hermes Time"\nMINDHALL_DB=/tmp/x/time.db\n')
    cfg = settings.resolve(home)
    assert cfg["agent"] == "Hermes Time"
    assert cfg["db"] == Path("/tmp/x/time.db")


def test_one_profiles_env_never_leaks_into_another(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDHALL_DB", "/tmp/leaked.db")
    monkeypatch.setenv("MINDHALL_AGENT_NAME", "Leaked")
    cfg = settings.resolve(make_profile(tmp_path, "growth"))
    assert cfg["agent"] == "Hermes Growth"
    assert cfg["db"] == settings.DEFAULT_DB


def test_comments_and_blank_lines_are_ignored(tmp_path):
    home = make_profile(tmp_path, "money", "# a comment\n\nMINDHALL_AGENT_NAME='Money Bot'\nOTHER=1\n")
    assert settings.resolve(home)["agent"] == "Money Bot"
