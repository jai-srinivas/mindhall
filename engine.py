"""Mindhall core engine: the shared mind every AI reads and writes.

This file has no Hermes imports, so the same engine powers the Hermes plugin,
the MCP server for Claude, ChatGPT and other apps, and the hosted version.

Rooms: About you, Projects (with their steps and own memories), Dates
and the Change Log. Every write is logged in the Change Log with who made it,
inside the same transaction, so a failed write never leaves a half-changed
mind, and every change can be undone.

The same file may also be written by older copies of this engine (other
Hermes profiles, older installs). Every schema change is therefore additive:
new tables, and new columns with defaults that older copies never need to
know about.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python older than 3.9
    ZoneInfo = None

VERSION = "0.4.0"   # matches plugin.yaml (a test checks); shown in the dashboard's Settings
STATUSES = ("Active", "Parked", "Done")
MAX_TEXT = 500      # characters per note, name, step or date label: a mind, not a document store
MAX_STEPS = 3       # next steps per project; the first is "up next"
READ_EVERY = 60     # seconds between read receipts for the same AI when nothing changed
DELETED_DAYS = 30   # how long deleted items can be restored from the dashboard
REMOVED_DAYS = 14   # how long AIs are told about removed items, so they stop using them

SCHEMA = """
CREATE TABLE IF NOT EXISTS about (
    id INTEGER PRIMARY KEY,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status TEXT NOT NULL DEFAULT 'Active',
    next_step TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dates (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    what TEXT NOT NULL,
    work TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    by TEXT NOT NULL,
    room TEXT NOT NULL,
    summary TEXT NOT NULL,
    before TEXT,
    after TEXT
);
CREATE INDEX IF NOT EXISTS idx_dates_day ON dates(day);
CREATE TABLE IF NOT EXISTS project_notes (
    id INTEGER PRIMARY KEY,
    work TEXT NOT NULL,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reads (
    by TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first release. Each has a default, so older copies that insert with
# their own column lists keep working. The `work` table above keeps its original shape, so new
# and old files reach the same shape through the same path.
SCHEMA_VERSION = 1
MIGRATIONS = (
    ("work", "later_steps", "TEXT NOT NULL DEFAULT ''"),   # JSON list of steps 2..3
)

# Each room the log uses: its table, the fields an undo compares and restores, and the field
# that links an item to a project by name (followed through later renames).
ROOMS = {
    "About you": ("about", ("note",), None),
    "Work": ("work", ("name", "status", "next_step", "later_steps"), None),       # older copies
    "Projects": ("work", ("name", "status", "next_step", "later_steps"), None),   # this version
    "Dates": ("dates", ("day", "what", "work"), "work"),
    "Project notes": ("project_notes", ("work", "note"), "work"),
}
NOUNS = {"about": "note", "work": "project", "dates": "date", "project_notes": "project memory"}
IDENTITY = {"about": ("note",), "work": ("name",), "dates": ("day", "what", "work"), "project_notes": ("work", "note")}
TEXT_FIELDS = {"about": ("note",), "work": ("name", "next_step", "later_steps"),
               "dates": ("what",), "project_notes": ("note",)}

PAUSED = ("Saving is paused: the user turned it off in Mindhall's settings. You can still read Mindhall, "
          "but don't save to it. If something should be saved, tell the user so they can resume saving.")
CLEARED_ROOM = "Mind"   # the log entry left by "Clear my mind" (never undoable: there's nothing left to bring back)
AI_ROOM = "AI apps"     # the person switching an AI app off or on: shown in Activity and undoable, never told to AIs
OFF = ("Mindhall is turned off for this app: the user switched it off in Mindhall's settings. Don't read from "
       "or save to Mindhall, and don't rely on what it said before. If you need it, ask the user to turn it "
       "back on.")

SETUP_PROMPT = (
    "Mindhall: the user's shared mind is empty, so none of their AIs know anything about them yet. "
    "If it suits the conversation, offer once to set it up in about two minutes: ask what they're "
    "working on and each project's next steps, which dates are coming up, and how they like answers "
    "written. Save each answer with the mindhall tools as you go. If they'd rather not, drop it."
)


class MindhallError(ValueError):
    """A request the mind refuses. The message is safe to show to an AI."""


def default_path() -> Path:
    raw = os.environ.get("MINDHALL_DB") or str(Path.home() / ".mindhall" / "mindhall.db")
    return Path(raw).expanduser()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _zone(name):
    """A time zone from its IANA name ("Asia/Kolkata"), or None when it's empty or unknown here."""
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(str(name))
    except Exception:  # unknown name, a path, or no time zone data on this machine
        return None


def _when(at: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _parse_day(value) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise MindhallError("Dates must be written as YYYY-MM-DD, for example 2026-11-20.")


def _text(value, what: str) -> str:
    text = (value or "").strip()
    if len(text) > MAX_TEXT:
        raise MindhallError(f"The {what} is too long. Keep it under {MAX_TEXT} characters.")
    return text


def _dump(value):
    return json.dumps(value) if value is not None else None


def _load(raw):
    try:
        value = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _status(value) -> str:
    match = next((s for s in STATUSES if s.lower() == str(value).strip().lower()), None)
    if match is None:
        raise MindhallError(f"Status must be one of: {', '.join(STATUSES)}.")
    return match


def _clean_steps(steps) -> list:
    """Blank steps dropped and repeats removed, ignoring case, keeping the order."""
    out, seen = [], set()
    for step in steps:
        step = str(step or "").strip()
        if step and step.lower() not in seen:
            seen.add(step.lower())
            out.append(step)
    return out


def _later(raw) -> list:
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(s) for s in value] if isinstance(value, list) else []


def _steps_of(row) -> list:
    """A project's next steps: next_step is step 1 (older copies only know that one)."""
    return _clean_steps([row["next_step"], *_later(row["later_steps"])])[:MAX_STEPS]


def _short(text, limit: int = 60) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _label(table: str, item: dict) -> str:
    """A removed item, briefly, for the line every AI sees."""
    if table == "about":
        return f"\"{_short(item.get('note'))}\""
    if table == "work":
        return f"project {_short(item.get('name'))}"
    if table == "dates":
        tag = f" ({_short(item.get('work'), 40)})" if item.get("work") else ""
        return f"{_short(item.get('what'))} on {item.get('day')}{tag}"
    return f"\"{_short(item.get('note'))}\" ({_short(item.get('work'), 40)})"


class Mind:
    """One shared mind, stored in a single SQLite file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()
        self._clock = lambda: datetime.now(timezone.utc)   # tests replace this
        with self._conn():
            pass  # creates the folder, file and tables on first use

    @contextmanager
    def _conn(self):
        # The folder and tables are checked on every connection, so a mind that
        # was moved or deleted while Hermes or the MCP server is running is
        # recreated cleanly, even if both processes recreate it at the same moment.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # Hermes and the MCP server can share the file
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.executescript(SCHEMA)  # every statement is IF NOT EXISTS, so this is cheap and safe
            self._migrate(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _migrate(conn):
        """Add the newer columns once. Cheap after the first time (one PRAGMA)."""
        if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return  # also leaves a file from a newer engine alone
        conn.execute("BEGIN IMMEDIATE")  # one process migrates; another waits, then finds it done
        try:
            for table, column, definition in MIGRATIONS:
                if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _log(self, c, by, room, summary, before=None, after=None):
        c.execute(
            "INSERT INTO changes (at, by, room, summary, before, after) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), by or "Unknown", room, summary, _dump(before), _dump(after)),
        )

    # ---------- About you, and each project's own memories ----------

    def add_note(self, note: str, by: str, work: str | None = None) -> dict:
        """Save a memory: about the person, or (with `work`) about one project."""
        note = _text(note, "note")
        if not note:
            raise MindhallError("The note is empty.")
        if work is not None and str(work).strip():
            return self._add_project_note(note, work, by)
        with self._conn() as c:
            # Telling two AIs the same thing should not save it twice.
            same = c.execute("SELECT id, note FROM about WHERE note = ? COLLATE NOCASE", (note,)).fetchone()
            if same is not None:
                return dict(same)
            cur = c.execute("INSERT INTO about (note, updated_at) VALUES (?, ?)", (note, _now()))
            after = {"id": cur.lastrowid, "note": note}
            self._log(c, by, "About you", f"Added note: {note}", after=after)
            return after

    def remove_note(self, note_id: int, by: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT id, note FROM about WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no note with id {note_id}.")
            c.execute("DELETE FROM about WHERE id = ?", (note_id,))
            self._log(c, by, "About you", f"Removed note: {row['note']}", before=dict(row))
            return {"removed": note_id}

    def _project_name(self, c, name) -> str:
        """The stored spelling of an existing project's name, or a refusal that lists the names."""
        name = _text(name, "work name")
        row = c.execute("SELECT name FROM work WHERE name = ?", (name,)).fetchone()
        if row is not None:
            return row["name"]
        names = [r["name"] for r in c.execute("SELECT name FROM work ORDER BY name LIMIT 30")]
        known = f" Projects: {', '.join(names)}." if names else " Add it first with mindhall_update_work."
        raise MindhallError(f"There is no project called '{name}'.{known}")

    def _add_project_note(self, note: str, work, by: str) -> dict:
        with self._conn() as c:
            name = self._project_name(c, work)
            same = c.execute("SELECT id, work, note FROM project_notes WHERE work = ? COLLATE NOCASE "
                             "AND note = ? COLLATE NOCASE", (name, note)).fetchone()
            if same is not None:
                return dict(same)
            cur = c.execute("INSERT INTO project_notes (work, note, updated_at) VALUES (?, ?, ?)",
                            (name, note, _now()))
            after = {"id": cur.lastrowid, "work": name, "note": note}
            self._log(c, by, "Project notes", f"Added note to {name}: {note}", after=after)
            return after

    def edit_project_note(self, note_id: int, by: str, note: str | None = None, work: str | None = None) -> dict:
        """Change a project memory's text, or move it to another project."""
        with self._conn() as c:
            row = c.execute("SELECT id, work, note FROM project_notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no project memory with id {note_id}.")
            before = dict(row)
            after = dict(before)
            if note is not None:
                after["note"] = _text(note, "note")
                if not after["note"]:
                    raise MindhallError("The note is empty. Delete it instead.")
            if work is not None:
                after["work"] = self._project_name(c, work)
            if after == before:
                return after
            c.execute("UPDATE project_notes SET work = ?, note = ?, updated_at = ? WHERE id = ?",
                      (after["work"], after["note"], _now(), note_id))
            if after["work"].lower() != before["work"].lower():
                summary = f"Moved note to {after['work']}: {after['note']}"
            else:
                summary = f"Edited note in {after['work']}: {after['note']}"
            self._log(c, by, "Project notes", summary, before=before, after=after)
            return after

    def remove_project_note(self, note_id: int, by: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT id, work, note FROM project_notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no project memory with id {note_id}.")
            c.execute("DELETE FROM project_notes WHERE id = ?", (note_id,))
            self._log(c, by, "Project notes", f"Removed note from {row['work']}: {row['note']}", before=dict(row))
            return {"removed": note_id}

    # ---------- Settings ----------
    # The person's preferences, not the mind's content: like read receipts, they aren't logged as changes.

    @staticmethod
    def _settings(c) -> dict:
        stored = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
        zone = stored.get("time_zone") or None
        off = sorted((v for k, v in stored.items() if k.startswith("off:")), key=str.lower)
        return {"time_zone": zone if _zone(zone) else None, "paused": stored.get("paused") == "1", "off": off}

    def settings(self) -> dict:
        with self._conn() as c:
            return self._settings(c)

    def set_time_zone(self, name) -> dict:
        """The person's time zone (an IANA name such as Asia/Kolkata), so "today" and the times AIs
        are given are theirs, not the server's. Empty clears it (back to this machine's clock)."""
        name = str(name or "").strip()
        if name and (len(name) > 64 or _zone(name) is None):
            raise MindhallError(f"'{name[:64]}' isn't a time zone Mindhall knows. Use a name like Asia/Kolkata or Europe/London.")
        with self._conn() as c:
            if name:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('time_zone', ?)", (name,))
            else:
                c.execute("DELETE FROM settings WHERE key = 'time_zone'")
            return self._settings(c)

    def set_paused(self, on: bool) -> dict:
        """Pause saving: every AI can still read, none can write (the tools check ensure_saving)."""
        with self._conn() as c:
            if on:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('paused', '1')")
            else:
                c.execute("DELETE FROM settings WHERE key = 'paused'")
            return self._settings(c)

    def ensure_saving(self) -> None:
        """Called by every AI tool that writes. The dashboard (the person) can always write."""
        if self.settings()["paused"]:
            raise MindhallError(PAUSED)

    # ---------- One AI app switched off or on ----------
    # Kept with the settings (so "Clear my mind" keeps it), keyed by the app's name cleaned like a read
    # receipt and not case-sensitive. Self-hosted, apps report their own names, so this is a convenience,
    # not a lock; hosted Mindhall also keeps a flag on each signed-in connection.

    @staticmethod
    def _app(name) -> str:
        return " ".join(str(name or "").split())[:60]

    def _is_on(self, c, name) -> bool:
        return c.execute("SELECT 1 FROM settings WHERE key = ?", ("off:" + name.lower(),)).fetchone() is None

    def _switch(self, c, name: str, on: bool, by: str, ref=None, summary=None) -> bool:
        was_on = self._is_on(c, name)
        if was_on == on:
            return False
        if on:
            c.execute("DELETE FROM settings WHERE key = ?", ("off:" + name.lower(),))
        else:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("off:" + name.lower(), name))
        self._log(c, by, AI_ROOM, summary or f"Turned {'on' if on else 'off'} {name}",
                  {"name": name, "on": was_on, "ref": ref}, {"name": name, "on": on, "ref": ref})
        return True

    def set_ai_on(self, name, on: bool, by: str, ref=None) -> dict:
        """Switch one AI app off (it can't read or save at all) or back on. A signed change, so it shows
        in Activity and can be undone. Switching to the state it's already in changes nothing."""
        name = self._app(name)
        if not name:
            raise MindhallError("Say which AI app to switch on or off.")
        with self._conn() as c:
            self._switch(c, name, bool(on), by, ref)
            return self._settings(c)

    def is_on(self, name) -> bool:
        name = self._app(name)
        if not name:
            return True
        with self._conn() as c:
            return self._is_on(c, name)

    def ensure_on(self, name) -> None:
        """Called by every AI tool, reads included, before anything else."""
        if not self.is_on(name):
            raise MindhallError(OFF)

    def clear(self, by: str) -> dict:
        """Erase every memory, project, date and project memory, and the whole history, keeping only
        the settings. For the dashboard's "Clear my mind": it can't be undone. For two weeks after,
        AIs are told the mind was cleared, so they stop using what it said before."""
        with self._conn() as c:
            c.execute("PRAGMA secure_delete = ON")  # overwrite the old text instead of leaving it in free space
            counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("about", "work", "dates", "project_notes")}
            for table in ("about", "work", "dates", "project_notes", "changes", "reads"):
                c.execute(f"DELETE FROM {table}")
            self._log(c, by, CLEARED_ROOM, "Cleared the mind: every memory, project and date, and their history, were erased")
        try:  # rebuild the file and fold the WAL back, so no old copy of the text lingers on disk
            with self._conn() as c:
                c.execute("VACUUM")
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass  # best effort: the rows themselves are already gone
        return {"cleared": counts}

    def _today(self, zone_name) -> date:
        """Today in the person's time zone, else on this machine's clock."""
        zone = _zone(zone_name)
        now = self._clock()
        return (now.astimezone(zone) if zone else now.astimezone()).date()

    # ---------- Projects ----------

    @staticmethod
    def _work_dict(row) -> dict:
        steps = _steps_of(row)
        return {"id": row["id"], "name": row["name"], "status": row["status"],
                "next_step": steps[0] if steps else "", "steps": steps}

    @staticmethod
    def _raw_work(row) -> dict:
        """A project row as the log stores it (raw column values, so an undo can compare them)."""
        return {"id": row["id"], "name": row["name"], "status": row["status"], "next_step": row["next_step"],
                "later_steps": row["later_steps"]}

    @staticmethod
    def _new_steps(current: list, next_step, steps) -> list:
        if steps is not None:
            if isinstance(steps, str):
                steps = [steps]
            clean = _clean_steps(_text(s, "next step") for s in steps)
            if len(clean) > MAX_STEPS:
                raise MindhallError(f"Give at most {MAX_STEPS} next steps.")
            if next_step is not None and (clean[0] if clean else "").lower() != _text(next_step, "next step").lower():
                raise MindhallError("next_step and steps disagree. Send just steps.")
            return clean
        if next_step is not None:
            # What older copies send: X becomes step 1, the later steps stay (X removed from them).
            first = _text(next_step, "next step")
            return _clean_steps([first] + [s for s in current[1:] if s.lower() != first.lower()])[:MAX_STEPS]
        return current

    def _update_work(self, c, row, by, name=None, status=None, next_step=None, steps=None) -> dict:
        current = _steps_of(row)
        new_name = row["name"]
        if name is not None:
            new_name = _text(name, "work name")
            if not new_name:
                raise MindhallError("The work name is empty.")
        new_status = _status(status) if status is not None else row["status"]
        new_steps = self._new_steps(current, next_step, steps)
        if (new_name, new_status, new_steps) == (row["name"], row["status"], current):
            return self._work_dict(row)
        before = self._raw_work(row)
        after = {"id": row["id"], "name": new_name, "status": new_status,
                 "next_step": new_steps[0] if new_steps else "",
                 "later_steps": json.dumps(new_steps[1:]) if len(new_steps) > 1 else ""}
        try:
            c.execute("UPDATE work SET name = ?, status = ?, next_step = ?, later_steps = ?, updated_at = ? WHERE id = ?",
                      (new_name, new_status, after["next_step"], after["later_steps"], _now(), row["id"]))
        except sqlite3.IntegrityError:
            raise MindhallError(f"There is already a project called '{new_name}'.")
        parts = []
        if new_name != row["name"]:
            self._carry_links(c, row["name"], new_name)
            parts.append(f"renamed to {new_name}")
        if new_status != row["status"]:
            parts.append(f"status {row['status']} to {new_status}")
        if new_steps != current:
            if new_steps[1:] == current[1:]:
                parts.append(f"next step: {new_steps[0] if new_steps else 'none'}")
            else:
                parts.append("steps: " + (" / ".join(new_steps) if new_steps else "none"))
        self._log(c, by, "Projects", f"Updated {row['name']}: " + "; ".join(parts), before=before, after=after)
        return self._work_dict(c.execute("SELECT * FROM work WHERE id = ?", (row["id"],)).fetchone())

    @staticmethod
    def _carry_links(c, old: str, new: str):
        """A renamed project keeps its dates and memories (they link by name)."""
        c.execute("UPDATE dates SET work = ? WHERE work = ? COLLATE NOCASE", (new, old))
        c.execute("UPDATE project_notes SET work = ? WHERE work = ? COLLATE NOCASE", (new, old))

    def set_work(self, name: str, by: str, status: str | None = None, next_step: str | None = None,
                 steps: list | None = None) -> dict:
        """Add a project, or update its status or next steps. Never renames."""
        name = _text(name, "work name")
        if not name:
            raise MindhallError("The work name is empty.")
        if status is not None:
            status = _status(status)
        with self._conn() as c:
            row = c.execute("SELECT * FROM work WHERE name = ?", (name,)).fetchone()
            if row is not None:
                return self._update_work(c, row, by, status=status, next_step=next_step, steps=steps)
            new_steps = self._new_steps([], next_step, steps)
            new = {"name": name, "status": status or "Active", "next_step": new_steps[0] if new_steps else "",
                   "later_steps": json.dumps(new_steps[1:]) if len(new_steps) > 1 else ""}
            cur = c.execute(
                "INSERT INTO work (name, status, next_step, later_steps, updated_at) VALUES (?, ?, ?, ?, ?)",
                (new["name"], new["status"], new["next_step"], new["later_steps"], _now()),
            )
            self._log(c, by, "Projects", f"Added {name} ({new['status']})", after={"id": cur.lastrowid, **new})
            return self._work_dict(c.execute("SELECT * FROM work WHERE id = ?", (cur.lastrowid,)).fetchone())

    # ---------- Dates ----------

    def _insert_date(self, c, what: str, day: str, work: str, by: str) -> dict:
        cur = c.execute(
            "INSERT INTO dates (day, what, work, updated_at) VALUES (?, ?, ?, ?)",
            (day, what, work, _now()),
        )
        after = {"day": day, "what": what, "work": work}
        self._log(c, by, "Dates", f"Added {what} on {day}", after={"id": cur.lastrowid, **after})
        return {"id": cur.lastrowid, **after}

    def add_date(self, what: str, day, by: str, work: str = "") -> dict:
        """Always add a new date, even if one with the same name exists (the web table's Add)."""
        day = _parse_day(day)
        what = _text(what, "date label")
        work = _text(work, "work name")
        if not what:
            raise MindhallError("Say what the date is for.")
        with self._conn() as c:
            return self._insert_date(c, what, day, work, by)

    def set_date(self, what: str, day, by: str, work: str = "", date_id: int | None = None) -> dict:
        """Add a date, or move an existing one.

        An existing date is found by id, or else by the same name ("Launch")
        within the same work, so "launch moved to Nov 20" updates the old
        entry instead of creating a second, conflicting one, while Project A's
        Launch and Project B's Launch stay separate dates.
        """
        day = _parse_day(day)
        what = _text(what, "date label")
        work = _text(work, "work name")
        with self._conn() as c:
            row = None
            if date_id is not None:
                row = c.execute("SELECT * FROM dates WHERE id = ?", (date_id,)).fetchone()
                if row is None:
                    raise MindhallError(f"There is no date with id {date_id}.")
            elif what:
                rows = c.execute("SELECT * FROM dates WHERE what = ? COLLATE NOCASE", (what,)).fetchall()
                if work:
                    # Same work first; a same-named date with no work yet is taken to be this one.
                    rows = ([r for r in rows if r["work"].lower() == work.lower()]
                            or [r for r in rows if not r["work"]])
                if len(rows) > 1:
                    raise MindhallError(
                        f"More than one date is called '{what}'. Pass its id, or the work it belongs to, to move it."
                    )
                row = rows[0] if rows else None
            if row is None:
                if not what:
                    raise MindhallError("Say what the date is for.")
                return self._insert_date(c, what, day, work, by)
            before = {"day": row["day"], "what": row["what"], "work": row["work"]}
            # Matched by name: keep the stored name. Matched by id: a new name renames it.
            new_what = what if (date_id is not None and what) else row["what"]
            # Keep the stored spelling when the work only differs in capitals.
            new_work = work if work and work.lower() != row["work"].lower() else row["work"]
            after = {"day": day, "what": new_what, "work": new_work}
            if after == before:
                return {"id": row["id"], **after}
            c.execute(
                "UPDATE dates SET day = ?, what = ?, work = ?, updated_at = ? WHERE id = ?",
                (after["day"], after["what"], after["work"], _now(), row["id"]),
            )
            if before["day"] != after["day"]:
                summary = f"Moved {after['what']} from {before['day']} to {after['day']}"
            else:
                summary = f"Updated {after['what']}"
            self._log(c, by, "Dates", summary, before={"id": row["id"], **before}, after={"id": row["id"], **after})
            return {"id": row["id"], **after}

    def remove_date(self, date_id: int, by: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM dates WHERE id = ?", (date_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no date with id {date_id}.")
            c.execute("DELETE FROM dates WHERE id = ?", (date_id,))
            before = {"id": row["id"], "day": row["day"], "what": row["what"], "work": row["work"]}
            self._log(c, by, "Dates", f"Removed {row['what']} ({row['day']})", before=before)
            return {"removed": date_id}

    # ---------- Editing (used by the web tables) ----------

    def edit_note(self, note_id: int, note: str, by: str) -> dict:
        note = _text(note, "note")
        if not note:
            raise MindhallError("The note is empty. Delete it instead.")
        with self._conn() as c:
            row = c.execute("SELECT id, note FROM about WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no note with id {note_id}.")
            if row["note"] == note:
                return dict(row)
            c.execute("UPDATE about SET note = ?, updated_at = ? WHERE id = ?", (note, _now(), note_id))
            self._log(c, by, "About you", f"Edited note: {note}", before=dict(row), after={"id": note_id, "note": note})
            return {"id": note_id, "note": note}

    def edit_work(self, work_id: int, by: str, name: str | None = None, status: str | None = None,
                  next_step: str | None = None, steps: list | None = None) -> dict:
        """Change any field of one project, found by id. A rename carries its dates and memories along."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM work WHERE id = ?", (work_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no work with id {work_id}.")
            return self._update_work(c, row, by, name=name, status=status, next_step=next_step, steps=steps)

    def remove_work(self, work_id: int, by: str) -> dict:
        """Remove a project. Its dates and memories stay, and come back if it is restored."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM work WHERE id = ?", (work_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no work with id {work_id}.")
            c.execute("DELETE FROM work WHERE id = ?", (work_id,))
            self._log(c, by, "Projects", f"Removed {row['name']}", before=self._raw_work(row))
            return {"removed": work_id}

    # ---------- Undo, restore and forget ----------

    def _renames(self, c) -> list:
        """Every project rename in the log, oldest first: [(change id, old name, new name)]."""
        out = []
        for r in c.execute("SELECT id, before, after FROM changes WHERE room IN ('Work', 'Projects') "
                           "AND before IS NOT NULL AND after IS NOT NULL ORDER BY id"):
            b, a = _load(r["before"]) or {}, _load(r["after"]) or {}
            old, new = b.get("name"), a.get("name")
            if old and new and old != new:
                out.append((r["id"], old, new))
        return out

    @staticmethod
    def _follow(name, renames: list, after_id: int):
        """The name a project link has now, following the renames logged after a change."""
        for change_id, old, new in renames:
            if change_id > after_id and name and name.lower() == old.lower():
                name = new
        return name

    @staticmethod
    def _work_of(row, renames: list):
        """The project a logged change belongs to, by the name it has now (following later renames)."""
        room = row["room"]
        if room not in ("Work", "Projects", "Dates", "Project notes"):
            return None
        side = _load(row["after"]) or _load(row["before"]) or {}
        name = side.get("name") if room in ("Work", "Projects") else side.get("work")
        return Mind._follow(name, renames, row["id"]) or None

    @staticmethod
    def _present(c, table: str, values: dict) -> bool:
        """Is this item in the mind (by content)? Catches restores by any route, and re-adds by hand."""
        if table == "about":
            sql, args = "SELECT 1 FROM about WHERE note = ? COLLATE NOCASE", (values.get("note"),)
        elif table == "work":
            sql, args = "SELECT 1 FROM work WHERE name = ?", (values.get("name"),)
        elif table == "dates":
            sql, args = ("SELECT 1 FROM dates WHERE day = ? AND what = ? COLLATE NOCASE AND work = ? COLLATE NOCASE",
                         (values.get("day"), values.get("what"), values.get("work") or ""))
        else:
            sql, args = ("SELECT 1 FROM project_notes WHERE work = ? COLLATE NOCASE AND note = ? COLLATE NOCASE",
                         (values.get("work"), values.get("note")))
        return c.execute(sql, args).fetchone() is not None

    def change(self, change_id) -> dict | None:
        """One change's id, time, author and room, without its contents; None if there's no such change.
        For an AI app being switched, also which app and the switch before and after, so a host can
        check what undoing it would do (hosted Mindhall: plan limits on history and connected apps)."""
        try:
            change_id = int(change_id)
        except (TypeError, ValueError):
            return None
        with self._conn() as c:
            row = c.execute("SELECT id, at, by, room, before, after FROM changes WHERE id = ?", (change_id,)).fetchone()
        if row is None:
            return None
        out = {"id": row["id"], "at": row["at"], "by": row["by"], "room": row["room"]}
        if row["room"] == AI_ROOM:
            before, after = _load(row["before"]) or {}, _load(row["after"]) or {}
            out["ai"] = {"name": after.get("name") or before.get("name") or "", "ref": after.get("ref", before.get("ref")),
                         "on_before": bool(before.get("on")), "on_after": bool(after.get("on"))}
        return out

    def undo(self, change_id: int, by: str) -> dict:
        """Reverse one change. The undo is itself a signed change, so it can be undone too."""
        with self._conn() as c:
            return self._undo(c, change_id, by)

    def restore(self, change_id: int, by: str) -> dict:
        """Put back something that was removed (an entry from Recently deleted)."""
        with self._conn() as c:
            ch = c.execute("SELECT before, after FROM changes WHERE id = ?", (change_id,)).fetchone()
            if ch is None:
                raise MindhallError(f"There is no change with id {change_id}.")
            if ch["after"] is not None or ch["before"] is None:
                raise MindhallError("Only something that was removed can be restored.")
            return self._undo(c, change_id, by)

    def _undo(self, c, change_id: int, by: str) -> dict:
        ch = c.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
        if ch is None:
            raise MindhallError(f"There is no change with id {change_id}.")
        room = ch["room"]
        before, after = _load(ch["before"]), _load(ch["after"])
        if room == AI_ROOM:
            name, ref = (after or before or {}).get("name") or "", (after or before or {}).get("ref")
            if not name or after is None or before is None:
                raise MindhallError("This change can't be undone.")
            if self._is_on(c, name) != bool(after.get("on")):
                raise MindhallError(f"{name} has been switched since. Use its switch in Settings instead.")
            self._switch(c, name, bool(before.get("on")), by, ref, summary=f"Undid: {ch['summary']}")
            return {"ai": name, "on": bool(before.get("on")), "ref": ref}
        if after is not None and after.get("forgot") is not None:
            raise MindhallError("Forgetting can't be undone.")
        if room not in ROOMS:
            raise MindhallError("This change can't be undone.")
        if (before or {}).get("forgotten") or (after or {}).get("forgotten"):
            raise MindhallError("This was forgotten for good, so it can't be undone.")
        table, fields, link = ROOMS[room]
        row_id = (after or before or {}).get("id")
        if row_id is None:
            raise MindhallError("This change was made before Undo existed, so it can't be undone.")
        # Compare and restore only what the log recorded: entries from older copies lack newer fields.
        keys = [f for f in fields if f in (after if after is not None else before)]
        renames = self._renames(c) if link else []

        def follow(name):
            return self._follow(name, renames, change_id)

        if after is not None:  # undo an add or an update
            row = c.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()

            def same(f):
                if f == link:
                    now = (row[f] or "").lower()
                    return now in {(after[f] or "").lower(), (follow(after[f]) or "").lower()}
                return row[f] == after[f]

            if row is None or not all(same(f) for f in keys):
                raise MindhallError("This was changed again later. Undo the newer change first.")
            current = {f: row[f] for f in keys}
            if before is None:
                c.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
                self._log(c, by, room, f"Undid: {ch['summary']}", before={"id": row_id, **current})
                return {"undone": change_id}
            restored = {f: (follow(before[f]) if f == link else before[f]) if f in before else current[f]
                        for f in keys}
            sets = ", ".join(f"{f} = ?" for f in keys)
            try:
                c.execute(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?",
                          (*[restored[f] for f in keys], _now(), row_id))
            except sqlite3.IntegrityError:
                raise MindhallError("Undoing this would clash with something that exists now.")
            if table == "work" and restored.get("name") != current.get("name"):
                self._carry_links(c, current["name"], restored["name"])
            self._log(c, by, room, f"Undid: {ch['summary']}",
                      before={"id": row_id, **current}, after={"id": row_id, **restored})
            return {"undone": change_id}

        # Undo a removal: put the item back, with its old id if that is still free.
        values = {f: (follow(before[f]) if f == link else before[f]) for f in keys}
        if self._present(c, table, values):
            if table == "work":
                raise MindhallError(f"There is already a project called '{values.get('name')}'.")
            raise MindhallError("It's already back.")
        free = c.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone() is None
        cols = (["id"] if free else []) + keys + ["updated_at"]
        args = ([row_id] if free else []) + [values[f] for f in keys] + [_now()]
        try:
            cur = c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", args)
        except sqlite3.IntegrityError:
            raise MindhallError("Undoing this would clash with something that exists now.")
        new_id = row_id if free else cur.lastrowid
        self._log(c, by, room, f"Undid: {ch['summary']}", after={"id": new_id, **values, "restores": change_id})
        return {"undone": change_id, "id": new_id}

    def _lifetime(self, c, table: str, change_id: int, item_id: int) -> list:
        """Every log entry about one item, from its removal back to when it was added."""
        rooms = [room for room, (t, _fields, _link) in ROOMS.items() if t == table]
        marks = ", ".join("?" * len(rooms))
        ids, found = {item_id}, []
        rows = c.execute(f"SELECT * FROM changes WHERE id <= ? AND room IN ({marks}) ORDER BY id DESC",
                         (change_id, *rooms)).fetchall()
        for r in rows:
            if not ids:
                break
            b, a = _load(r["before"]), _load(r["after"])
            rid = (a or b or {}).get("id")
            if rid not in ids:
                continue
            found.append(r)
            if a is not None and a.get("restores") is not None:
                # This copy came back from an earlier removal, perhaps under a new id.
                src = c.execute("SELECT before FROM changes WHERE id = ?", (a["restores"],)).fetchone()
                src_id = (_load(src["before"]) or {}).get("id") if src else None
                if src_id is not None and src_id != rid:
                    ids.discard(rid)
                    ids.add(src_id)
            elif a is not None and b is None:
                ids.discard(rid)  # the add that created it; earlier entries with this id were another item
        return found

    def forget(self, change_id: int, by: str) -> dict:
        """Erase a removed item's text from the whole history, for good. It can't be restored after."""
        with self._conn() as c:
            c.execute("PRAGMA secure_delete = ON")  # overwrite the old text instead of leaving it in free space
            ch = c.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
            if ch is None:
                raise MindhallError(f"There is no change with id {change_id}.")
            before, after = _load(ch["before"]), _load(ch["after"])
            if ch["room"] not in ROOMS or before is None or after is not None:
                raise MindhallError("Only something that was removed can be forgotten.")
            if before.get("forgotten"):
                raise MindhallError("This was already forgotten.")
            if before.get("id") is None:
                raise MindhallError("This was removed before Undo existed, so it can't be traced to forget it.")
            for r in c.execute("SELECT after FROM changes WHERE id > ? AND after IS NOT NULL", (change_id,)):
                if (_load(r["after"]) or {}).get("restores") == change_id:
                    raise MindhallError("This was restored after this removal. Forget it from its latest removal.")
            table, fields, link = ROOMS[ch["room"]]
            renames = self._renames(c)
            values = {f: (self._follow(before[f], renames, change_id) if f == link else before[f])
                      for f in fields if f in before}
            if self._present(c, table, values):
                raise MindhallError("It's back in your mind. Delete it first, then forget it.")
            noun = NOUNS[table]
            entries = self._lifetime(c, table, change_id, before["id"])
            for r in entries:
                sides = [_load(r["before"]), _load(r["after"])]
                for side in sides:
                    if side is not None:
                        for f in TEXT_FIELDS[table]:
                            if f in side:
                                side[f] = ""
                        side["forgotten"] = True
                m = re.match(r"^((?:Undid: )*)([A-Za-z]+)", r["summary"])
                prefix, verb = (m.group(1), m.group(2)) if m else ("", "Changed")
                c.execute("UPDATE changes SET summary = ?, before = ?, after = ? WHERE id = ?",
                          (f"{prefix}{verb} {noun} (forgotten)", _dump(sides[0]), _dump(sides[1]), r["id"]))
            self._log(c, by, ch["room"], f"Forgot a removed {noun}", after={"forgot": change_id})
            result = {"forgotten": change_id, "entries": len(entries)}
        try:  # rebuild the file and fold the WAL back, so no old copy of the text lingers on disk
            with self._conn() as c:
                c.execute("VACUUM")
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass  # best effort: the log itself is already redacted
        return result

    def _removed(self, c, days: int, now: datetime | None = None) -> list:
        """Items removed in the last `days` and not back since, newest first (from the log alone)."""
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        renames = self._renames(c)
        out, seen, restored = [], set(), set()
        for r in c.execute("SELECT * FROM changes ORDER BY id DESC"):
            moment = _when(r["at"])
            if moment is not None and moment < cutoff:
                break
            b, a = _load(r["before"]), _load(r["after"])
            if a is not None:
                if a.get("restores") is not None:
                    restored.add(a["restores"])
                continue
            if b is None or r["room"] not in ROOMS or b.get("forgotten") or r["id"] in restored:
                continue
            table, fields, link = ROOMS[r["room"]]
            values = {f: (self._follow(b[f], renames, r["id"]) if f == link else b[f]) for f in fields if f in b}
            key = (table, tuple(str(values.get(f) or "").lower() for f in IDENTITY[table]))
            if key in seen:
                continue
            seen.add(key)
            if self._present(c, table, values):
                continue
            out.append({"change": r["id"], "room": r["room"], "table": table, "at": r["at"], "by": r["by"],
                        "item": values, "label": _label(table, values)})
        return out

    def deleted(self, days: int = DELETED_DAYS, now: datetime | None = None) -> list:
        """What can still be restored from the dashboard."""
        with self._conn() as c:
            return self._removed(c, days, now)

    # ---------- Read receipts ----------

    def mark_read(self, by: str, seen: int | None = None, now: datetime | None = None) -> bool:
        """Note that an AI read the mind. At most once a minute per AI, unless it has seen a newer change.
        Best effort: a busy file never fails the read itself."""
        by = " ".join(str(by or "").split())[:60]
        if not by:
            return False
        moment = now or datetime.now(timezone.utc)
        try:
            with self._conn() as c:
                c.execute("PRAGMA busy_timeout=250")
                if seen is None:
                    seen = c.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]
                row = c.execute("SELECT at, seen FROM reads WHERE by = ?", (by,)).fetchone()
                if row is not None:
                    last = _when(row["at"])
                    if seen <= row["seen"] and last is not None and (moment - last).total_seconds() < READ_EVERY:
                        return False
                    seen = max(seen, row["seen"])
                c.execute("INSERT OR REPLACE INTO reads (by, at, seen) VALUES (?, ?, ?)",
                          (by, moment.isoformat(timespec="seconds"), seen))
                return True
        except (sqlite3.Error, OSError):
            return False

    # ---------- Reading ----------

    def _last_changes(self, c) -> dict:
        """Who last changed each item, and when, from the Change Log: {(table, id): (by, at)}.
        Read only from the log, so it needs no new column and works with minds written by older
        versions. Items whose changes predate ids in the log have no entry."""
        rooms = tuple(ROOMS)
        marks = ", ".join("?" * len(rooms))
        last, newest = {}, {}
        try:
            # SQLite takes the bare columns (by, at) from the row holding MAX(id).
            rows = c.execute(
                "SELECT room, COALESCE(json_extract(after, '$.id'), json_extract(before, '$.id')) AS item, "
                f"by, at, MAX(id) AS cid FROM changes WHERE room IN ({marks}) GROUP BY room, item", rooms).fetchall()
            for r in rows:
                key = (ROOMS[r["room"]][0], r["item"])
                if r["item"] is not None and r["cid"] > newest.get(key, 0):
                    newest[key], last[key] = r["cid"], (r["by"], r["at"])
        except sqlite3.OperationalError:  # an SQLite built without JSON functions
            for r in c.execute(f"SELECT room, by, at, before, after FROM changes WHERE room IN ({marks}) "
                               "ORDER BY id", rooms):
                item = _load(r["after"]) or _load(r["before"]) or {}
                if item.get("id") is not None:
                    last[(ROOMS[r["room"]][0], item["id"])] = (r["by"], r["at"])
        return last

    def _readers(self, c) -> list:
        out = []
        for r in c.execute("SELECT by, at, seen FROM reads ORDER BY at DESC"):
            missed = c.execute("SELECT COUNT(*) FROM changes WHERE id > ? AND by != ? AND room != ?",
                               (r["seen"], r["by"], AI_ROOM)).fetchone()[0]
            out.append({"by": r["by"], "at": r["at"], "seen": r["seen"], "missed": missed})
        return out

    @staticmethod
    def _undoable(r) -> bool:
        if r["room"] == AI_ROOM:
            return True
        b, a = _load(r["before"]) or {}, _load(r["after"]) or {}
        item = a or b
        return (r["room"] in ROOMS and item.get("id") is not None
                and not b.get("forgotten") and not a.get("forgotten"))

    def everything(self, changes: int = 100) -> dict:
        """The whole mind with ids, for the web tables. Each item also says who last changed it and
        when (`by`, `changed_at`; None when the log doesn't say)."""
        with self._conn() as c:
            about = [dict(r) for r in c.execute("SELECT id, note FROM about ORDER BY id")]
            work = [self._work_dict(r) for r in c.execute(
                "SELECT * FROM work ORDER BY "
                "CASE status WHEN 'Active' THEN 0 WHEN 'Parked' THEN 1 ELSE 2 END, name"
            )]
            dates = [dict(r) for r in c.execute("SELECT id, day, what, work FROM dates ORDER BY day")]
            notes = [dict(r) for r in c.execute("SELECT id, work, note FROM project_notes ORDER BY id")]
            last = self._last_changes(c)
            for table, items in (("about", about), ("work", work), ("dates", dates), ("project_notes", notes)):
                for item in items:
                    item["by"], item["changed_at"] = last.get((table, item["id"]), (None, None))
            renames = self._renames(c)
            log = [{"id": r["id"], "at": r["at"], "by": r["by"], "room": r["room"], "summary": r["summary"],
                    "undoable": self._undoable(r), "work": self._work_of(r, renames)}
                   for r in c.execute("SELECT * FROM changes ORDER BY id DESC LIMIT ?", (changes,))]
            readers = self._readers(c)
            deleted = self._removed(c, DELETED_DAYS)
            settings = self._settings(c)
            total = c.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
            daily = self._daily(c, renames, _zone(settings["time_zone"]))
        return {"about": about, "work": work, "dates": dates, "project_notes": notes, "changes": log,
                "readers": readers, "deleted": deleted, "settings": settings, "change_count": total,
                "daily": daily, "version": VERSION}

    def _daily(self, c, renames: list, zone, days: int = 14) -> list:
        """How many changes each AI made to each project, per day, over the last two weeks, counted in
        the person's time zone: the dashboard's activity charts (not limited to the latest 100 changes)."""
        cutoff = (self._clock() - timedelta(days=days + 1)).isoformat(timespec="seconds")
        counts = {}
        for r in c.execute("SELECT id, at, by, room, before, after FROM changes WHERE at >= ?", (cutoff,)):
            key = (_local(r["at"], zone).date().isoformat(), r["by"], self._work_of(r, renames))
            counts[key] = counts.get(key, 0) + 1
        return [{"day": d, "by": b, "work": w, "n": n}
                for (d, b, w), n in sorted(counts.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or ""))]

    def project(self, work_id: int | None = None, name: str | None = None, history: int = 50) -> dict:
        """One project's page: the project, its memories, its dates and the changes that touched it."""
        with self._conn() as c:
            if work_id is not None:
                row = c.execute("SELECT * FROM work WHERE id = ?", (work_id,)).fetchone()
            else:
                row = c.execute("SELECT * FROM work WHERE name = ?", (_text(name, "work name"),)).fetchone()
            if row is None and work_id is not None:
                raise MindhallError(f"There is no work with id {work_id}.")
            if row is None:
                self._project_name(c, name)  # raises, listing the projects there are
            work = self._work_dict(row)
            names = {row["name"].lower()}
            for _change, old, new in reversed(self._renames(c)):  # every earlier name of this project
                if new.lower() in names:
                    names.add(old.lower())
            last = self._last_changes(c)
            notes = [dict(r) for r in c.execute(
                "SELECT id, work, note FROM project_notes WHERE work = ? COLLATE NOCASE ORDER BY id", (row["name"],))]
            dates = [dict(r) for r in c.execute(
                "SELECT id, day, what, work FROM dates WHERE work = ? COLLATE NOCASE ORDER BY day", (row["name"],))]
            for table, items in (("project_notes", notes), ("dates", dates)):
                for item in items:
                    item["by"], item["changed_at"] = last.get((table, item["id"]), (None, None))
            work["by"], work["changed_at"] = last.get(("work", row["id"]), (None, None))
            log = []
            for r in c.execute("SELECT * FROM changes WHERE room IN ('Work', 'Projects', 'Dates', 'Project notes') "
                               "ORDER BY id DESC"):
                item = _load(r["after"]) or _load(r["before"]) or {}
                key = item.get("name") if r["room"] in ("Work", "Projects") else item.get("work")
                if key and key.lower() in names:
                    log.append({"id": r["id"], "at": r["at"], "by": r["by"], "room": r["room"],
                                "summary": r["summary"], "undoable": self._undoable(r)})
                    if len(log) >= history:
                        break
        return {"work": work, "notes": notes, "dates": dates, "history": log}

    def snapshot(self, days_ahead: int = 14, changes: int = 5, today: date | None = None) -> dict:
        with self._conn() as c:
            prefs = self._settings(c)
            zone = prefs["time_zone"]
            today = today or self._today(zone)
            horizon = (today + timedelta(days=days_ahead)).isoformat()
            about = [dict(r) for r in c.execute("SELECT id, note FROM about ORDER BY id")]
            work = [self._work_dict(r) for r in c.execute(
                "SELECT * FROM work ORDER BY "
                "CASE status WHEN 'Active' THEN 0 WHEN 'Parked' THEN 1 ELSE 2 END, name"
            )]
            notes = {}
            for r in c.execute("SELECT id, work, note FROM project_notes ORDER BY id"):
                notes.setdefault(r["work"].lower(), []).append({"id": r["id"], "note": r["note"]})
            for w in work:
                w["notes"] = notes.get(w["name"].lower(), [])
            upcoming = [dict(r) for r in c.execute(
                "SELECT id, day, what, work FROM dates WHERE day >= ? AND day <= ? ORDER BY day",
                (today.isoformat(), horizon),
            )]
            recent = [dict(r) for r in c.execute(
                "SELECT at, by, room, summary FROM changes WHERE room != ? ORDER BY id DESC LIMIT ?", (AI_ROOM, changes)
            )]
            removed = [r["label"] for r in self._removed(c, REMOVED_DAYS)[:8]]
            latest = c.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]
            empty = not about and not work and c.execute("SELECT 1 FROM dates LIMIT 1").fetchone() is None
            wiped = c.execute("SELECT at FROM changes WHERE room = ? ORDER BY id DESC LIMIT 1", (CLEARED_ROOM,)).fetchone()
        snap = {"about": about, "work": work, "upcoming": upcoming, "recent_changes": recent,
                "removed": removed, "latest": latest, "time_zone": zone, "paused": prefs["paused"]}
        if wiped and (_when(wiped["at"]) or self._clock()) >= self._clock() - timedelta(days=REMOVED_DAYS):
            snap["cleared"] = _local(wiped["at"], _zone(zone)).date().isoformat()
        if empty:
            snap["setup"] = SETUP_PROMPT
        return snap

    def all_dates(self, include_past: bool = False, today: date | None = None) -> list[dict]:
        with self._conn() as c:
            today = today or self._today(self._settings(c)["time_zone"])
            if include_past:
                rows = c.execute("SELECT id, day, what, work FROM dates ORDER BY day")
            else:
                rows = c.execute(
                    "SELECT id, day, what, work FROM dates WHERE day >= ? ORDER BY day", (today.isoformat(),)
                )
            return [dict(r) for r in rows]

    def recent_changes(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit or 20), 200))
        with self._conn() as c:
            return [dict(r) for r in c.execute(  # what AIs read: switching apps on or off isn't about the mind
                "SELECT id, at, by, room, summary FROM changes WHERE room != ? ORDER BY id DESC LIMIT ?", (AI_ROOM, limit)
            )]

    def is_empty(self) -> bool:
        with self._conn() as c:
            for table in ("about", "work", "dates"):
                if c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    return False
        return True


