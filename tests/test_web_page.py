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
        assert re.fullmatch(r'"background:"\s*\+\s*inkFor\(name\)|"width:"\s*\+\s*\(40 \+ i \* 10\)\s*\+\s*"%"', style.strip()), style


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
    # Besides style attributes, the script only sets a growing text box's height (measured from the box
    # itself). Never data.
    script = PAGE.split('<script>\n(() => {', 1)[1]
    assert set(re.findall(r'\.style\.(\w+)\s*=\s*([^;]+);', script)) == {
        ("height", '"auto"'), ("height", 't.scrollHeight + "px"')}


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


def test_the_logo_is_drawn_in_the_page_itself():
    # The mark (five dots joined by one line) appears in the browser-tab icon, the rail and the phone bar,
    # always drawn inline so nothing is fetched for it.
    assert PAGE.count("M12 49L20 15L32 37L44 15L52 49") == 3
    icon = re.search(r'<link rel="icon" href="([^"]+)">', PAGE).group(1)
    assert icon.startswith("data:image/svg+xml,")
    assert PAGE.count('<svg class="mark" viewBox="0 0 64 64" aria-hidden="true"><path class="line"') == 2
    for old in ('class="orbit"', "--mark-ring", "--mark-orbit", "--mark-core"):
        assert old not in PAGE, old


def test_there_is_no_brand_colour():
    # No brand colour (Jai, 21 Nov 2026): buttons, active icons, switches and focus rings use the text
    # colour, and "You" writes in it too. Lavender was dropped because Mem0 uses almost the same colour.
    assert PAGE.count("--accent: #eeedf6;") == 2 and PAGE.count("--accent: #15161f;") == 1
    assert PAGE.count("--you: #eeedf6;") == 2 and PAGE.count("--you: #15161f;") == 1
    low = PAGE.lower()
    for lavender in ("b9a6f4", "6d55d6", "8b73e6", "185, 166, 244", "109, 85, 214"):
        assert lavender not in low, lavender
    assert ".spot-eyebrow { color: var(--you)" not in PAGE


def test_names_are_plain_text_with_a_small_ink_dot():
    # AI names are written in the text colour, never in their ink; the ink only fills a small dot.
    assert 'el("span", { class: "sig", style: "color:"' not in PAGE
    assert 'class: "sig-dot", "aria-hidden": "true", style: "background:" + inkFor(name)' in PAGE
    assert ".sig { font: 600 14px/1.4 var(--display); letter-spacing: -.005em; color: var(--text); }" in PAGE


def test_the_logo_is_black_and_white():
    # No brand colour (Jai, 21 Nov 2026): the mark is the text colour in both themes, and the tab icon is
    # a white M on a black tile. Lavender was dropped because Mem0 uses almost the same colour.
    assert PAGE.count("--logo: #eeedf6;") == 2 and PAGE.count("--logo: #15161f;") == 1
    icon = re.search(r'<link rel="icon" href="([^"]+)">', PAGE).group(1)
    assert "%230A0A0C" in icon and "%23F6F4EF" in icon
    assert "b9a6f4" not in icon.lower() and "B9A6F4" not in icon


