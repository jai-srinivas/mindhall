"""Project pages, read receipts, Recently deleted (restore and forget) and the empty-mind setup."""
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine  # noqa: E402

TODAY = date(2026, 11, 7)


@pytest.fixture
def mind(tmp_path):
    return engine.Mind(tmp_path / "mind.db")


def latest(mind):
    return mind.recent_changes(1)[0]


def project(mind, name="Mindhall"):
    return next(w for w in mind.everything()["work"] if w["name"] == name)


# ---------- Projects: steps ----------

def test_a_project_holds_up_to_three_next_steps(mind):
    mind.set_work("Mindhall", "Claude", steps=["Record the demo", "record the demo", "", "Write the post", "Launch"])
    assert project(mind)["steps"] == ["Record the demo", "Write the post", "Launch"]
    assert project(mind)["next_step"] == "Record the demo"
    with pytest.raises(engine.MindhallError):
        mind.set_work("Mindhall", "Claude", steps=["A", "B", "C", "D"])
    with pytest.raises(engine.MindhallError):
        mind.set_work("Mindhall", "Claude", next_step="X", steps=["Y"])


def test_setting_only_the_next_step_keeps_the_later_ones(mind):
    mind.set_work("Mindhall", "Claude", steps=["A", "B", "C"])
    mind.set_work("Mindhall", "ChatGPT", next_step="X")
    assert project(mind)["steps"] == ["X", "B", "C"]
    assert latest(mind)["summary"] == "Updated Mindhall: next step: X"
    mind.edit_work(project(mind)["id"], "You", steps=["B", "C"])          # X ticked done
    assert project(mind)["steps"] == ["B", "C"]
    assert latest(mind)["summary"] == "Updated Mindhall: steps: B / C"


def test_projects_have_no_focus_flag_for_now(mind):
    # Focus was taken out on 17 Nov 2026, to come back later: no field, no tool parameter, no log wording.
    work = mind.set_work("Mindhall", "Claude", steps=["Record the demo"])
    assert "focus" not in work and "focus" not in project(mind)
    with pytest.raises(TypeError):
        mind.set_work("Mindhall", "Claude", focus=True)
    assert "focus" not in engine.render_context(mind.snapshot(today=TODAY)).lower()


def test_the_context_shows_active_projects_with_all_their_steps_and_memories(mind):
    mind.set_work("Newsletter", "Claude", next_step="Draft issue 4")
    mind.set_work("Mindhall", "Claude", steps=["Record the demo", "Write the post"])
    mind.add_note("Demo script is in the Notion page", "Claude", work="Mindhall")
    mind.set_work("Old site", "Claude", status="Done")
    text = engine.render_context(mind.snapshot(today=TODAY))
    work = text.split("Work:\n")[1]
    assert "- Mindhall [Active, next: Record the demo; then: Write the post]\n  - Demo script is in the Notion page\n" in work
    assert "- Newsletter [Active, next: Draft issue 4]" in work
    assert "Old site" not in text.split("Latest changes:")[0]


def test_parked_projects_get_one_line_in_the_context(mind):
    mind.set_work("Mindhall", "Claude", next_step="Ship the plugin")
    mind.set_work("Garden", "Claude", status="Parked", steps=["Order seeds", "Dig"])
    text = engine.render_context(mind.snapshot(today=TODAY))
    assert "- Mindhall [Active, next: Ship the plugin]" in text
    assert "- Garden [Parked, next: Order seeds]" in text      # not featured: one line, step 1 only


# ---------- Project memories ----------

def test_project_memories_are_separate_from_about_you(mind):
    mind.set_work("Mindhall", "Claude")
    saved = mind.add_note("Uses SQLite per user", "Claude", work="mindhall")
    assert saved["work"] == "Mindhall"                                   # the stored spelling
    assert mind.add_note("uses sqlite per user", "ChatGPT", work="Mindhall")["id"] == saved["id"]
    snap = mind.snapshot(today=TODAY)
    assert snap["about"] == []
    assert snap["work"][0]["notes"] == [{"id": saved["id"], "note": "Uses SQLite per user"}]
    assert latest(mind)["summary"] == "Added note to Mindhall: Uses SQLite per user"


def test_a_memory_for_an_unknown_project_names_the_real_ones(mind):
    mind.set_work("Mindhall", "Claude")
    with pytest.raises(engine.MindhallError, match="Projects: Mindhall"):
        mind.add_note("Something", "Claude", work="Mindhal")


