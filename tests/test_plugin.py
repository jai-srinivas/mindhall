"""The Hermes plugin, loaded with a stand-in for Hermes (no Hermes install needed)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME = "mindhall_plugin_under_test"


class FakeHermes:
    """Records what the plugin registers. Like an older Hermes: no system prompt sections."""

    def __init__(self):
        self.tools, self.hooks, self.commands = {}, {}, {}

    def register_tool(self, name, toolset, schema, handler):
        self.tools[name] = (schema, handler)

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_command(self, name, fn, help_text):
        self.commands[name] = fn


@pytest.fixture
def hermes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))                 # sessions.json stays in the test folder
    monkeypatch.setenv("MINDHALL_DB", str(tmp_path / "mind.db"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("MINDHALL_AGENT_NAME", raising=False)
    spec = importlib.util.spec_from_file_location(NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
    fake = FakeHermes()
    module.register(fake)
    fake.engine = sys.modules[f"{NAME}.engine"]
    yield fake
    for key in [k for k in sys.modules if k == NAME or k.startswith(NAME + ".")]:
        del sys.modules[key]


def call(hermes, tool, **params):
    return json.loads(hermes.tools[tool][1](params))


def test_the_tools_save_steps_and_project_memories(hermes):
    saved = call(hermes, "mindhall_update_work", name="Mindhall", steps=["Record the demo", "Write the post"])
    assert saved["success"] and saved["steps"] == ["Record the demo", "Write the post"]
    note = call(hermes, "mindhall_add_note", note="Demo script is in Notion", work="mindhall")
    assert note["success"] and note["work"] == "Mindhall"
    page = call(hermes, "mindhall_read", project="Mindhall")
    assert [n["note"] for n in page["notes"]] == ["Demo script is in Notion"]
    assert page["work"]["steps"] == ["Record the demo", "Write the post"]
    assert call(hermes, "mindhall_add_note", note="Prefers short answers")["success"]
    wrong = call(hermes, "mindhall_add_note", note="Something", work="Nope")
    assert not wrong["success"] and "Projects: Mindhall" in wrong["error"]


def test_reading_leaves_a_read_receipt(hermes):
    call(hermes, "mindhall_add_note", note="Prefers short answers")
    call(hermes, "mindhall_read")
    mind = hermes.engine.Mind()
    assert [r["by"] for r in mind.everything()["readers"]] == ["Hermes"]


def test_remove_takes_project_memories(hermes):
    call(hermes, "mindhall_update_work", name="Mindhall")
    note = call(hermes, "mindhall_add_note", note="Uses SQLite", work="Mindhall")
    assert call(hermes, "mindhall_remove", room="project_notes", id=note["id"])["success"]
    assert call(hermes, "mindhall_read", project="Mindhall")["notes"] == []


def test_the_turn_block_offers_setup_when_empty_and_counts_as_a_read(hermes):
    inject = hermes.hooks["pre_llm_call"]
    assert inject(session_id="s1") == {"context": hermes.engine.SETUP_PROMPT}
    call(hermes, "mindhall_add_note", note="Prefers short answers")
    block = inject(session_id="s1")["context"]
    assert "Prefers short answers" in block and "Mindhall is right" in block
    assert hermes.engine.Mind().everything()["readers"][0]["by"] == "Hermes"


def test_the_schemas_offer_the_new_parameters(hermes):
    props = {name: schema["parameters"]["properties"] for name, (schema, _h) in hermes.tools.items()}
    assert "steps" in props["mindhall_update_work"] and "focus" not in props["mindhall_update_work"]
    assert props["mindhall_update_work"]["steps"]["maxItems"] == 3
    assert "work" in props["mindhall_add_note"] and "project" in props["mindhall_read"]
    assert props["mindhall_remove"]["room"]["enum"] == ["about", "dates", "project_notes"]


def _listed(manifest: str, key: str) -> list:
    lines, out, inside = manifest.splitlines(), [], False
    for line in lines:
        if line.strip().startswith(f"{key}:"):
            inside = True
            continue
        if inside and line.strip().startswith("- "):
            out.append(line.strip()[2:].strip())
        elif inside and line.strip():
            inside = False
    return out


def test_plugin_yaml_lists_exactly_what_the_plugin_registers(hermes):
    for manifest in ("plugin.yaml", "docs/catalog-entry.yaml"):
        text = (ROOT / manifest).read_text(encoding="utf-8")
        assert sorted(_listed(text, "provides_tools")) == sorted(hermes.tools), manifest
        assert sorted(_listed(text, "provides_hooks")) == sorted(hermes.hooks), manifest



def test_paused_saving_stops_the_plugins_writes_but_not_reads(hermes, tmp_path):
    hermes.engine.Mind(tmp_path / "mind.db").set_paused(True)
    saved = call(hermes, "mindhall_add_note", note="Prefers short answers")
    assert not saved.get("success") and "paused" in json.dumps(saved)
    assert "about" in call(hermes, "mindhall_read")
    block = hermes.hooks["pre_llm_call"](session_id="s1")["context"]
    assert "Saving is paused" in block


def test_a_switched_off_profile_gets_no_mind_at_all(hermes, tmp_path):
    call(hermes, "mindhall_add_note", note="Prefers short answers")
    mind = hermes.engine.Mind(tmp_path / "mind.db")
    mind.set_ai_on("hermes", False, "You")                             # the default profile signs as "Hermes"
    for tool, params in (("mindhall_read", {}), ("mindhall_add_note", {"note": "Likes tea"})):
        result = call(hermes, tool, **params)
        assert not result.get("success") and "turned off for this app" in result["error"]
    assert hermes.hooks["pre_llm_call"](session_id="s1") == {"context": hermes.engine.OFF}
    assert "Prefers short answers" not in hermes.commands["mindhall"](session_id="s1")
    assert [r["by"] for r in mind.everything()["readers"]] == []        # nothing it was refused counts as a read
    mind.set_ai_on("Hermes", True, "You")
    assert "Prefers short answers" in hermes.hooks["pre_llm_call"](session_id="s1")["context"]
