"""Older copies of the engine share the same mind file (other Hermes profiles, older installs).

These tests run the real v0.3.0 and v0.3.4 engines from tests/legacy against files the new
engine has upgraded, and the other way round.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine  # noqa: E402

LEGACY = Path(__file__).resolve().parent / "legacy"


def _load(version):
    spec = importlib.util.spec_from_file_location(f"legacy_engine_{version}", LEGACY / f"engine_{version}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OLD = {"v0.3.0": _load("v030"), "v0.3.4": _load("v034")}


@pytest.fixture(params=sorted(OLD))
def old(request):
    return OLD[request.param]


def columns(path, table):
    with sqlite3.connect(path) as conn:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_an_old_file_is_upgraded_and_keeps_its_data(tmp_path, old):
    path = tmp_path / "mind.db"
    before = old.Mind(path)
    before.add_note("Prefers short answers", "Hermes")
    before.set_work("Mindhall", "Hermes", next_step="Ship the plugin")
    before.set_date("Launch", "2026-11-20", "Hermes", work="Mindhall")
    state = engine.Mind(path).everything()
    assert [n["note"] for n in state["about"]] == ["Prefers short answers"]
    work = state["work"][0]
    assert (work["name"], work["steps"]) == ("Mindhall", ["Ship the plugin"])
    assert state["dates"][0]["work"] == "Mindhall"
    assert "later_steps" in columns(path, "work")
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == engine.SCHEMA_VERSION


def test_the_upgrade_runs_once_and_survives_a_reset_version(tmp_path):
    path = tmp_path / "mind.db"
    engine.Mind(path)
    engine.Mind(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 0")      # e.g. a tool that rewrote the file
    mind = engine.Mind(path)
    mind.set_work("Mindhall", "Claude", steps=["A", "B"])
    assert columns(path, "work").count("later_steps") == 1
    assert mind.everything()["work"][0]["steps"] == ["A", "B"]


def test_a_file_an_old_engine_recreated_is_upgraded_again(tmp_path):
    path = tmp_path / "mind.db"
    new = engine.Mind(path)
    new.set_work("Mindhall", "Claude", steps=["A", "B"])
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    OLD["v0.3.4"].Mind(path).set_work("Newsletter", "Hermes", next_step="Draft")
    new.set_work("Newsletter", "Claude", steps=["Draft", "Send"])
    assert new.everything()["work"][0]["steps"] == ["Draft", "Send"]


def test_old_engines_keep_working_on_an_upgraded_file(tmp_path, old):
    path = tmp_path / "mind.db"
    new = engine.Mind(path)
    new.set_work("Mindhall", "Claude", steps=["A", "B", "C"])
    new.add_note("Launch copy is in the Notion page", "Claude", work="Mindhall")
    mind = old.Mind(path)
    note = mind.add_note("Works in IST", "Hermes")
    mind.set_work("Newsletter", "Hermes", next_step="Draft issue 4")
    mind.set_date("Launch", "2026-11-20", "Hermes", work="Mindhall")
    mind.undo(mind.recent_changes(1)[0]["id"], "You")             # its own changes stay undoable
    mind.remove_note(note["id"], "Hermes")
    assert mind.snapshot()["about"] == []                          # project memories never show as About you
    assert {w["name"] for w in mind.everything()["work"]} == {"Mindhall", "Newsletter"}
    steps = {w["name"]: w["steps"] for w in new.everything()["work"]}
    assert steps == {"Mindhall": ["A", "B", "C"], "Newsletter": ["Draft issue 4"]}   # its project took the default


@pytest.mark.parametrize("next_step, steps", [("X", ["X", "B", "C"]), ("B", ["B", "C"]), ("", ["B", "C"])])
def test_an_old_next_step_write_keeps_the_later_steps(tmp_path, old, next_step, steps):
    path = tmp_path / "mind.db"
    new = engine.Mind(path)
    new.set_work("Mindhall", "Claude", steps=["A", "B", "C"])
    old.Mind(path).set_work("Mindhall", "Hermes", next_step=next_step)
    assert new.everything()["work"][0]["steps"] == steps


def test_old_engines_refuse_to_undo_new_project_changes(tmp_path, old):
    path = tmp_path / "mind.db"
    new = engine.Mind(path)
    new.set_work("Mindhall", "Claude", steps=["A", "B"])
    new.edit_work(new.everything()["work"][0]["id"], "You", status="Parked")
    with pytest.raises(old.MindhallError):
        old.Mind(path).undo(new.recent_changes(1)[0]["id"], "You")
    assert new.everything()["work"][0]["status"] == "Parked"


def test_the_new_engine_undoes_old_work_changes(tmp_path, old):
    path = tmp_path / "mind.db"
    mind = old.Mind(path)
    mind.set_work("Mindhall", "Hermes", next_step="Ship")
    mind.set_work("Mindhall", "Hermes", status="Parked")
    new = engine.Mind(path)
    new.undo(new.recent_changes(1)[0]["id"], "You")
    work = new.everything()["work"][0]
    assert (work["status"], work["steps"]) == ("Active", ["Ship"])


def test_old_engines_undo_new_notes_and_dates(tmp_path, old):
    path = tmp_path / "mind.db"
    new = engine.Mind(path)
    new.add_note("Prefers short answers", "Claude")
    new.set_date("Launch", "2026-11-20", "Claude")
    mind = old.Mind(path)
    mind.undo(new.recent_changes(1)[0]["id"], "You")
    mind.undo(new.recent_changes(3)[2]["id"], "You")
    state = new.everything()
    assert state["about"] == [] and state["dates"] == []


def test_a_restore_by_an_old_engine_hides_the_item_from_recently_deleted(tmp_path, old):
    path = tmp_path / "mind.db"
    new = engine.Mind(path)
    note = new.add_note("Prefers short answers", "Claude")
    new.remove_note(note["id"], "You")
    assert len(new.deleted()) == 1
    old.Mind(path).undo(new.recent_changes(1)[0]["id"], "You")
    assert new.deleted() == []


def test_old_engines_ignore_the_settings_table(tmp_path, old):
    path = tmp_path / "mind.db"
    new = engine.Mind(path)
    new.set_time_zone("Asia/Kolkata")
    mind = old.Mind(path)
    mind.set_work("Newsletter", "Hermes", next_step="Draft issue 4")
    assert mind.snapshot()["work"][0]["name"] == "Newsletter"
    assert new.settings()["time_zone"] == "Asia/Kolkata"
