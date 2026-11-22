"""Engine tests. They need no Hermes install: run `pytest` from the repo root."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine  # noqa: E402

TODAY = date(2026, 11, 7)


@pytest.fixture
def mind(tmp_path):
    return engine.Mind(tmp_path / "mind.db")


def test_new_mind_is_empty(mind):
    assert mind.is_empty()


def test_work_is_added_then_updated_and_both_are_logged(mind):
    mind.set_work("Hackathon entry", "Claude", next_step="Write the README")
    mind.set_work("hackathon entry", "Hermes Time", status="parked")
    work = mind.snapshot(today=TODAY)["work"][0]
    assert {k: work[k] for k in ("name", "status", "next_step")} == {
        "name": "Hackathon entry", "status": "Parked", "next_step": "Write the README"}
    assert work["steps"] == ["Write the README"] and "focus" not in work
    changes = mind.recent_changes()
    assert changes[0]["by"] == "Hermes Time"
    assert "Active to Parked" in changes[0]["summary"]
    assert changes[1]["by"] == "Claude"


def test_unchanged_update_writes_no_log_entry(mind):
    mind.set_work("Mindhall", "Claude")
    mind.set_work("Mindhall", "Claude", status="Active")
    assert len(mind.recent_changes()) == 1


def test_bad_status_is_refused(mind):
    with pytest.raises(engine.MindhallError):
        mind.set_work("Mindhall", "Claude", status="Someday")


def test_same_named_date_is_moved_not_duplicated(mind):
    mind.set_date("Launch", "2026-11-12", "Claude", work="Mindhall")
    mind.set_date("launch", "2026-11-20", "Hermes Time")
    dates = mind.all_dates(today=TODAY)
    assert len(dates) == 1
    assert dates[0]["day"] == "2026-11-20"
    assert dates[0]["work"] == "Mindhall"
    assert mind.recent_changes()[0]["summary"] == "Moved Launch from 2026-11-12 to 2026-11-20"


def test_bad_date_is_refused(mind):
    with pytest.raises(engine.MindhallError):
        mind.set_date("Launch", "Nov 20", "Claude")


def test_snapshot_only_shows_the_window(mind):
    mind.set_date("Demo video", "2026-11-11", "Claude")
    mind.set_date("Public launch", "2027-01-15", "Claude")
    upcoming = mind.snapshot(days_ahead=14, today=TODAY)["upcoming"]
    assert [d["what"] for d in upcoming] == ["Demo video"]


def test_context_skips_done_work_and_names_the_source_of_truth(mind):
    mind.add_note("Prefers short answers", "Claude")
    mind.set_work("Mindhall", "Claude", next_step="Ship the plugin")
    mind.set_work("Old site", "Claude", status="Done")
    mind.set_date("Launch", "2026-11-20", "Hermes Time")
    text = engine.render_context(mind.snapshot(today=TODAY))
    assert "Mindhall is right" in text
    assert "Prefers short answers" in text
    assert "Mindhall [Active, next: Ship the plugin]" in text
    assert "Old site" not in text.split("Latest changes:")[0]
    assert "2026-11-20: Launch" in text


def test_context_is_capped(mind):
    for i in range(200):
        mind.add_note(f"Note number {i} with some extra words to take space", "Claude")
    text = engine.render_context(mind.snapshot(today=TODAY), max_chars=800)
    assert len(text) <= 800


def test_two_connections_share_one_mind(tmp_path):
    path = tmp_path / "shared.db"
    hermes, mcp_server = engine.Mind(path), engine.Mind(path)
    hermes.set_date("Launch", "2026-11-20", "Hermes Time")
    assert mcp_server.all_dates(today=TODAY)[0]["what"] == "Launch"


def test_mind_moved_away_while_running_is_recreated_empty(tmp_path):
    folder = tmp_path / "mindhall"
    mind = engine.Mind(folder / "mind.db")
    mind.set_date("Launch", "2026-11-20", "Claude")
    folder.rename(tmp_path / "mindhall-real")  # the recording-day swap
    assert mind.is_empty()
    mind.add_note("Demo note", "Claude")
    assert len(mind.recent_changes()) == 1


def test_removing_a_note_is_logged(mind):
    note = mind.add_note("Works from a terrace", "Claude")
    mind.remove_note(note["id"], "Claude")
    assert mind.snapshot(today=TODAY)["about"] == []
    assert mind.recent_changes()[0]["summary"].startswith("Removed note")


def test_overlong_text_is_refused_and_nothing_is_saved(mind):
    with pytest.raises(engine.MindhallError):
        mind.add_note("x" * (engine.MAX_TEXT + 1), "Claude")
    with pytest.raises(engine.MindhallError):
        mind.set_work("Mindhall", "Claude", next_step="y" * (engine.MAX_TEXT + 1))
    assert mind.is_empty() and mind.recent_changes() == []


def latest(mind):
    return mind.recent_changes()[0]


def test_undo_reverses_adds_moves_and_removals_for_dates(mind):
    mind.set_date("Launch", "2026-11-20", "Claude")
    mind.set_date("Launch", "2026-11-22", "ChatGPT")
    mind.undo(latest(mind)["id"], "You")
    assert mind.all_dates(today=TODAY)[0]["day"] == "2026-11-20"
    assert latest(mind)["by"] == "You" and latest(mind)["summary"].startswith("Undid: Moved Launch")
    date_id = mind.all_dates(today=TODAY)[0]["id"]
    mind.remove_date(date_id, "Claude")
    mind.undo(latest(mind)["id"], "You")
    assert mind.all_dates(today=TODAY)[0]["id"] == date_id  # comes back with the same id


def test_undo_an_add_removes_it_and_undoing_the_undo_brings_it_back(mind):
    mind.add_note("Prefers short answers", "Claude")
    mind.undo(latest(mind)["id"], "You")
    assert mind.snapshot(today=TODAY)["about"] == []
    mind.undo(latest(mind)["id"], "You")
    assert mind.snapshot(today=TODAY)["about"][0]["note"] == "Prefers short answers"


def test_undo_work_updates_renames_and_removals(mind):
    mind.set_work("Mindhall", "Claude", next_step="Ship")
    work_id = mind.everything()["work"][0]["id"]
    mind.edit_work(work_id, "You", name="Mindhall v1", status="Parked")
    mind.undo(latest(mind)["id"], "You")
    assert {k: mind.everything()["work"][0][k] for k in ("name", "status", "next_step")} == {"name": "Mindhall", "status": "Active", "next_step": "Ship"}
    mind.remove_work(work_id, "You")
    mind.undo(latest(mind)["id"], "You")
    assert mind.everything()["work"][0]["name"] == "Mindhall"


def test_undo_refuses_when_something_changed_later(mind):
    mind.set_date("Launch", "2026-11-20", "Claude")
    first = latest(mind)["id"]
    mind.set_date("Launch", "2026-11-22", "ChatGPT")
    with pytest.raises(engine.MindhallError):
        mind.undo(first, "You")


def test_edit_note_and_rename_collisions(mind):
    note = mind.add_note("Works late", "Claude")
    mind.edit_note(note["id"], "Works early", "You")
    assert mind.snapshot(today=TODAY)["about"][0]["note"] == "Works early"
    mind.set_work("A", "You")
    mind.set_work("B", "You")
    b_id = [w for w in mind.everything()["work"] if w["name"] == "B"][0]["id"]
    with pytest.raises(engine.MindhallError):
        mind.edit_work(b_id, "You", name="a")  # names are unique, ignoring case


def test_everything_marks_which_changes_can_be_undone(mind):
    mind.add_note("Hello", "Claude")
    state = mind.everything()
    assert state["changes"][0]["undoable"] is True
    assert {"about", "work", "dates", "changes", "project_notes", "readers", "deleted"} <= set(state)


def test_same_named_dates_in_different_work_stay_separate(mind):
    mind.set_date("Launch", "2026-11-20", "Claude", work="Project A")
    mind.set_date("Launch", "2026-12-05", "ChatGPT", work="Project B")
    dates = {d["work"]: d["day"] for d in mind.all_dates(today=TODAY)}
    assert dates == {"Project A": "2026-11-20", "Project B": "2026-12-05"}
    mind.set_date("launch", "2026-11-22", "Hermes", work="project a")  # moves A's only
    dates = {d["work"]: d["day"] for d in mind.all_dates(today=TODAY)}
    assert dates == {"Project A": "2026-11-22", "Project B": "2026-12-05"}


def test_same_named_date_without_work_is_refused_when_ambiguous(mind):
    mind.set_date("Launch", "2026-11-20", "Claude", work="Project A")
    mind.set_date("Launch", "2026-12-05", "Claude", work="Project B")
    with pytest.raises(engine.MindhallError):
        mind.set_date("Launch", "2026-12-10", "Claude")
    assert len(mind.all_dates(today=TODAY)) == 2


def test_date_without_work_is_adopted_by_its_work(mind):
    mind.set_date("Launch", "2026-11-20", "Claude")
    mind.set_date("Launch", "2026-11-22", "ChatGPT", work="Mindhall")
    dates = mind.all_dates(today=TODAY)
    assert len(dates) == 1 and dates[0]["work"] == "Mindhall" and dates[0]["day"] == "2026-11-22"


def test_add_date_always_adds(mind):
    mind.set_date("Call", "2026-11-10", "Claude")
    mind.add_date("Call", "2026-11-17", "You")
    assert [d["day"] for d in mind.all_dates(today=TODAY)] == ["2026-11-10", "2026-11-17"]


def test_same_note_is_saved_once(mind):
    first = mind.add_note("Prefers short answers", "Claude")
    again = mind.add_note("prefers short answers", "ChatGPT")
    assert again["id"] == first["id"]
    assert len(mind.snapshot(today=TODAY)["about"]) == 1 and len(mind.recent_changes()) == 1


def test_existing_empty_file_gets_its_tables(tmp_path):
    path = tmp_path / "mind.db"
    path.touch()  # e.g. created by another process a moment earlier
    assert engine.Mind(path).is_empty()


def test_change_times_are_shown_in_local_time(mind, monkeypatch):
    mind.add_note("Hello", "Claude")
    snap = mind.snapshot(today=TODAY)
    snap["recent_changes"][0]["at"] = "2026-11-08T20:00:00+00:00"
    from datetime import datetime
    local = datetime.fromisoformat("2026-11-08T20:00:00+00:00").astimezone()
    assert local.strftime("%Y-%m-%d %H:%M") in engine.render_summary(snap)
    assert local.strftime("%Y-%m-%d") + " Claude" in engine.render_context(snap)


def test_everything_says_who_last_changed_each_item(mind):
    mind.add_note("Prefers short answers", "ChatGPT")
    morning = mind.add_note("Works mornings", "Claude")
    mind.edit_note(morning["id"], "Works best in the mornings", "You")
    mind.set_work("Mindhall", "Claude", "Active", "Launch")
    mind.set_work("Newsletter", "Claude")
    mind.set_work("Mindhall", "ChatGPT", next_step="Ship billing")
    mind.set_date("Launch", "2030-01-15", "Grok")
    e = mind.everything()
    assert [(n["note"], n["by"]) for n in e["about"]] == [("Prefers short answers", "ChatGPT"),
                                                          ("Works best in the mornings", "You")]
    assert {w["name"]: w["by"] for w in e["work"]} == {"Mindhall": "ChatGPT", "Newsletter": "Claude"}
    assert [(d["what"], d["by"]) for d in e["dates"]] == [("Launch", "Grok")]
    assert all(item["changed_at"] and item["changed_at"].endswith("+00:00") for item in e["about"] + e["work"] + e["dates"])


def test_an_undo_counts_as_the_last_change(mind):
    mind.set_work("Mindhall", "Claude", "Active", "Launch")
    mind.set_work("Mindhall", "ChatGPT", next_step="Something wrong")
    latest = mind.recent_changes(1)[0]["id"]
    mind.undo(latest, "You")
    work = mind.everything()["work"][0]
    assert (work["next_step"], work["by"]) == ("Launch", "You")


def test_a_reused_id_names_the_new_items_author(mind):
    first = mind.add_note("Old note", "ChatGPT")
    mind.remove_note(first["id"], "You")
    again = mind.add_note("New note", "Claude")
    assert again["id"] == first["id"]                   # SQLite reuses the freed id
    assert [(n["note"], n["by"]) for n in mind.everything()["about"]] == [("New note", "Claude")]


def test_items_whose_changes_predate_ids_have_no_author(mind):
    with mind._conn() as c:                             # as written by a version from before Undo
        c.execute("INSERT INTO about (note, updated_at) VALUES ('Old fact', '2026-01-01T00:00:00+00:00')")
        c.execute("INSERT INTO changes (at, by, room, summary, before, after) VALUES "
                  "('2026-01-01T00:00:00+00:00', 'Hermes', 'About you', 'Added note: Old fact', NULL, '{\"note\": \"Old fact\"}')")
    note = mind.everything()["about"][0]
    assert (note["note"], note["by"], note["changed_at"]) == ("Old fact", None, None)


def test_authors_are_found_without_sqlite_json_functions(mind):
    import sqlite3
    mind.add_note("A", "ChatGPT")
    b = mind.add_note("B", "Claude")
    mind.edit_note(b["id"], "B2", "You")
    mind.set_date("Launch", "2030-01-15", "Grok")

    class NoJSON:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if "json_extract" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return self.conn.execute(sql, *args)

    with mind._conn() as c:
        with_json = mind._last_changes(c)
        without = mind._last_changes(NoJSON(c))
    assert without == with_json and len(without) == 3


def test_the_engine_version_matches_plugin_yaml():
    import re
    manifest = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
    assert re.search(r'^version:\s*"?([^"\n]+)"?', manifest, re.M).group(1) == engine.VERSION