def test_project_memories_can_be_edited_moved_removed_and_undone(mind):
    mind.set_work("Mindhall", "Claude")
    mind.set_work("Newsletter", "Claude")
    note = mind.add_note("Ship on Tuesday", "Claude", work="Mindhall")
    mind.edit_project_note(note["id"], "You", note="Ship on Wednesday")
    mind.edit_project_note(note["id"], "You", work="newsletter")
    assert latest(mind)["summary"] == "Moved note to Newsletter: Ship on Wednesday"
    mind.undo(latest(mind)["id"], "You")
    assert mind.everything()["project_notes"][0]["work"] == "Mindhall"
    mind.remove_project_note(note["id"], "You")
    assert mind.everything()["project_notes"] == []
    mind.undo(latest(mind)["id"], "You")
    assert mind.everything()["project_notes"][0]["note"] == "Ship on Wednesday"


def test_a_project_page_has_its_memories_dates_and_history(mind):
    mind.set_work("Mindhall", "Claude", next_step="Ship")
    mind.add_note("Uses SQLite", "Claude", work="Mindhall")
    mind.set_date("Launch", "2026-11-20", "ChatGPT", work="mindhall")
    mind.set_date("Dentist", "2026-11-12", "ChatGPT")
    page = mind.project(name="MINDHALL")
    assert page["work"]["name"] == "Mindhall"
    assert [n["note"] for n in page["notes"]] == ["Uses SQLite"]
    assert [d["what"] for d in page["dates"]] == ["Launch"]
    assert [h["summary"] for h in page["history"]] == [
        "Added Launch on 2026-11-20", "Added note to Mindhall: Uses SQLite", "Added Mindhall (Active)"]
    with pytest.raises(engine.MindhallError):
        mind.project(work_id=999)


# ---------- Renames carry dates and memories ----------

def test_a_rename_carries_dates_and_memories_and_undo_carries_them_back(mind):
    mind.set_work("Mindhall", "Claude")
    mind.set_date("Launch", "2026-11-20", "Claude", work="mindhall")
    mind.add_note("Uses SQLite", "Claude", work="Mindhall")
    work_id = project(mind)["id"]
    mind.edit_work(work_id, "You", name="Mindhall Cloud")
    state = mind.everything()
    assert state["dates"][0]["work"] == "Mindhall Cloud" and state["project_notes"][0]["work"] == "Mindhall Cloud"
    rename = latest(mind)["id"]
    mind.set_date("Beta", "2026-11-25", "Claude", work="Mindhall Cloud")   # added after the rename
    mind.undo(rename, "You")
    state = mind.everything()
    assert {d["work"] for d in state["dates"]} == {"Mindhall"}
    assert state["project_notes"][0]["work"] == "Mindhall"
    page = mind.project(work_id=work_id)
    assert {h["summary"] for h in page["history"]} >= {"Added Mindhall (Active)", "Added Beta on 2026-11-25"}


def test_undo_and_restore_follow_a_later_rename(mind):
    mind.set_work("Mindhall", "Claude")
    mind.set_date("Launch", "2026-11-20", "Claude", work="Mindhall")
    moved = latest(mind)["id"]
    mind.set_date("Launch", "2026-11-22", "ChatGPT", work="Mindhall")
    moved = latest(mind)["id"]
    date_id = mind.all_dates(today=TODAY)[0]["id"]
    mind.add_note("Uses SQLite", "Claude", work="Mindhall")
    note_id = mind.everything()["project_notes"][0]["id"]
    mind.remove_project_note(note_id, "You")
    removed = latest(mind)["id"]
    mind.edit_work(project(mind)["id"], "You", name="Mindhall Cloud")
    mind.undo(moved, "You")                                               # a move from before the rename
    assert mind.all_dates(today=TODAY)[0]["day"] == "2026-11-20"
    mind.restore(removed, "You")                                          # back under the new name
    assert mind.everything()["project_notes"][0]["work"] == "Mindhall Cloud"
    assert mind.all_dates(today=TODAY)[0]["id"] == date_id


def test_a_rename_onto_an_existing_name_is_refused(mind):
    mind.set_work("A", "You")
    mind.set_work("B", "You")
    with pytest.raises(engine.MindhallError):
        mind.edit_work(project(mind, "B")["id"], "You", name="a")


# ---------- Recently deleted, Restore and Forget ----------

