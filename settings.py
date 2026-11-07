"""Per-profile settings for the Mindhall Hermes plugin.

One Hermes gateway runs every profile in a single process, so environment
variables are shared between profiles and one profile's .env can leak into
another. Mindhall therefore reads its two settings straight from the
profile's own .env file instead:

    MINDHALL_AGENT_NAME   the name this profile signs changes with
    MINDHALL_DB           where this profile's mind is stored

Defaults: profiles sign as "Hermes <Profile>" (the default profile as plain
"Hermes"), and every profile shares one mind at ~/.mindhall/mindhall.db.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DB = Path.home() / ".mindhall" / "mindhall.db"


def read_dotenv(path: Path) -> dict:
    """Read KEY=VALUE lines from a .env file. Missing or unreadable files give {}."""
    values: dict = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def profile_home() -> Path | None:
    """The Hermes home of the profile loading the plugin, or None outside Hermes."""
    try:
        from hermes_constants import get_hermes_home  # provided by Hermes

        return Path(get_hermes_home()).expanduser()
    except Exception:
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else None


def profile_name(home: Path | None) -> str:
    """'money' for ~/.hermes/profiles/money, 'default' for ~/.hermes, '' outside Hermes."""
    if home is None:
        return ""
    return home.name if home.parent.name == "profiles" else "default"


def default_agent_name(profile: str) -> str:
    if profile in ("", "default"):
        return "Hermes"
    return "Hermes " + profile.replace("-", " ").replace("_", " ").title()


def resolve(home: Path | None = None) -> dict:
    """Work out this profile's name, mind and signature."""
    home = profile_home() if home is None else home
    profile = profile_name(home)
    env = read_dotenv(home / ".env") if home is not None else {}
    if home is None:  # outside Hermes, fall back to the process environment
        env = {k: v for k, v in os.environ.items() if k.startswith("MINDHALL_")}
    db = Path(env.get("MINDHALL_DB") or DEFAULT_DB).expanduser()
    agent = (env.get("MINDHALL_AGENT_NAME") or "").strip() or default_agent_name(profile)
    return {"profile": profile or "none", "db": db, "agent": agent}


def hermes_root() -> Path:
    """The folder holding the default profile and the profiles/ folder (~/.hermes)."""
    try:
        from hermes_constants import get_default_hermes_root  # provided by Hermes

        return Path(get_default_hermes_root()).expanduser()
    except Exception:
        return Path.home() / ".hermes"


def home_for_profile(name: str) -> Path:
    """~/.hermes for "default", ~/.hermes/profiles/<name> for any other profile."""
    root = hermes_root()
    if not name or name == "default" or "/" in name or name.startswith("."):
        return root
    return root / "profiles" / name


class SessionProfiles:
    """Remembers which profile each Hermes session belongs to.

    Hermes reports the true profile name when a session starts, but tool
    calls only carry the session id. Pairs are saved to a small JSON file so
    they survive gateway restarts; only the most recent ones are kept.
    """

    def __init__(self, path: Path, keep: int = 2000):
        import json
        import threading

        self._json = json
        self._lock = threading.Lock()
        self.path = Path(path)
        self.keep = keep
        try:
            self._map = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self._map, dict):
                self._map = {}
        except (OSError, ValueError):
            self._map = {}

    def get(self, session_id) -> str | None:
        return self._map.get(str(session_id)) if session_id else None

    def set(self, session_id, profile: str) -> None:
        if not session_id or not profile:
            return
        key = str(session_id)
        with self._lock:
            if self._map.get(key) == profile:
                return
            self._map.pop(key, None)
            self._map[key] = profile  # newest last, so trimming drops the oldest
            while len(self._map) > self.keep:
                self._map.pop(next(iter(self._map)))
            self._save()

    def carry_over(self, new_id, old_id) -> None:
        """A compacted session gets a new id; keep its profile."""
        profile = self.get(old_id)
        if profile:
            self.set(new_id, profile)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self._json.dumps(self._map), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # remembering is best-effort; the default profile is the fallback
