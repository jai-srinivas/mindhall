"""The dashboard page (web/index.html) keeps its security rules and the markers that servers embedding it rely on."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")


def test_embed_markers_appear_exactly_once():
    # Servers that embed this page insert their own links and scripts at exactly these spots,
    # so each must appear exactly once.
    for marker in ('<div class="foot">', '<script>\n(() => {\n  "use strict";', "</head>"):
        assert PAGE.count(marker) == 1, marker


def test_loads_nothing_from_other_sites():
    assert not re.search(r"""\b(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//""", PAGE, re.I)
    assert not re.search(r"""url\(\s*["']?\s*(?:https?:)?//""", PAGE, re.I)
    assert not re.search(r"""\bfetch\(\s*["'`](?:https?:)?//""", PAGE)
    assert "@import" not in PAGE


def test_font_is_embedded_not_fetched():
    assert "src: url(data:font/woff2;base64," in PAGE
    assert "fonts.googleapis" not in PAGE and "fonts.gstatic" not in PAGE


def test_sends_no_referrer():
    assert '<meta name="referrer" content="no-referrer">' in PAGE


def test_data_is_inserted_as_text_never_as_html():
    # The only markup the page ever parses is its own fixed icon set.
    assert PAGE.count("innerHTML") == 1
    assert "t.innerHTML = '<svg" in PAGE and "+ ICONS[name] +" in PAGE
    for risky in ("outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert risky not in PAGE, risky


def test_inline_styles_only_take_fixed_values():
    # Inks and widths come from fixed lists; no data is ever written into a style attribute.
    script = PAGE.split('<script>\n(() => {', 1)[1]
    styles = re.findall(r'[{,]\s*style:\s*([^,}]+)', script)
    assert styles
    for style in styles:
        assert re.fullmatch(r'"color:"\s*\+\s*inkFor\(name\)|"width:"\s*\+\s*\(40 \+ i \* 10\)\s*\+\s*"%"', style.strip()), style


def _log_logic() -> str:
    start = PAGE.index("// ---------- log logic")
    return PAGE[start:PAGE.index("// ---------- end of log logic ----------")]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_undo_matching_and_log_wording():
    script = _log_logic() + r"""
const ch = (id, room, summary) => ({ id, room, summary });
const out = {};
// Newest first, as the API sends them.
let log = [ch(3, "Dates", "Undid: Moved Launch from 2026-11-20 to 2026-11-22"),
           ch(2, "Dates", "Moved Launch from 2026-11-20 to 2026-11-22"),
           ch(1, "Dates", "Added Launch on 2026-11-20")];
let m = undoMap(log);
out.undo = { struck: [...m.struck.keys()], by: m.struck.get(2).id, undid: m.undid.get(3).id };
m = undoMap([ch(4, "Dates", "Undid: Undid: Moved Launch from 2026-11-20 to 2026-11-22"), ...log]);
out.redo = [...m.struck.keys()];
m = undoMap([ch(6, "About you", "Undid: Added note: x"), ch(5, "Work", "Added note: x"), ch(4, "About you", "Added note: x")]);
out.room = [...m.struck.keys()];
out.words = [
  friendly("Added note: Prefers short answers", "About you"),
  friendly("Updated Mindhall: next step: Ship it", "Work"),
  friendly("Updated Mindhall: status Active to Parked; next step: none", "Work"),
  friendly("Moved Launch from 2026-11-20 to 2026-11-22", "Dates"),
  friendly("Added Launch on 2026-11-20", "Dates"),
  friendly("Removed Launch (2026-11-20)", "Dates"),
  friendly("Undid: Added note: x", "About you"),
  friendly("Added note: Born on 1990-05-01", "About you"),
  friendly("Added Fiverr gigs (Active)", "Work"),
];
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["undo"] == {"struck": [2], "by": 3, "undid": 2}
    assert out["redo"] == [3]  # undoing the undo brings the move back
    assert out["room"] == [4]  # the same words in another room are a different change
    words = out["words"]
    assert words[0] == "Added memory: Prefers short answers"
    assert words[1] == "Updated Mindhall: up next: Ship it"
    assert words[2] == "Updated Mindhall: status Active to Parked; up next: none"
    for text in words[3:6]:
        assert not re.search(r"\d{4}-\d{2}-\d{2}", text), text
    assert words[3].startswith("Moved Launch from ") and " to " in words[3]
    assert words[6] == "Undid: Added memory: x"
    assert words[7] == "Added memory: Born on 1990-05-01"  # what someone wrote stays as written
    assert words[8] == "Added Fiverr gigs (Active)"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_project_wording_and_reading_lines():
    script = _log_logic() + r"""
const now = Date.parse("2026-11-17T10:00:00Z");
console.log(JSON.stringify({
  words: [
    friendly("Updated Mindhall: steps: A / B", "Projects"),
    friendly("Updated Mindhall: next step: Ship", "Projects"),
    friendly("Added note to Mindhall: Uses SQLite", "Project notes"),
    friendly("Moved note to Newsletter: Ship Tuesday", "Project notes"),
    friendly("Removed note (forgotten)", "About you"),
    friendly("Forgot a removed note", "About you"),
    friendly("Undid: Removed note from Mindhall: Uses SQLite", "Project notes"),
  ],
  reads: [
    readerLine(null, now),
    readerLine({ at: "2026-11-17T09:55:00Z", missed: 0 }, now),
    readerLine({ at: "2026-11-17T08:00:00Z", missed: 1 }, now),
    readerLine({ at: "2026-11-17T08:00:00Z", missed: 3 }, now),
  ],
}));
"""
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["words"] == [
        "Updated Mindhall: next steps: A / B",
        "Updated Mindhall: up next: Ship",
        "Added memory to Mindhall: Uses SQLite",
        "Moved memory to Newsletter: Ship Tuesday",
        "Removed memory (forgotten)",
        "Forgot a removed memory",
        "Undid: Removed memory from Mindhall: Uses SQLite",
    ]
    assert out["reads"] == [
        "No reads recorded yet",
        "Last read 5m ago · up to date",
        "Last read 2h ago · hasn’t seen 1 change",
        "Last read 2h ago · hasn’t seen 3 changes",
    ]


def test_style_properties_set_from_script_only_take_fixed_values():
    # Besides style attributes, the script sets an app's signature ink (from the fixed list) and a growing
    # text box's height (measured from the box itself). Never data.
    script = PAGE.split('<script>\n(() => {', 1)[1]
    assert set(re.findall(r'\.style\.(\w+)\s*=\s*([^;]+);', script)) == {
        ("color", "inkFor(c.name)"), ("height", '"auto"'), ("height", 't.scrollHeight + "px"')}


def test_settings_is_reachable_and_irreversible_actions_need_a_typed_word():
    assert '<a class="settings-link" id="settings-link" href="#settings">' in PAGE
    assert "settings: renderSettings" in PAGE
    assert 'typedConfirm("clear", "Clear my mind"' in PAGE and 'typedConfirm("delete", "Delete my account"' in PAGE
    assert 'api("POST", "clear", { confirm: word })' in PAGE
    assert "recent_sign_in" in PAGE          # deleting an account asks you to sign in again first


def _run_node(script, tz="UTC"):
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, env={"TZ": tz, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_time_zones_are_shown_by_their_current_names():
    block = PAGE[PAGE.index("  const MODERN = {"):PAGE.index("  const browserZone")]
    out = _run_node(block + 'console.log(JSON.stringify(["Asia/Calcutta", "Europe/Kiev", "Europe/London"].map(modern)));')
    assert out == ["Asia/Kolkata", "Europe/Kyiv", "Europe/London"]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_activity_counts_come_from_the_server_or_fall_back_to_the_latest_changes():
    iso = re.search(r"  const isoDay = .*;\n", PAGE).group(0)
    daily = PAGE[PAGE.index("  function dailyRows() {"):PAGE.index("  function activity(span) {")]
    script = "let state;\n" + iso + daily + r"""
state = { daily: [{ day: "2026-11-17", by: "Claude", work: "Mindhall", n: 4 }], changes: [] };
const fromServer = dailyRows();
state = { changes: [
  { at: "2026-11-17T10:00:00Z", by: "Claude", work: "Mindhall" },
  { at: "2026-11-17T11:00:00Z", by: "Claude", work: "Mindhall" },
  { at: "2026-11-16T09:00:00Z", by: "ChatGPT", work: null },
] };
console.log(JSON.stringify({ fromServer, fallback: dailyRows().sort((a, b) => (a.day < b.day ? -1 : 1)) }));
"""
    out = _run_node(script)
    assert out["fromServer"] == [{"day": "2026-11-17", "by": "Claude", "work": "Mindhall", "n": 4}]
    assert out["fallback"] == [{"day": "2026-11-16", "by": "ChatGPT", "work": None, "n": 1},
                               {"day": "2026-11-17", "by": "Claude", "work": "Mindhall", "n": 2}]