def test_recently_deleted_lists_who_removed_what_and_restores_it(mind):
    note = mind.add_note("Prefers long answers", "ChatGPT")
    mind.remove_note(note["id"], "Claude")
    gone = mind.deleted()
    assert [(g["room"], g["by"], g["item"]["note"]) for g in gone] == [("About you", "Claude", "Prefers long answers")]
    mind.restore(gone[0]["change"], "You")
    assert [n["note"] for n in mind.everything()["about"]] == ["Prefers long answers"]
    assert mind.deleted() == []
    with pytest.raises(engine.MindhallError):
        mind.restore(gone[0]["change"], "You")                            # already back
    mind.undo(latest(mind)["id"], "You")                                   # undo the restore
    assert len(mind.deleted()) == 1


def test_restore_works_when_the_old_id_was_reused(mind):
    first = mind.add_note("Old fact", "ChatGPT")
    mind.remove_note(first["id"], "You")
    removal = latest(mind)["id"]
    second = mind.add_note("New fact", "Claude")
    assert second["id"] == first["id"]                                     # SQLite reused the id
    restored = mind.restore(removal, "You")
    assert restored["id"] != first["id"]
    assert sorted(n["note"] for n in mind.everything()["about"]) == ["New fact", "Old fact"]


def test_an_item_added_back_by_hand_leaves_recently_deleted(mind):
    note = mind.add_note("Prefers short answers", "ChatGPT")
    mind.remove_note(note["id"], "You")
    mind.add_note("prefers short answers", "Claude")
    assert mind.deleted() == []


def test_recently_deleted_keeps_thirty_days_and_lists_each_item_once(mind):
    for _ in range(2):
        note = mind.add_note("Prefers long answers", "ChatGPT")
        mind.remove_note(note["id"], "You")
    assert len(mind.deleted()) == 1
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert mind.deleted(now=later) == []


def test_a_removed_project_comes_back_with_its_memories_and_dates(mind):
    mind.set_work("Mindhall", "Claude", steps=["A", "B"])
    mind.add_note("Uses SQLite", "Claude", work="Mindhall")
    mind.set_date("Launch", "2026-11-20", "Claude", work="Mindhall")
    mind.remove_work(project(mind)["id"], "You")
    assert [g["label"] for g in mind.deleted()] == ["project Mindhall"]
    mind.restore(mind.deleted()[0]["change"], "You")
    work = project(mind)
    assert work["steps"] == ["A", "B"]
    page = mind.project(name="Mindhall")
    assert len(page["notes"]) == 1 and len(page["dates"]) == 1


def test_ais_are_told_what_was_removed_for_two_weeks(mind):
    note = mind.add_note("Prefers long answers", "ChatGPT")
    mind.remove_note(note["id"], "You")
    mind.set_date("Launch", "2026-11-20", "Claude", work="Mindhall")
    mind.remove_date(mind.all_dates(today=TODAY)[0]["id"], "You")
    mind.add_note("Works in IST", "Claude")
    snap = mind.snapshot(today=TODAY)
    assert snap["removed"] == ["Launch on 2026-11-20 (Mindhall)", "\"Prefers long answers\""]
    text = engine.render_context(snap)
    assert "Recently removed (don't use): Launch on 2026-11-20 (Mindhall); \"Prefers long answers\"" in text
    for i in range(200):
        mind.add_note(f"Note number {i} with some extra words to take space", "Claude")
    text = engine.render_context(mind.snapshot(today=TODAY), max_chars=800)
    assert len(text) <= 800 and "Recently removed (don't use)" in text     # survives the cap


def test_forget_erases_an_item_from_the_whole_history(mind, tmp_path):
    secret = "Lives at 12 Example Street"
    note = mind.add_note(secret, "ChatGPT")
    mind.edit_note(note["id"], secret + ", flat 3", "You")
    mind.remove_note(note["id"], "You")
    removal = latest(mind)["id"]
    mind.restore(removal, "You")
    mind.remove_note(mind.everything()["about"][0]["id"], "You")
    final = latest(mind)["id"]
    with pytest.raises(engine.MindhallError):
        mind.forget(removal, "You")                                        # an earlier, restored removal
    result = mind.forget(final, "You")
    assert result["entries"] == 5
    assert mind.deleted() == [] and mind.snapshot(today=TODAY)["removed"] == []
    summaries = [c["summary"] for c in mind.everything()["changes"]]
    assert summaries[0] == "Forgot a removed note"
    assert "Removed note (forgotten)" in summaries and "Undid: Removed note (forgotten)" in summaries
    assert not any("Example Street" in s for s in summaries)
    for suffix in ("", "-wal"):
        raw = Path(str(mind.path) + suffix)
        if raw.exists():
            assert b"Example Street" not in raw.read_bytes()
    with pytest.raises(engine.MindhallError):
        mind.undo(latest(mind)["id"], "You")                               # forgetting can't be undone
    assert not any(c["undoable"] for c in mind.everything()["changes"][:6])


