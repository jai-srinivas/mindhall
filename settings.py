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
