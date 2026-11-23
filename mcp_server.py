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

# Worded as facts the user chose to save, not instructions for the AI: the Claude and ChatGPT
# directories refuse servers that steer an AI's behaviour (Claude policy 2F, OpenAI 6.4).
INSTRUCTIONS = (
    "Mindhall is a memory the user shares across their AI apps (Hermes agents, Claude, ChatGPT and others): "
    "their projects, dates and the facts and preferences they chose to save. "
    "When the user asks about their work, projects, dates or preferences, read it with mindhall_read. "
    "If something there differs from what you remember, point out the difference and check with the user. "
    "When the user asks you to remember something, or tells you about a change to their projects or dates, "
    "save it with the mindhall tools and confirm briefly. Save only what the user actually said."
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
# WARNING keeps the per-request access log off: every request path contains the
# secret, and the web page polls every few seconds, so INFO would write it to the logs.
mcp = MCPServer(name="Mindhall", instructions=INSTRUCTIONS, log_level="WARNING")


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


def _guard(ctx) -> None:
    """The first thing every AI tool does, reads included: stop if the person switched this app off."""
    _do(lambda: mind.ensure_on(app_name(ctx)))


def _save(call):
    """Run an engine write for an AI, unless the person has paused saving."""
    return _do(lambda: (mind.ensure_saving(), call())[1])


@mcp.tool(title="Read Mindhall", annotations=ToolAnnotations(title='Read Mindhall', readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def mindhall_read(ctx: Context, room: str = "all", include_past: bool = False, limit: int = 20,
                  project: str = "") -> dict:
    """Read the user's shared mind (Mindhall): About you, Work, Dates and the Change Log.

    room: one of all, about, work, dates, changes (default all).
    include_past: for dates, also return past dates.
    limit: for changes, how many to return (max 200).
    project: optional, a project's name: returns that project's page (its next steps, its own
    memories, its dates and its history).
    """
    _guard(ctx)  # before the read receipt: a switched-off app hasn't read anything
    mind.mark_read(app_name(ctx))  # read receipts: the dashboard shows when each app last read the mind
    if (project or "").strip():
        return _do(lambda: mind.project(name=project, history=20))
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


@mcp.tool(title="Update work", annotations=ToolAnnotations(title='Update work', readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def mindhall_update_work(name: str, ctx: Context, status: str | None = None, next_step: str | None = None,
                         steps: list[str] | None = None) -> dict:
    """Add a project, client or course to the user's Work, or change its status or next steps.

    status: Active, Parked or Done. Use the exact name already in Mindhall when updating.
    next_step: only the first next step (up next); the later steps are kept.
    steps: up to 3 next steps in order, the first is up next; replaces the list.
    """
    _guard(ctx)
    return _save(lambda: mind.set_work(name, app_name(ctx), status, next_step, steps))


@mcp.tool(title="Add or move a date", annotations=ToolAnnotations(title='Add or move a date', readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def mindhall_set_date(day: str, ctx: Context, what: str = "", work: str = "", id: int | None = None) -> dict:
    """Add a date, or move an existing one. A date with the same name (in the same work) is moved, not duplicated.

    day: the date as YYYY-MM-DD. what: what happens that day, e.g. "Launch".
    work: optional, the work it belongs to. id: optional, the id of the date to change.
    """
    _guard(ctx)
    return _save(lambda: mind.set_date(what, day, app_name(ctx), work, id))


@mcp.tool(title="Add a note", annotations=ToolAnnotations(title='Add a note', readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def mindhall_add_note(note: str, ctx: Context, work: str = "") -> dict:
    """Save a fact or preference the user wants remembered to About you, e.g. where they're based or how they like to work.
    With work (a project's exact name), save it as that project's own memory instead.
    Only what the user said."""
    _guard(ctx)
    return _save(lambda: mind.add_note(note, app_name(ctx), work or None))


@mcp.tool(title="Remove a note or date", annotations=ToolAnnotations(title='Remove a note or date', readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def mindhall_remove(room: str, id: int, ctx: Context) -> dict:
    """Remove a note (room "about"), a date (room "dates") or a project's memory (room "project_notes")
    by id. Only when the user asks. The user can restore it from the dashboard for 30 days."""
    _guard(ctx)
    room = (room or "").lower()
    if room == "about":
        return _save(lambda: mind.remove_note(id, app_name(ctx)))
    if room == "dates":
        return _save(lambda: mind.remove_date(id, app_name(ctx)))
    if room == "project_notes":
        return _save(lambda: mind.remove_project_note(id, app_name(ctx)))
    raise ToolError("Room must be 'about', 'dates' or 'project_notes'.")


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

    async def context(request):
        return {"text": engine.render_context(mind.snapshot())}  # what every AI is given each turn

    async def project(request):
        return mind.project(work_id=request.path_params["item"])

    async def create(request):
        kind, data = request.path_params["kind"], await _body(request)
        if kind == "note":
            return mind.add_note(data.get("note"), WEB_NAME)
        if kind == "project-note":
            if not str(data.get("work") or "").strip():
                raise engine.MindhallError("Say which project this memory belongs to.")
            return mind.add_note(data.get("note"), WEB_NAME, data.get("work"))
        if kind == "work":
            return mind.set_work(data.get("name"), WEB_NAME, data.get("status"), data.get("next_step"),
                                 data.get("steps"))
        if kind == "date":
            return mind.add_date(data.get("what"), data.get("day"), WEB_NAME, data.get("work") or "")
        raise engine.MindhallError("Unknown table.")

    async def change(request):
        kind, item, data = request.path_params["kind"], request.path_params["item"], await _body(request)
        if kind == "note":
            return mind.edit_note(item, data.get("note"), WEB_NAME)
        if kind == "project-note":
            return mind.edit_project_note(item, WEB_NAME, data.get("note"), data.get("work"))
        if kind == "work":
            return mind.edit_work(item, WEB_NAME, data.get("name"), data.get("status"), data.get("next_step"),
                                  data.get("steps"))
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
        if kind == "project-note":
            return mind.remove_project_note(item, WEB_NAME)
        if kind == "work":
            return mind.remove_work(item, WEB_NAME)
        if kind == "date":
            return mind.remove_date(item, WEB_NAME)
        raise engine.MindhallError("Unknown table.")

    async def settings(request):
        return mind.settings()

    async def change_settings(request):
        data = await _body(request)
        if "time_zone" not in data and "paused" not in data:
            raise engine.MindhallError("Nothing to change.")
        if "paused" in data and not isinstance(data["paused"], bool):
            raise engine.MindhallError("paused is true or false.")
        result = mind.settings()
        if "time_zone" in data:
            result = mind.set_time_zone(data.get("time_zone"))
        if "paused" in data:
            result = mind.set_paused(data["paused"])
        return result

    async def ai_switch(request):
        """The person switching one AI app off (no reading or saving at all) or back on."""
        data = await _body(request)
        if not isinstance(data.get("on"), bool):
            raise engine.MindhallError("on is true or false.")
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            raise engine.MindhallError("Say which AI app to switch on or off.")
        return mind.set_ai_on(data["name"], data["on"], WEB_NAME)

    async def clear(request):
        data = await _body(request)
        if str(data.get("confirm", "")).strip().lower() != "clear":
            raise engine.MindhallError('Type "clear" to confirm.')
        return mind.clear(WEB_NAME)

    async def undo(request):
        return mind.undo(request.path_params["item"], WEB_NAME)

    async def restore(request):
        return mind.restore(request.path_params["item"], WEB_NAME)

    async def forget(request):
        return mind.forget(request.path_params["item"], WEB_NAME)

    mcp.custom_route(base + "/api/mind", methods=["GET"], include_in_schema=False)(_web(state))
    mcp.custom_route(base + "/api/context", methods=["GET"], include_in_schema=False)(_web(context))
    mcp.custom_route(base + "/api/project/{item:int}", methods=["GET"], include_in_schema=False)(_web(project))
    mcp.custom_route(base + "/api/settings", methods=["GET"], include_in_schema=False)(_web(settings))
    mcp.custom_route(base + "/api/settings", methods=["PATCH"], include_in_schema=False)(_web(change_settings))
    mcp.custom_route(base + "/api/clear", methods=["POST"], include_in_schema=False)(_web(clear))
    mcp.custom_route(base + "/api/ai-switch", methods=["POST"], include_in_schema=False)(_web(ai_switch))
    mcp.custom_route(base + "/api/undo/{item:int}", methods=["POST"], include_in_schema=False)(_web(undo))
    mcp.custom_route(base + "/api/restore/{item:int}", methods=["POST"], include_in_schema=False)(_web(restore))
    mcp.custom_route(base + "/api/forget/{item:int}", methods=["POST"], include_in_schema=False)(_web(forget))
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