def test_only_a_removed_item_can_be_forgotten(mind):
    note = mind.add_note("Prefers short answers", "ChatGPT")
    with pytest.raises(engine.MindhallError):
        mind.forget(latest(mind)["id"], "You")
    mind.remove_note(note["id"], "You")
    removal = latest(mind)["id"]
    mind.add_note("Prefers short answers", "Claude")                      # it's back
    with pytest.raises(engine.MindhallError):
        mind.forget(removal, "You")


# ---------- Read receipts ----------

def test_reads_are_noted_once_a_minute_unless_something_new_was_seen(mind):
    start = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
    assert mind.mark_read("ChatGPT", now=start) is True
    assert mind.mark_read("ChatGPT", now=start + timedelta(seconds=30)) is False
    mind.add_note("Prefers short answers", "Claude")
    assert mind.mark_read("ChatGPT", now=start + timedelta(seconds=40)) is True
    assert mind.mark_read("ChatGPT", now=start + timedelta(seconds=120)) is True
    reader = mind.everything()["readers"][0]
    assert (reader["by"], reader["seen"], reader["missed"]) == ("ChatGPT", 1, 0)
    assert all(c["by"] != "ChatGPT" for c in mind.recent_changes())     # reads are never changes


def test_an_ais_own_changes_never_count_as_missed(mind):
    mind.mark_read("ChatGPT")
    mind.add_note("Prefers short answers", "ChatGPT")
    mind.add_note("Works in IST", "You")
    assert mind.everything()["readers"][0]["missed"] == 1


def test_a_busy_file_never_fails_a_read(mind):
    blocker = sqlite3.connect(mind.path, timeout=5)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        assert mind.mark_read("ChatGPT") is False
    finally:
        blocker.rollback()
        blocker.close()


# ---------- The empty mind ----------

def test_an_empty_mind_asks_for_the_two_minute_setup(mind):
    snap = mind.snapshot(today=TODAY)
    assert snap["setup"] == engine.SETUP_PROMPT
    assert engine.render_context(snap) == engine.SETUP_PROMPT
    assert "offer once" in engine.SETUP_PROMPT
    mind.set_date("Launch", "2030-01-01", "Claude")                        # outside the window still counts
    assert "setup" not in mind.snapshot(today=TODAY)



# ---------- The person's time zone ----------

def test_the_time_zone_is_stored_checked_and_never_logged(mind):
    assert mind.settings()["time_zone"] is None
    assert mind.set_time_zone("Asia/Kolkata")["time_zone"] == "Asia/Kolkata"
    assert mind.everything()["settings"]["time_zone"] == "Asia/Kolkata"
    for bad in ("Mars/Olympus", "../../etc/passwd", "x" * 80):
        with pytest.raises(engine.MindhallError):
            mind.set_time_zone(bad)
    assert mind.settings()["time_zone"] == "Asia/Kolkata"      # a refused value changes nothing
    assert mind.recent_changes() == []                           # a preference, not part of the mind
    assert mind.set_time_zone("")["time_zone"] is None


def test_today_is_the_persons_today_not_the_servers(mind):
    # 20:00 UTC on 17 Nov is already 01:30 on 18 Nov in India, and still noon on 17 Nov in California.
    mind._clock = lambda: datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)
    mind.set_date("Call with the design partner", "2026-11-17", "Claude")
    mind.set_date("Mindhall launch", "2026-11-18", "Claude")
    mind.set_time_zone("Asia/Kolkata")
    assert [d["what"] for d in mind.snapshot()["upcoming"]] == ["Mindhall launch"]
    assert [d["what"] for d in mind.all_dates()] == ["Mindhall launch"]
    mind.set_time_zone("America/Los_Angeles")
    assert [d["what"] for d in mind.snapshot()["upcoming"]] == ["Call with the design partner", "Mindhall launch"]


