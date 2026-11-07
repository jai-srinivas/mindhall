"""Mindhall core engine: the shared mind every AI reads and writes.

This file has no Hermes imports, so the same engine can power the Hermes
plugin (now), a Hermes memory provider (later) and the MCP server for
Claude, ChatGPT and Cursor (Day 2).

Four rooms: About you, Work, Dates, Change Log. Every write is logged in
the Change Log with who made it, inside the same transaction, so a failed
write never leaves a half-changed mind.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("Active", "Parked", "Done")
MAX_TEXT = 500  # characters per note, name, step or date label: a mind, not a document store

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
"""


class MindhallError(ValueError):
    """A request the mind refuses. The message is safe to show to an AI."""


def default_path() -> Path:
    raw = os.environ.get("MINDHALL_DB") or str(Path.home() / ".mindhall" / "mindhall.db")
    return Path(raw).expanduser()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


class Mind:
    """One shared mind, stored in a single SQLite file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()
        with self._conn():
            pass  # creates the folder, file and tables on first use

    @contextmanager
    def _conn(self):
        # Checked on every connection, so a mind that was moved or deleted
        # while Hermes or the MCP server is running is recreated cleanly.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # Hermes and the MCP server can share the file
        conn.execute("PRAGMA busy_timeout=5000")
        if fresh:
            conn.executescript(SCHEMA)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _log(self, c, by, room, summary, before=None, after=None):
        c.execute(
            "INSERT INTO changes (at, by, room, summary, before, after) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), by or "Unknown", room, summary, _dump(before), _dump(after)),
        )

    # ---------- About you ----------

    def add_note(self, note: str, by: str) -> dict:
        note = _text(note, "note")
        if not note:
            raise MindhallError("The note is empty.")
        with self._conn() as c:
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

    # ---------- Work ----------

    def set_work(self, name: str, by: str, status: str | None = None, next_step: str | None = None) -> dict:
        """Add a piece of work, or update its status or next step."""
        name = _text(name, "work name")
        if not name:
            raise MindhallError("The work name is empty.")
        if status is not None:
            match = next((s for s in STATUSES if s.lower() == str(status).strip().lower()), None)
            if match is None:
                raise MindhallError(f"Status must be one of: {', '.join(STATUSES)}.")
            status = match
        with self._conn() as c:
            row = c.execute("SELECT * FROM work WHERE name = ?", (name,)).fetchone()
            if row is None:
                new = {"name": name, "status": status or "Active", "next_step": _text(next_step, "next step")}
                c.execute(
                    "INSERT INTO work (name, status, next_step, updated_at) VALUES (?, ?, ?, ?)",
                    (new["name"], new["status"], new["next_step"], _now()),
                )
                self._log(c, by, "Work", f"Added {name} ({new['status']})", after=new)
                return new
            before = {"name": row["name"], "status": row["status"], "next_step": row["next_step"]}
            after = dict(before)
            if status is not None:
                after["status"] = status
            if next_step is not None:
                after["next_step"] = _text(next_step, "next step")
            if after == before:
                return after
            c.execute(
                "UPDATE work SET status = ?, next_step = ?, updated_at = ? WHERE id = ?",
                (after["status"], after["next_step"], _now(), row["id"]),
            )
            parts = []
            if after["status"] != before["status"]:
                parts.append(f"status {before['status']} to {after['status']}")
            if after["next_step"] != before["next_step"]:
                parts.append(f"next step: {after['next_step'] or 'none'}")
            self._log(c, by, "Work", f"Updated {row['name']}: " + "; ".join(parts), before=before, after=after)
            return after

    # ---------- Dates ----------

    def set_date(self, what: str, day, by: str, work: str = "", date_id: int | None = None) -> dict:
        """Add a date, or move an existing one.

        An existing date is found by id, or else by the same name
        ("Launch"), so "launch moved to Nov 20" updates the old entry
        instead of creating a second, conflicting one.
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
                if len(rows) > 1:
                    raise MindhallError(f"More than one date is called '{what}'. Pass its id to move it.")
                row = rows[0] if rows else None
            if row is None:
                if not what:
                    raise MindhallError("Say what the date is for.")
                cur = c.execute(
                    "INSERT INTO dates (day, what, work, updated_at) VALUES (?, ?, ?, ?)",
                    (day, what, work, _now()),
                )
                after = {"day": day, "what": what, "work": work}
                self._log(c, by, "Dates", f"Added {what} on {day}", after=after)
                return {"id": cur.lastrowid, **after}
            before = {"day": row["day"], "what": row["what"], "work": row["work"]}
            # Matched by name: keep the stored name. Matched by id: a new name renames it.
            new_what = what if (date_id is not None and what) else row["what"]
            after = {"day": day, "what": new_what, "work": work or row["work"]}
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
            self._log(c, by, "Dates", summary, before=before, after=after)
            return {"id": row["id"], **after}

    def remove_date(self, date_id: int, by: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM dates WHERE id = ?", (date_id,)).fetchone()
            if row is None:
                raise MindhallError(f"There is no date with id {date_id}.")
            c.execute("DELETE FROM dates WHERE id = ?", (date_id,))
            before = {"day": row["day"], "what": row["what"], "work": row["work"]}
            self._log(c, by, "Dates", f"Removed {row['what']} ({row['day']})", before=before)
            return {"removed": date_id}

    # ---------- Reading ----------

    def snapshot(self, days_ahead: int = 14, changes: int = 5, today: date | None = None) -> dict:
        today = today or date.today()
        horizon = (today + timedelta(days=days_ahead)).isoformat()
        with self._conn() as c:
            about = [dict(r) for r in c.execute("SELECT id, note FROM about ORDER BY id")]
            work = [dict(r) for r in c.execute(
                "SELECT name, status, next_step FROM work ORDER BY "
                "CASE status WHEN 'Active' THEN 0 WHEN 'Parked' THEN 1 ELSE 2 END, name"
            )]
            upcoming = [dict(r) for r in c.execute(
                "SELECT id, day, what, work FROM dates WHERE day >= ? AND day <= ? ORDER BY day",
                (today.isoformat(), horizon),
            )]
            recent = [dict(r) for r in c.execute(
                "SELECT at, by, room, summary FROM changes ORDER BY id DESC LIMIT ?", (changes,)
            )]
        return {"about": about, "work": work, "upcoming": upcoming, "recent_changes": recent}

    def all_dates(self, include_past: bool = False, today: date | None = None) -> list[dict]:
        today = today or date.today()
        with self._conn() as c:
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
            return [dict(r) for r in c.execute(
                "SELECT id, at, by, room, summary FROM changes ORDER BY id DESC LIMIT ?", (limit,)
            )]

    def is_empty(self) -> bool:
        with self._conn() as c:
            for table in ("about", "work", "dates"):
                if c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    return False
        return True


# ---------- Text for AIs and people ----------

def render_context(snap: dict, max_chars: int = 1800) -> str:
    """The short block injected into every AI turn. Done work is left out."""
    lines = ["Mindhall: the user's shared mind. If it disagrees with your own memory, Mindhall is right."]
    if snap["about"]:
        lines.append("About the user:")
        lines += [f"- {a['note']}" for a in snap["about"]]
    active = [w for w in snap["work"] if w["status"] != "Done"]
    if active:
        lines.append("Work:")
        for w in active:
            step = f", next: {w['next_step']}" if w["next_step"] else ""
            lines.append(f"- {w['name']} [{w['status']}{step}]")
    if snap["upcoming"]:
        lines.append("Upcoming dates:")
        for d in snap["upcoming"]:
            tag = f" ({d['work']})" if d["work"] else ""
            lines.append(f"- {d['day']}: {d['what']}{tag}")
    if snap["recent_changes"]:
        lines.append("Latest changes:")
        for ch in snap["recent_changes"]:
            lines.append(f"- {ch['at'][:10]} {ch['by']}: {ch['summary']}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 40].rstrip() + "\n- (more in Mindhall, use mindhall_read)"
    return text


def render_summary(snap: dict) -> str:
    """What the /mindhall command shows a person."""
    out = ["Mindhall", ""]
    out.append("About you")
    out += [f"  {a['id']}. {a['note']}" for a in snap["about"]] or ["  Nothing yet."]
    out += ["", "Work"]
    out += [
        f"  {w['name']} ({w['status']})" + (f": {w['next_step']}" if w["next_step"] else "")
        for w in snap["work"]
    ] or ["  Nothing yet."]
    out += ["", "Coming up"]
    out += [
        f"  {d['day']}  {d['what']}" + (f" ({d['work']})" if d["work"] else "")
        for d in snap["upcoming"]
    ] or ["  No dates in this window."]
    out += ["", "Latest changes"]
    out += [f"  {c['at'][:16].replace('T', ' ')}  {c['by']}: {c['summary']}" for c in snap["recent_changes"]] or [
        "  No changes yet."
    ]
    return "\n".join(out)
