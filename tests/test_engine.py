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
    assert work == {"name": "Hackathon entry", "status": "Parked", "next_step": "Write the README"}
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
    assert mind.everything()["work"][0] | {"id": 0} == {"id": 0, "name": "Mindhall", "status": "Active", "next_step": "Ship"}
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
    assert set(state) == {"about", "work", "dates", "changes"}