# ---------- Text for AIs and people ----------

def _local(at: str, zone=None) -> datetime:
    """A stored UTC timestamp in the person's time zone (their setting), else this machine's (TZ)."""
    try:
        moment = datetime.fromisoformat(at)
    except ValueError:
        moment = datetime.now(timezone.utc)
    return moment.astimezone(zone) if zone else moment.astimezone()


def _work_line(w: dict, featured: bool) -> list:
    steps = w.get("steps") or ([w["next_step"]] if w.get("next_step") else [])
    step = f", next: {steps[0]}" if steps else ""
    if featured and len(steps) > 1:
        step += "; then: " + "; ".join(steps[1:])
    lines = [f"- {w['name']} [{w['status']}{step}]"]
    if featured:
        lines += [f"  - {_short(n['note'], 150)}" for n in (w.get("notes") or [])[:3]]
    return lines


def render_context(snap: dict, max_chars: int = 1800) -> str:
    """The short block injected into every AI turn. Done work is left out; Active projects come first."""
    notes = []
    if snap.get("cleared"):
        notes.append(f"The user cleared their Mindhall on {snap['cleared']}: don't use anything it told you before then.")
    if snap.get("paused"):
        notes.append(PAUSED)
    if snap.get("setup"):
        return "\n".join(notes + [snap["setup"]])
    body = ["Mindhall: the user's shared mind. If it disagrees with your own memory, Mindhall is right."] + notes
    if snap["about"]:
        body.append("About the user:")
        body += [f"- {a['note']}" for a in snap["about"]]
    open_work = [w for w in snap["work"] if w["status"] != "Done"]
    featured = [w for w in open_work if w["status"] == "Active"]   # with all their steps and memories
    others = [w for w in open_work if w not in featured]
    if open_work:
        body.append("Work:")
        for w in featured:
            body += _work_line(w, True)
        for w in others:
            body += _work_line(w, False)
    if snap["upcoming"]:
        body.append("Upcoming dates:")
        for d in snap["upcoming"]:
            tag = f" ({d['work']})" if d["work"] else ""
            body.append(f"- {d['day']}: {d['what']}{tag}")
    tail = ""
    if snap.get("removed"):
        tail = _short("Recently removed (don't use): " + "; ".join(snap["removed"]), 320)
    changes = []
    if snap["recent_changes"]:
        zone = _zone(snap.get("time_zone"))
        changes = ["Latest changes:"] + [f"- {_local(ch['at'], zone):%Y-%m-%d} {ch['by']}: {ch['summary']}"
                                         for ch in snap["recent_changes"]]

    def joined():
        return "\n".join(body + ([tail] if tail else []) + changes)

    while len(joined()) > max_chars and changes:   # the latest changes give way first
        changes = changes[:-1] if len(changes) > 2 else []
    text = joined()
    if len(text) > max_chars:
        room = max_chars - 40 - (len(tail) + 1 if tail else 0)
        text = "\n".join(body)[:room].rstrip() + "\n- (more in Mindhall, use mindhall_read)"
        if tail:
            text += "\n" + tail
    return text


def render_summary(snap: dict) -> str:
    """What the /mindhall command shows a person."""
    out = ["Mindhall", ""]
    out.append("About you")
    out += [f"  {a['id']}. {a['note']}" for a in snap["about"]] or ["  Nothing yet."]
    out += ["", "Work"]
    out += [
        f"  {w['name']} ({w['status']})"
        + (f": {' / '.join(w.get('steps') or [w['next_step']])}" if w["next_step"] else "")
        for w in snap["work"]
    ] or ["  Nothing yet."]
    out += ["", "Coming up"]
    out += [
        f"  {d['day']}  {d['what']}" + (f" ({d['work']})" if d["work"] else "")
        for d in snap["upcoming"]
    ] or ["  No dates in this window."]
    out += ["", "Latest changes"]
    zone = _zone(snap.get("time_zone"))
    out += [f"  {_local(c['at'], zone):%Y-%m-%d %H:%M}  {c['by']}: {c['summary']}" for c in snap["recent_changes"]] or [
        "  No changes yet."
    ]
    return "\n".join(out)