def test_the_ai_block_dates_changes_in_the_persons_time_zone(mind, tmp_path):
    mind.set_work("Mindhall", "Claude")
    with sqlite3.connect(tmp_path / "mind.db") as conn:
        conn.execute("UPDATE changes SET at = '2026-11-17T20:00:00+00:00'")
    mind.set_time_zone("Asia/Kolkata")
    assert "- 2026-11-18 Claude: Added Mindhall (Active)" in engine.render_context(mind.snapshot())
    mind.set_time_zone("America/Los_Angeles")
    assert "- 2026-11-17 Claude: Added Mindhall (Active)" in engine.render_context(mind.snapshot())


def test_the_dashboard_gets_settings_version_and_the_full_change_count(mind):
    for i in range(3):
        mind.add_note(f"Fact {i}", "Claude")
    state = mind.everything(changes=2)
    assert len(state["changes"]) == 2 and state["change_count"] == 3
    assert state["version"] == engine.VERSION and state["settings"]["time_zone"] is None


# ---------- Pause saving and Clear my mind ----------

def test_pausing_stops_ai_writes_and_tells_every_ai(mind):
    mind.set_work("Mindhall", "Claude")
    assert mind.set_paused(True)["paused"] is True
    with pytest.raises(engine.MindhallError, match="paused"):
        mind.ensure_saving()
    assert engine.PAUSED in engine.render_context(mind.snapshot(today=TODAY))
    assert [c["summary"] for c in mind.recent_changes()] == ["Added Mindhall (Active)"]   # a preference, not a change
    assert mind.set_paused(False)["paused"] is False
    mind.ensure_saving()
    assert engine.PAUSED not in engine.render_context(mind.snapshot(today=TODAY))


def test_clearing_erases_everything_for_good_but_keeps_settings(mind, tmp_path):
    mind.set_time_zone("Asia/Kolkata")
    mind.add_note("Marker plum-kettle-41", "Claude")
    mind.set_work("Mindhall", "Claude", steps=["A"])
    mind.add_note("Uses SQLite", "Claude", work="Mindhall")
    mind.set_date("Launch", "2026-11-20", "Claude", work="Mindhall")
    mind.mark_read("Claude")
    assert mind.clear("You")["cleared"] == {"about": 1, "work": 1, "dates": 1, "project_notes": 1}
    state = mind.everything()
    assert (state["about"], state["work"], state["dates"], state["project_notes"], state["readers"]) == ([], [], [], [], [])
    assert state["change_count"] == 1 and state["changes"][0]["by"] == "You" and not state["changes"][0]["undoable"]
    assert state["settings"]["time_zone"] == "Asia/Kolkata"
    assert b"plum-kettle-41" not in b"".join(p.read_bytes() for p in tmp_path.glob("mind.db*"))
    text = engine.render_context(mind.snapshot())
    assert "cleared their Mindhall" in text and "shared mind is empty" in text   # drop the old, offer the setup


def test_ais_hear_about_a_clear_for_two_weeks(mind):
    mind.add_note("Prefers short answers", "Claude")
    mind.clear("You")
    assert "cleared" in mind.snapshot()
    mind._clock = lambda: datetime.now(timezone.utc) + timedelta(days=15)
    assert "cleared" not in mind.snapshot()


# ---------- What the dashboard's charts read ----------

def test_each_change_says_which_project_it_belongs_to_after_renames(mind):
    mind.set_work("Mindhall", "Claude")
    mind.set_date("Launch", "2026-11-20", "Claude", work="Mindhall")
    mind.add_note("Uses SQLite", "Claude", work="Mindhall")
    mind.add_note("Prefers short answers", "ChatGPT")
    mind.edit_work(project(mind)["id"], "You", name="Mindhall Cloud")
    works = {c["summary"]: c["work"] for c in mind.everything()["changes"]}
    assert works["Added Mindhall (Active)"] == "Mindhall Cloud"            # followed through the rename
    assert works["Added note to Mindhall: Uses SQLite"] == "Mindhall Cloud"
    assert works["Updated Mindhall: renamed to Mindhall Cloud"] == "Mindhall Cloud"
    assert works["Added note: Prefers short answers"] is None              # About you belongs to no project
    assert [w for s, w in works.items() if s.startswith("Added Launch")] == ["Mindhall Cloud"]


