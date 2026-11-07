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
from pathlib import Path

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

try:
    from mcp.types import ToolAnnotations
except ImportError:  # mcp 2.x splits the types into their own package
    from mcp_types import ToolAnnotations

import engine
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

WEB_PAGE = Path(__file__).resolve().parent / "web" / "index.html"
WEB_NAME = os.environ.get("MINDHALL_WEB_NAME", "You").strip() or "You"

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


@mcp.tool(title="Read Mindhall", annotations=ToolAnnotations(title='Read Mindhall', readOnlyHint=True, openWorldHint=False))
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


@mcp.tool(title="Update work", annotations=ToolAnnotations(title='Update work', readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def mindhall_update_work(name: str, ctx: Context, status: str | None = None, next_step: str | None = None) -> dict:
    """Add a project, client or course to the user's Work, or change its status or next step.

    status: Active, Parked or Done. Use the exact name already in Mindhall when updating.
    """
    return _do(lambda: mind.set_work(name, app_name(ctx), status, next_step))


@mcp.tool(title="Add or move a date", annotations=ToolAnnotations(title='Add or move a date', readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def mindhall_set_date(day: str, ctx: Context, what: str = "", work: str = "", id: int | None = None) -> dict:
    """Add a date, or move an existing one. A date with the same name is moved, not duplicated.

    day: the date as YYYY-MM-DD. what: what happens that day, e.g. "Launch".
    work: optional, the work it belongs to. id: optional, the id of the date to change.
    """
    return _do(lambda: mind.set_date(what, day, app_name(ctx), work, id))


@mcp.tool(title="Add a note", annotations=ToolAnnotations(title='Add a note', readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def mindhall_add_note(note: str, ctx: Context) -> dict:
    """Save a lasting fact or rule about the user to About you, e.g. how they like answers written.
    Only what the user said."""
    return _do(lambda: mind.add_note(note, app_name(ctx)))


@mcp.tool(title="Remove a note or date", annotations=ToolAnnotations(title='Remove a note or date', readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def mindhall_remove(room: str, id: int, ctx: Context) -> dict:
    """Remove a note (room "about") or a date (room "dates") by id. Only when the user asks."""
    room = (room or "").lower()
    if room == "about":
        return _do(lambda: mind.remove_note(id, app_name(ctx)))
    if room == "dates":
        return _do(lambda: mind.remove_date(id, app_name(ctx)))
    raise ToolError("Room must be 'about' or 'dates'.")


# ---------- Web tables: the page a person uses to read and edit the mind ----------

_NO_LEAK = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",  # the address holds the secret: never send it on
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}


def _json(data, status=200):
    return JSONResponse(data, status_code=status, headers=_NO_LEAK)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _web(handler):
    async def wrapped(request: Request):
        try:
            return _json(await handler(request))
        except engine.MindhallError as e:
            return _json({"error": str(e)}, 400)
        except Exception:
            return _json({"error": "Mindhall could not complete that. Nothing was changed."}, 500)
    return wrapped


def add_web_routes(secret: str) -> None:
    base = f"/{secret}"

    @mcp.custom_route(base, methods=["GET"], include_in_schema=False)
    async def to_page(request: Request):
        return RedirectResponse(base + "/", headers=_NO_LEAK)

    @mcp.custom_route(base + "/", methods=["GET"], include_in_schema=False)
    async def page(request: Request):
        return HTMLResponse(WEB_PAGE.read_text(encoding="utf-8"), headers=_NO_LEAK)

    async def state(request):
        return mind.everything()

    async def create(request):
        kind, data = request.path_params["kind"], await _body(request)
        if kind == "note":
            return mind.add_note(data.get("note"), WEB_NAME)
        if kind == "work":
            return mind.set_work(data.get("name"), WEB_NAME, data.get("status"), data.get("next_step"))
        if kind == "date":
            return mind.set_date(data.get("what"), data.get("day"), WEB_NAME, data.get("work") or "")
        raise engine.MindhallError("Unknown table.")

    async def change(request):
        kind, item, data = request.path_params["kind"], request.path_params["item"], await _body(request)
        if kind == "note":
            return mind.edit_note(item, data.get("note"), WEB_NAME)
        if kind == "work":
            return mind.edit_work(item, WEB_NAME, data.get("name"), data.get("status"), data.get("next_step"))
        if kind == "date":
            current = next((d for d in mind.all_dates(include_past=True) if d["id"] == item), None)
            if current is None:
                raise engine.MindhallError(f"There is no date with id {item}.")
            return mind.set_date(data.get("what", current["what"]), data.get("day", current["day"]), WEB_NAME,
                                 data.get("work", current["work"]), item)
        raise engine.MindhallError("Unknown table.")

    async def remove(request):
        kind, item = request.path_params["kind"], request.path_params["item"]
        if kind == "note":
            return mind.remove_note(item, WEB_NAME)
        if kind == "work":
            return mind.remove_work(item, WEB_NAME)
        if kind == "date":
            return mind.remove_date(item, WEB_NAME)
        raise engine.MindhallError("Unknown table.")

    async def undo(request):
        return mind.undo(request.path_params["item"], WEB_NAME)

    mcp.custom_route(base + "/api/mind", methods=["GET"], include_in_schema=False)(_web(state))
    mcp.custom_route(base + "/api/undo/{item:int}", methods=["POST"], include_in_schema=False)(_web(undo))
    mcp.custom_route(base + "/api/{kind}", methods=["POST"], include_in_schema=False)(_web(create))
    mcp.custom_route(base + "/api/{kind}/{item:int}", methods=["PATCH"], include_in_schema=False)(_web(change))
    mcp.custom_route(base + "/api/{kind}/{item:int}", methods=["DELETE"], include_in_schema=False)(_web(remove))


def main() -> None:
    secret = os.environ.get("MINDHALL_MCP_SECRET", "").strip()
    if len(secret) < 24 or "/" in secret:
        sys.exit(
            "Set MINDHALL_MCP_SECRET to a long random string (24+ characters, no slashes).\n"
            "Make one with: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    host = os.environ.get("MINDHALL_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MINDHALL_MCP_PORT", "8765"))
    # With a permanent address, set MINDHALL_MCP_ALLOWED_HOSTS (e.g. mcp.getmindhall.com)
    # to have every request's Host and Origin headers checked. Quick tunnels change
    # address on every start, so the check is off unless hosts are given; the secret
    # in the path protects access either way.
    hosts = [h.strip() for h in os.environ.get("MINDHALL_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts),
        allowed_hosts=hosts,
        allowed_origins=[f"https://{h}" for h in hosts],
    )
    add_web_routes(secret)
    print(f"Mindhall tables on http://{host}:{port}/<secret>/", file=sys.stderr)
    print(f"Mindhall MCP server on http://{host}:{port}/<secret>/mcp (database: {mind.path})", file=sys.stderr)
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path=f"/{secret}/mcp",
        transport_security=security,
    )


if __name__ == "__main__":
    main()
