"""Mindhall for Hermes: one shared mind for every AI you use.

This is the Hermes plugin adapter. All storage and logic live in
engine.py, so the MCP server and a future memory provider reuse it.
"""
import json
import logging

from . import engine, settings

logger = logging.getLogger(__name__)

RULES = (
    "Mindhall is the user's shared mind, used by all of their AIs. "
    "Each user message may include a Mindhall block: treat it as the source of truth. "
    "If it disagrees with your own memory, Mindhall is right. "
    "When the user tells you something that changes their work, dates or preferences, "
    "save it with the mindhall tools in the same turn, then confirm briefly. "
    "Only save what the user actually said. Never invent entries."
)


def _ok(data):
    return json.dumps({"success": True, **data})


def _err(message):
    return json.dumps({"success": False, "error": message})


def register(ctx):
    # One gateway serves every profile, and this plugin may be set up once for
    # all of them. So the profile is looked up when each request runs: a Money
    # turn reads Money's .env and mind, a demo turn the demo's. Cached per profile.
    cache = {}

    def current():
        home = settings.profile_home()  # the profile this request runs under
        if home is None:
            home = getattr(getattr(ctx, "_manager", None), "home_path", None)
        key = str(home)
        if key not in cache:
            cfg = settings.resolve(home) if home is not None else settings.resolve()
            cache[key] = (cfg, engine.Mind(cfg["db"]))
            logger.info("mindhall: profile %s signs as %r, mind at %s", cfg["profile"], cfg["agent"], cfg["db"])
        return cache[key]

    class _CurrentMind:
        """Stands in for the current profile's mind on every call."""

        def __getattr__(self, attr):
            return getattr(current()[1], attr)

    mind = _CurrentMind()

    def agent_name():
        # Per profile: MINDHALL_AGENT_NAME in the profile's own .env file,
        # otherwise "Hermes <Profile>" (plain "Hermes" for the default profile).
        return current()[0]["agent"]

    def tool(name, description, properties, required, fn):
        schema = {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        }

        def handler(params, **kwargs):
            try:
                return _ok(fn(params or {}))
            except engine.MindhallError as e:
                return _err(str(e))
            except Exception:
                logger.exception("mindhall tool %s failed", name)
                return _err("Mindhall could not complete that. Nothing was changed.")

        ctx.register_tool(name=name, toolset="mindhall", schema=schema, handler=handler)

    # ---------- Tools ----------

    def read(p):
        room = (p.get("room") or "all").lower()
        if room == "about":
            return {"about": mind.snapshot()["about"]}
        if room == "work":
            return {"work": mind.snapshot()["work"]}
        if room == "dates":
            return {"dates": mind.all_dates(include_past=bool(p.get("include_past")))}
        if room == "changes":
            return {"changes": mind.recent_changes(p.get("limit") or 20)}
        return mind.snapshot(days_ahead=30, changes=10)

    tool(
        "mindhall_read",
        "Read the user's shared mind (Mindhall). Use it when the context block is not enough, "
        "for example to see all dates, Done work or a longer change history.",
        {
            "room": {"type": "string", "enum": ["all", "about", "work", "dates", "changes"],
                     "description": "Which part to read. Default: all."},
            "include_past": {"type": "boolean", "description": "For dates: include past dates."},
            "limit": {"type": "integer", "description": "For changes: how many to return (max 200)."},
        },
        [],
        read,
    )

    tool(
        "mindhall_update_work",
        "Add a project, client or course to the user's Work, or change its status or next step. "
        "Use the exact name already in Mindhall when updating.",
        {
            "name": {"type": "string", "description": "Name of the work, e.g. 'Mindhall hackathon entry'."},
            "status": {"type": "string", "enum": list(engine.STATUSES)},
            "next_step": {"type": "string", "description": "The single next step, in a few words."},
        },
        ["name"],
        lambda p: mind.set_work(p.get("name"), agent_name(), p.get("status"), p.get("next_step")),
    )

    tool(
        "mindhall_set_date",
        "Add a date, or move an existing one. A date with the same name is moved, not duplicated.",
        {
            "what": {"type": "string", "description": "What happens on that day, e.g. 'Launch'."},
            "day": {"type": "string", "description": "The date as YYYY-MM-DD."},
            "work": {"type": "string", "description": "Optional: the work it belongs to."},
            "id": {"type": "integer", "description": "Optional: id of the date to change."},
        },
        ["day"],
        lambda p: mind.set_date(p.get("what"), p.get("day"), agent_name(), p.get("work") or "", p.get("id")),
    )

    tool(
        "mindhall_add_note",
        "Save a lasting fact or rule about the user to About you, "
        "e.g. how they like answers written. Only what the user said.",
        {"note": {"type": "string", "description": "One short sentence."}},
        ["note"],
        lambda p: mind.add_note(p.get("note"), agent_name()),
    )

    def remove(p):
        room = (p.get("room") or "").lower()
        if room == "about":
            return mind.remove_note(int(p["id"]), agent_name())
        if room == "dates":
            return mind.remove_date(int(p["id"]), agent_name())
        raise engine.MindhallError("Room must be 'about' or 'dates'.")

    tool(
        "mindhall_remove",
        "Remove a note from About you or a date from Dates, by id. Only when the user asks.",
        {
            "room": {"type": "string", "enum": ["about", "dates"]},
            "id": {"type": "integer"},
        },
        ["room", "id"],
        remove,
    )

    # ---------- Per-turn context ----------

    def inject(**kwargs):
        # Runs every turn, so it must be fast and must never break the agent.
        try:
            if mind.is_empty():
                return None
            return {"context": engine.render_context(mind.snapshot())}
        except Exception:
            logger.exception("mindhall context injection failed")
            return None

    ctx.register_hook("pre_llm_call", inject)

    # ---------- Standing rule (older Hermes versions may not have this) ----------

    if hasattr(ctx, "register_system_prompt_section"):
        try:
            ctx.register_system_prompt_section("mindhall.rules", RULES, position="after_memory", max_chars=1200)
        except Exception:
            logger.exception("mindhall could not register its rules section")

    # ---------- /mindhall ----------

    def show(args="", **kwargs):
        try:
            summary = engine.render_summary(mind.snapshot(days_ahead=30, changes=10))
            cfg = current()[0]
            return f"{summary}\n\nProfile: {cfg['profile']}. This agent signs as {cfg['agent']}."
        except Exception:
            logger.exception("mindhall /mindhall failed")
            return "Mindhall could not be read right now."

    ctx.register_command("mindhall", show, "Show what's in your shared mind")