def test_each_ai_has_an_on_off_switch_in_settings():
    assert 'save("POST", "ai-switch", { name, on }' in PAGE                    # self-hosted: the server route
    assert "aiSwitch(n, !r.off, (on) => setAiOn(n, on))" in PAGE              # on every reader row
    assert 'typeof c.off === "boolean"' in PAGE                               # hosted: only when the server has the switch
    assert 'connectionAct(c, on ? "on" : "off"' in PAGE
    assert "Anyone with your secret address. To cut every app off, change the secret." in PAGE   # self-hosted's real lock


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_a_switched_off_ai_stays_listed_and_its_switch_undoes_like_any_change():
    keys = PAGE[PAGE.index("  const appKey = "):PAGE.index("  function syncRow(n) {")]
    names = PAGE[PAGE.index("  function aiNames() {"):PAGE.index("  function whatTheySee() {")]
    script = "let state;\n" + keys + names + _log_logic() + r"""
state = { changes: [{ id: 3, at: "2026-11-22T11:00:00Z", by: "Mindhall", room: "AI apps", summary: "Turned off Grok" },
                    { id: 2, at: "2026-11-22T10:00:00Z", by: "You", room: "AI apps", summary: "Turned off Hermes  Money" },
                    { id: 1, at: "2026-11-21T10:00:00Z", by: "Claude", room: "About you", summary: "Added note: x" }],
          readers: [{ by: "Claude", at: "2026-11-22T09:00:00Z" }],
          settings: { off: ["ChatGPT", "hermes money"] } };
const m = undoMap([{ id: 3, room: "AI apps", summary: "Undid: Turned off ChatGPT" }, { id: 2, room: "AI apps", summary: "Turned off ChatGPT" }]);
console.log(JSON.stringify({ names: aiNames(), off: ["chatgpt", " ChatGPT ", "Hermes  Money", "Claude"].map(isOff),
                             struck: [...m.struck.keys()] }));
"""
    out = _run_node(script)
    assert out["names"] == ["Claude", "ChatGPT", "hermes money"]   # quiet but off: still listed, after the active ones
    # a switch signed by the plan ("Mindhall") never shows Mindhall as one of your AIs
    assert out["off"] == [True, True, True, False]
    assert out["struck"] == [2]                                    # undoing a switch strikes it through


def test_plans_show_only_when_the_hosted_server_sends_one():
    # Upgrade and billing go through our own server, which answers with the address; the page holds none.
    assert 'go("billing/checkout", { interval: o.interval })' in PAGE and 'go("billing/portal")' in PAGE
    assert "location.href = r.url" in PAGE and "dodopayments" not in PAGE.lower()
    assert "hosted && account.plan ? planPanel(account.plan) : null" in PAGE
    assert 'c.off_reason === "plan" ? PLAN_OFF_LINE : OFF_LINE' in PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_plan_buttons_go_under_the_text_on_narrower_screens():
    # the hosted upgrade buttons are wide ("Yearly: $10/month, billed $120 a year"), so they must not squeeze the text
    assert 'srow(title, text, controls, "srow-plan")' in PAGE
    assert "@media (max-width: 1100px) { .srow-plan { grid-template-columns: minmax(0, 1fr); }" in PAGE


def test_plan_limits_follow_the_plan_and_default_to_none():
    plural = re.search(r"  const plural = .*;\n", PAGE).group(0)
    block = PAGE[PAGE.index("  const planOf = "):PAGE.index("  function planPanel(p) {")]
    script = "let state = null, account = null;\n" + plural + block + r"""
const live = [{ off: false }, { off: false }, { off: true }];
const out = { selfHosted: [restoreDays(), aisMeta(live), planOf()] };
state = { plan: { name: "free", ai_limit: 2, history_days: 7, restore_days: 7 } };
out.freeFromMind = [restoreDays(), aisMeta(live)];
account = { plan: { name: "pro", ai_limit: null, history_days: null, restore_days: 30 } };
out.proFromAccount = [restoreDays(), aisMeta(live)];
console.log(JSON.stringify(out));
"""
    out = _run_node(script)
    assert out["selfHosted"] == [30, "3 apps connected", None]          # your own server: no plan, no limits
    assert out["freeFromMind"] == [7, "2 of 2 apps on"]
    assert out["proFromAccount"] == [30, "3 apps connected"]            # Settings' fresher account wins


