"""Mindhall MCP server: connects Claude, ChatGPT, Cursor and other AI apps
to the same mind your Hermes agents use.

It reuses engine.py and the same database file, so a date Hermes saves is
the date Claude sees, and the other way round. Every change is signed with
the name of the app that made it (each app introduces itself when it
connects), so the Change Log shows "Claude: Moved Launch to Nov 20".

Run it:
    MINDHALL_MCP_SECRET=<long random string> python3 mcp_server.py

It listens on 127.0.0.1:8765 by default. The address an AI app connects to
contains the secret: https://<your-public-address>/<secret>/mcp
Anyone without the secret gets a 404. Keep the full address private.
"""
from __future__ import annotations

import os
import sys

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

import engine

INSTRUCTIONS = (
    "Mindhall is the user's shared mind, used by all of their AIs (Hermes agents, Claude, ChatGPT and others). "
    "Before answering anything about the user's work, projects, dates or preferences, read it with mindhall_read. "
    "If it disagrees with what you remember, Mindhall is right. "
    "When the user tells you something that changes their work, dates or preferences, save it with the "
    "mindhall tools in the same turn and confirm briefly. Only save what the user actually said."
)

# How connecting apps identify themselves, mapped to friendly names for the Change Log.
# More specific names first: "claude-code" must match before "claude".
APP_NAMES = (
    ("claude-code", "Claude Code"),
    ("claude", "Claude"),
    ("chatgpt", "ChatGPT"),
    ("openai", "ChatGPT"),
    ("cursor", "Cursor"),
    ("codex", "Codex"),
    ("gemini", "Gemini"),
)

mind = engine.Mind()
mcp = MCPServer(name="Mindhall", instructions=INSTRUCTIONS)


def app_name(ctx: Context | None) -> str:
    override = os.environ.get("MINDHALL_MCP_AGENT_NAME", "").strip()
    if override:
        return override
    raw = ""
    try:
        params = ctx.session.client_params if ctx is not None else None
        info = getattr(params, "client_info", None) or getattr(params, "clientInfo", None)
        raw = (getattr(info, "name", "") or "").strip()
    except Exception:
        raw = ""
    low = raw.lower()
    for key, nice in APP_NAMES:
        if key in low:
            return nice
    return raw or "AI app"


def _do(call):
    """Run an engine call; turn its refusals into readable tool errors for the AI."""
    try:
        return call()
    except engine.MindhallError as e:
        raise ToolError(str(e)) from None


@mcp.tool()
def mindhall_read(room: str = "all", include_past: bool = False, limit: int = 20) -> dict:
    """Read the user's shared mind (Mindhall): About you, Work, Dates and the Change Log.

    room: one of all, about, work, dates, changes (default all).
    include_past: for dates, also return past dates.
    limit: for changes, how many to return (max 200).
    """
    room = (room or "all").lower()
    if room == "about":
        return {"about": mind.snapshot()["about"]}
    if room == "work":
        return {"work": mind.snapshot()["work"]}
    if room == "dates":
        return {"dates": mind.all_dates(include_past=include_past)}
    if room == "changes":
        return {"changes": mind.recent_changes(limit)}
    return mind.snapshot(days_ahead=30, changes=10)


@mcp.tool()
def mindhall_update_work(name: str, ctx: Context, status: str | None = None, next_step: str | None = None) -> dict:
    """Add a project, client or course to the user's Work, or change its status or next step.

    status: Active, Parked or Done. Use the exact name already in Mindhall when updating.
    """
    return _do(lambda: mind.set_work(name, app_name(ctx), status, next_step))


@mcp.tool()
def mindhall_set_date(day: str, ctx: Context, what: str = "", work: str = "", id: int | None = None) -> dict:
    """Add a date, or move an existing one. A date with the same name is moved, not duplicated.

    day: the date as YYYY-MM-DD. what: what happens that day, e.g. "Launch".
    work: optional, the work it belongs to. id: optional, the id of the date to change.
    """
    return _do(lambda: mind.set_date(what, day, app_name(ctx), work, id))


@mcp.tool()
def mindhall_add_note(note: str, ctx: Context) -> dict:
    """Save a lasting fact or rule about the user to About you, e.g. how they like answers written.
    Only what the user said."""
    return _do(lambda: mind.add_note(note, app_name(ctx)))


@mcp.tool()
def mindhall_remove(room: str, id: int, ctx: Context) -> dict:
    """Remove a note (room "about") or a date (room "dates") by id. Only when the user asks."""
    room = (room or "").lower()
    if room == "about":
        return _do(lambda: mind.remove_note(id, app_name(ctx)))
    if room == "dates":
        return _do(lambda: mind.remove_date(id, app_name(ctx)))
    raise ToolError("Room must be 'about' or 'dates'.")


def main() -> None:
    secret = os.environ.get("MINDHALL_MCP_SECRET", "").strip()
    if len(secret) < 24 or "/" in secret:
        sys.exit(
            "Set MINDHALL_MCP_SECRET to a long random string (24+ characters, no slashes).\n"
            "Make one with: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    host = os.environ.get("MINDHALL_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MINDHALL_MCP_PORT", "8765"))
    print(f"Mindhall MCP server on http://{host}:{port}/<secret>/mcp (database: {mind.path})", file=sys.stderr)
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path=f"/{secret}/mcp",
        # The server only listens on localhost and is reached through a tunnel,
        # whose public hostname would fail the default Host-header check.
        # Access is protected by the secret in the path instead.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    main()
