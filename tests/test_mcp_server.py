"""The self-hosted MCP server's tools and dashboard routes.

Needs the MCP SDK; without it these tests are skipped. Run them with:
    uvx --with 'mcp>=2,<3' pytest
"""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
from starlette.testclient import TestClient  # noqa: E402  (installed with the MCP SDK)

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret-that-is-long-enough-123"


def ctx(app="claude-ai"):
    return SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        client_info=SimpleNamespace(name=app))))


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDHALL_DB", str(tmp_path / "mind.db"))
    monkeypatch.delenv("MINDHALL_MCP_AGENT_NAME", raising=False)
    monkeypatch.syspath_prepend(str(ROOT))
    sys.modules.pop("mcp_server", None)
    module = importlib.import_module("mcp_server")
    yield module
    sys.modules.pop("mcp_server", None)


@pytest.fixture
def web(server):
    server.add_web_routes(SECRET)
    client = TestClient(server.mcp.streamable_http_app(streamable_http_path=f"/{SECRET}/mcp"))
    return lambda method, path, body=None: client.request(method, f"/{SECRET}/api/{path}", json=body)


def test_tools_take_steps_and_project_memories(server):
    work = server.mindhall_update_work("Mindhall", ctx(), steps=["Record the demo", "Write the post"])
    assert work["steps"] == ["Record the demo", "Write the post"] and "focus" not in work
    note = server.mindhall_add_note("Demo script is in Notion", ctx(), work="Mindhall")
    assert note["work"] == "Mindhall"
    page = server.mindhall_read(ctx("chatgpt"), project="Mindhall")
    assert [n["note"] for n in page["notes"]] == ["Demo script is in Notion"]
    assert server.mindhall_remove("project_notes", note["id"], ctx())["removed"] == note["id"]
    readers = server.mind.everything()["readers"]
    assert [r["by"] for r in readers] == ["ChatGPT"]                  # the reader is named by its app


def test_the_read_tool_offers_setup_on_an_empty_mind(server):
    assert server.mindhall_read(ctx())["setup"].startswith("Mindhall: the user's shared mind is empty")


def test_the_dashboard_routes_cover_projects_restore_and_forget(web):
    assert web("POST", "work", {"name": "Mindhall", "steps": ["A", "B"]}).status_code == 200
    work_id = web("GET", "mind").json()["work"][0]["id"]
    assert web("PATCH", f"work/{work_id}", {"steps": ["B"]}).json()["steps"] == ["B"]
    assert web("POST", "project-note", {"note": "Uses SQLite"}).status_code == 400      # needs a project
    note = web("POST", "project-note", {"work": "Mindhall", "note": "Uses SQLite"}).json()
    assert web("PATCH", f"project-note/{note['id']}", {"note": "Uses SQLite per user"}).status_code == 200
    page = web("GET", f"project/{work_id}").json()
    assert [n["note"] for n in page["notes"]] == ["Uses SQLite per user"]
    assert web("DELETE", f"project-note/{note['id']}").status_code == 200
    gone = web("GET", "mind").json()["deleted"]
    assert [g["label"] for g in gone] == ['"Uses SQLite per user" (Mindhall)']
    assert web("POST", f"restore/{gone[0]['change']}").status_code == 200
    assert web("GET", "mind").json()["deleted"] == []
    note_id = web("GET", "mind").json()["project_notes"][0]["id"]
    web("DELETE", f"project-note/{note_id}")
    removal = web("GET", "mind").json()["deleted"][0]["change"]
    assert web("POST", f"forget/{removal}").json()["forgotten"] == removal
    assert "Uses SQLite" not in web("GET", "mind").text
    assert "Mindhall [Active, next: B]" in web("GET", "context").json()["text"]


def test_the_dashboard_reads_and_sets_the_time_zone(web):
    assert web("GET", "settings").json()["time_zone"] is None
    assert web("PATCH", "settings", {"time_zone": "Asia/Kolkata"}).json()["time_zone"] == "Asia/Kolkata"
    assert web("GET", "mind").json()["settings"]["time_zone"] == "Asia/Kolkata"
    assert web("PATCH", "settings", {"time_zone": "Mars/Olympus"}).status_code == 400
    assert web("PATCH", "settings", {}).status_code == 400
    assert web("GET", "settings").json()["time_zone"] == "Asia/Kolkata"


def test_paused_saving_stops_ai_tools_but_not_the_dashboard(server, web):
    assert web("PATCH", "settings", {"paused": True}).json()["paused"] is True
    with pytest.raises(Exception, match="paused"):
        server.mindhall_add_note("Prefers short answers", ctx())
    assert server.mindhall_read(ctx())["about"] == []                      # reading still works
    assert web("POST", "note", {"note": "Prefers short answers"}).status_code == 200   # you can still edit
    assert web("PATCH", "settings", {"paused": "yes"}).status_code == 400
    web("PATCH", "settings", {"paused": False})
    assert server.mindhall_add_note("Writes in British English", ctx())["note"] == "Writes in British English"


def test_clearing_from_the_dashboard_needs_the_word(web):
    web("POST", "note", {"note": "Prefers short answers"})
    assert web("POST", "clear", {"confirm": "yes"}).status_code == 400
    assert web("GET", "mind").json()["about"] != []
    assert web("POST", "clear", {"confirm": "clear"}).json()["cleared"]["about"] == 1
    assert web("GET", "mind").json()["about"] == []