def _setup_logic() -> str:
    start = PAGE.index("// ---------- setup logic")
    return PAGE[start:PAGE.index("// ---------- end of setup logic ----------")]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_setup_opens_by_itself_once_on_a_hosted_account_with_no_ai_connected():
    script = _setup_logic() + r"""
const app = (name, extra) => Object.assign({ client_id: name, name, disconnected: false, revoked: false }, extra || {});
const hosted = (connections) => ({ mcp_url: "https://mcp.example/mcp", connections });
console.log(JSON.stringify([
  shouldAutoSetup(null, {}),                                              // your own server: no account
  shouldAutoSetup({ connections: [] }, {}),                               // no address to connect to
  shouldAutoSetup(hosted([]), {}),                                        // new hosted account: opens
  shouldAutoSetup(hosted([]), { skipped: true }),                         // skipped once: never again here
  shouldAutoSetup(hosted([]), { done: true }),
  shouldAutoSetup(hosted([app("ChatGPT")]), {}),                          // an AI is connected already
  shouldAutoSetup(hosted([app("ChatGPT", { disconnected: true }), app("Grok", { revoked: true })]), {}),
  [0, 1, 2, 3].map((n) => setupStartStep(Array(n).fill(app("x")))),
]));
"""
    assert _run_node(script) == [False, False, True, False, False, False, True, ["connect1", "connect2", "try", "try"]]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the page's script")
def test_setup_sees_an_ai_save_and_another_ai_read_it():
    script = _setup_logic() + r"""
const ch = (id, by, at, room) => ({ id, by, at, room: room || "About you", summary: "Added note: x" });
// newest first, as the API sends them; "Try it" began when change 5 was the newest
const st = { changes: [ch(9, "Claude", "2026-11-24T10:09:00+00:00"), ch(8, "ChatGPT", "2026-11-24T10:08:00+00:00"),
                       ch(7, "You", "2026-11-24T10:07:00+00:00"), ch(6, "Mindhall", "2026-11-24T10:06:00+00:00", "AI apps"),
                       ch(5, "ChatGPT", "2026-11-24T10:05:00+00:00")],
             readers: [{ by: "ChatGPT", at: "2026-11-24T10:20:00+00:00" }, { by: "Claude", at: "2026-11-24T10:07:30+00:00" }] };
const saved = setupSaved(st, 5);
const out = { saved: saved && saved.id, none: setupSaved(st, 9), notStarted: setupSaved(st, undefined),
              early: setupRead(st, saved) };                                // Claude read before ChatGPT saved
st.readers.push({ by: "Claude", at: "2026-11-24T10:12:00+00:00" });
st.readers.splice(1, 1);
out.read = (setupRead(st, saved) || {}).by;
out.self = setupRead({ readers: [{ by: "ChatGPT", at: "2026-11-24T11:00:00+00:00" }] }, saved);   // the saver reading its own save
console.log(JSON.stringify(out));
"""
    out = _run_node(script)
    assert out["saved"] == 8          # the first AI save after the start; not yours, not Mindhall's switch
    assert out["none"] is None and out["notStarted"] is None
    assert out["early"] is None       # a read before the save doesn't count
    assert out["read"] == "Claude"
    assert out["self"] is None        # it has to be another AI


def test_setup_is_hosted_only_and_reachable_from_settings():
    assert "if (ob.root || !account || !account.mcp_url) return;" in PAGE       # needs the hosted address
    assert "if (shouldAutoSetup(account, setupMem)) openSetup(true);" in PAGE
    assert 'onclick: () => openSetup(false) }, "Open the guide")' in PAGE       # Settings → Help and about
    assert 'onclick: () => openSetup(false) }, "Walk me through it, step by step")' in PAGE
    assert 'onclick: () => openSetup(false) }, "Connect your first AI")' in PAGE   # an empty Overview
    # the steps match the website's guides, which they link to
    for app in ("chatgpt", "claude", "claude-code", "grok"):
        assert f'guide: "https://getmindhall.com/integrations/{app}/"' in PAGE
    # Escape leaves it, and nothing behind it can take the focus while it's open
    assert 'if (ob.root) { if (e.key === "Escape")' in PAGE
    assert "shell.inert = true" in PAGE and "shell.inert = false" in PAGE
    # its motion stops when someone asks for less
    assert "@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }" in PAGE