def test_daily_counts_cover_two_weeks_in_the_persons_time_zone(mind, tmp_path):
    mind.set_work("Mindhall", "Claude")
    mind.add_note("Prefers short answers", "ChatGPT")
    mind.add_note("Writes in British English", "ChatGPT")
    with sqlite3.connect(tmp_path / "mind.db") as conn:   # all at 20:00 UTC on 17 Nov
        conn.execute("UPDATE changes SET at = '2026-11-17T20:00:00+00:00'")
    mind._clock = lambda: datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)
    mind.set_time_zone("Asia/Kolkata")                        # already 18 Nov there
    assert mind.everything()["daily"] == [
        {"day": "2026-11-18", "by": "ChatGPT", "work": None, "n": 2},
        {"day": "2026-11-18", "by": "Claude", "work": "Mindhall", "n": 1}]
    mind.set_time_zone("America/Los_Angeles")                 # still 17 Nov there
    assert {d["day"] for d in mind.everything()["daily"]} == {"2026-11-17"}
    mind._clock = lambda: datetime(2026, 12, 10, tzinfo=timezone.utc)
    assert mind.everything()["daily"] == []                   # older than two weeks


# ---------- Switching one AI app off or on ----------

def test_switching_an_app_off_stops_it_completely_and_is_a_signed_change(mind):
    mind.set_work("Mindhall", "Claude")
    assert mind.set_ai_on("ChatGPT", False, "You")["off"] == ["ChatGPT"]
    with pytest.raises(engine.MindhallError, match="turned off for this app"):
        mind.ensure_on("ChatGPT")
    with pytest.raises(engine.MindhallError):
        mind.ensure_on("  chatgpt ")                                   # the same app, however it's written
    mind.ensure_on("Claude")                                           # other apps are untouched
    entry = mind.everything()["changes"][0]
    assert (entry["by"], entry["room"], entry["summary"], entry["undoable"]) == (
        "You", engine.AI_ROOM, "Turned off ChatGPT", True)
    assert mind.set_ai_on("ChatGPT", False, "You")["off"] == ["ChatGPT"]   # already off: nothing new logged
    assert mind.everything()["change_count"] == 2
    assert mind.set_ai_on("ChatGPT", True, "You")["off"] == []
    mind.ensure_on("ChatGPT")
    with pytest.raises(engine.MindhallError):
        mind.set_ai_on("   ", False, "You")


def test_undoing_a_switch_switches_back_and_is_itself_undoable(mind):
    mind.set_ai_on("ChatGPT", False, "You")
    off_id = mind.everything()["changes"][0]["id"]
    assert mind.undo(off_id, "You") == {"ai": "ChatGPT", "on": True, "ref": None}
    mind.ensure_on("ChatGPT")
    back = mind.everything()["changes"][0]
    assert back["summary"] == "Undid: Turned off ChatGPT" and back["by"] == "You" and back["undoable"]
    mind.undo(back["id"], "You")                                       # undoing the undo: off again
    assert not mind.is_on("ChatGPT")
    assert mind.everything()["changes"][0]["summary"] == "Undid: Undid: Turned off ChatGPT"
    mind.set_ai_on("ChatGPT", True, "You")
    with pytest.raises(engine.MindhallError, match="switched since"):
        mind.undo(off_id, "You")                                       # an old entry never fights a newer switch


def test_switching_apps_is_not_news_for_the_ais(mind):
    mind.set_work("Mindhall", "Claude")
    mind.mark_read("Claude")
    mind.set_ai_on("ChatGPT", False, "You")
    claude = next(r for r in mind.everything()["readers"] if r["by"] == "Claude")
    assert claude["missed"] == 0                                       # not a change Claude has missed
    assert all(c["room"] != engine.AI_ROOM for c in mind.recent_changes())
    assert "Turned off ChatGPT" not in engine.render_context(mind.snapshot(today=TODAY))


def test_a_switched_off_app_stays_off_after_clear_my_mind(mind):
    mind.set_ai_on("ChatGPT", False, "You")
    mind.clear("You")
    assert not mind.is_on("ChatGPT") and mind.settings()["off"] == ["ChatGPT"]


def test_one_change_can_be_looked_up_without_its_contents(mind):
    note = mind.add_note("Prefers short answers", "Claude")
    first = mind.everything()["changes"][0]
    got = mind.change(first["id"])
    assert set(got) == {"id", "at", "by", "room"} and got["by"] == "Claude" and got["at"] == first["at"]
    assert "Prefers" not in str(got)                                       # no contents
    mind.set_ai_on("ChatGPT", False, "You", ref="client-1")
    switch = mind.change(mind.everything()["changes"][0]["id"])
    assert switch["ai"] == {"name": "ChatGPT", "ref": "client-1", "on_before": True, "on_after": False}
    assert mind.change(99999) is None and mind.change("nope") is None
    assert note["id"]
