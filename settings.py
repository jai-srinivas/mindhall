"""Per-profile settings for the Mindhall Hermes plugin.

Settings come from the profile's own .env, read through Hermes's public
profile-aware route (agent.secret_scope), never by opening .env files:

    MINDHALL_AGENT_NAME   the name this profile signs changes with
    MINDHALL_DB           where this profile's mind is stored

Defaults: profiles sign as "Hermes <Profile>" (the default profile as plain
"Hermes"), and every profile shares one mind at ~/.mindhall/mindhall.db.
Outside Hermes (tests, scripts) the process environment is used instead.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DB = Path.home() / ".mindhall" / "mindhall.db"


def get_setting(name: str) -> str | None:
    """A setting from the current profile's scope (Hermes), else the process environment."""
    try:
        from agent.secret_scope import get_secret  # public Hermes plugin API
    except Exception:
        return os.environ.get(name) or None
    try:
        return get_secret(name, None) or None
    except Exception:  # e.g. no profile scope active; fall back to defaults
        return None


def scoped_home() -> Path | None:
    """The home of the profile whose turn is running, as stamped on Hermes's per-turn scope."""
    try:
        from agent.secret_scope import current_secret_scope_home  # public Hermes plugin API

        home = current_secret_scope_home()
        return Path(home).expanduser() if home else None
    except Exception:
        return None


def ambient_home() -> Path | None:
    """The profile Hermes reports as active, or None outside Hermes."""
    try:
        from hermes_constants import get_hermes_home  # public Hermes plugin API

        return Path(get_hermes_home()).expanduser()
    except Exception:
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else None


def profile_name(home: Path | None) -> str:
    """'money' for <root>/profiles/money, 'default' for the root, '' when unknown."""
    if home is None:
        return ""
    return home.name if home.parent.name == "profiles" else "default"


def default_agent_name(profile: str) -> str:
    if profile in ("", "default"):
        return "Hermes"
    return "Hermes " + profile.replace("-", " ").replace("_", " ").title()


def resolve(home: Path | None) -> dict:
    """This profile's name, mind and signature."""
    profile = profile_name(home)
    db = Path(get_setting("MINDHALL_DB") or DEFAULT_DB).expanduser()
    agent = (get_setting("MINDHALL_AGENT_NAME") or "").strip() or default_agent_name(profile)
    return {"profile": profile or "default", "db": db, "agent": agent}


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
