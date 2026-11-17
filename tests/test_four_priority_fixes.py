"""Behavioral regressions for the four audited reliability gaps."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

import pytest
from fastapi.testclient import TestClient

from core_services import CheckInManager, GoalManager, KnowledgeBase, MemoryManager
from saya_app.services.backup_manager import BackupManager
from saya_app.services.state_storage import StateSaveError
from saya_app.services.web_research.citation_validator import validate_response_citations
from saya_app.services.web_research.models import WebSource
from test_chat_prompt_web import SERVER


@pytest.mark.parametrize("mode,role", [("normal", ""), ("deep", "assistant")])
def test_temporary_chat_does_not_read_or_write_private_context(tmp_path, mode, role):
    from saya_app.services.conversation import conversation_service
    manager = MemoryManager(str(tmp_path / "chats"))
    kb = KnowledgeBase(str(tmp_path / "kb"))
    session = manager.create_session()
    manager.save_message("user", "PRIVATE_HISTORY", session_id=session)
    before = {p.name: p.read_bytes() for p in (tmp_path / "chats").iterdir() if p.is_file()}
    payload = {}

    import httpx

    def handler(request):
        payload.update(json.loads(request.content.decode("utf-8")))
        body = (
            "data: " + json.dumps({"choices": [{"delta": {"content": "isolated reply"}}]}) + "\n\n"
            + "data: [DONE]\n\n"
        ).encode("utf-8")
        return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                    base_url="https://example.invalid")

    async def collect():
        response = await SERVER.chat_stream(SERVER.ChatWithMotion(
            user_input="CURRENT_INPUT", session_id=session, model="api", api_key="fixture",
            api_url="https://example.invalid/v1", api_model_name="fixture-model",
            temporary_chat=True, agent_mode=True, mode=mode,
            context={"project_id": "private-project", "checkin_context": "PRIVATE_CHECKIN", "role_mode": role},
        ))
        return "".join([str(chunk) async for chunk in response.body_iterator])

    with (patch.object(SERVER, "memory_manager", manager), patch.object(SERVER, "kb_system", kb),
          patch.object(SERVER, "_get_api_httpx_client", lambda: mock_client),
          patch.object(SERVER, "journal_service_prompt_context", return_value="PRIVATE_JOURNAL") as journal,
          patch.object(SERVER, "bridge_service_prompt_context", return_value="PRIVATE_BRIDGE") as bridge,
          patch.object(SERVER.project_manager, "build_project_context", return_value="PRIVATE_PROJECT") as project,
          patch.object(SERVER.project_manager, "add_session") as add_project,
          patch.object(SERVER.assistant_role_manager, "add_session") as add_assistant,
          patch.object(SERVER.assistant_role_manager, "build_prompt_context", return_value="PRIVATE_ROLE") as assistant,
          patch.object(conversation_service, "save_credentials") as credentials,
          patch.object(manager, "load_session", wraps=manager.load_session) as history,
          patch.object(manager, "create_session", wraps=manager.create_session) as create):
        stream = asyncio.run(collect())
    assert "isolated reply" in stream
    assert '"temporary": true' in stream
    prompt = json.dumps(payload)
    assert "CURRENT_INPUT" in prompt
    assert "PRIVATE_" not in prompt
    for function in (journal, bridge, project, assistant, credentials, history, create, add_project, add_assistant):
        function.assert_not_called()
    after = {p.name: p.read_bytes() for p in (tmp_path / "chats").iterdir() if p.is_file()}
    assert before == after


def test_temporary_prompt_boundary_also_covers_web_rebuild():
    with patch.object(SERVER, "journal_service_prompt_context") as journal, patch.object(SERVER, "bridge_service_prompt_context") as bridge:
        prompt = SERVER.build_prompt("current", "", temporary_chat=True, role_mode="assistant",
            history_context=["PRIVATE_HISTORY"], memory_context_override="PRIVATE_MEMORY",
            project_context="PRIVATE_PROJECT", checkin_context="PRIVATE_DAY", web_context="PUBLIC_WEB")
    assert "PRIVATE_" not in prompt
    assert "PUBLIC_WEB" in prompt
    journal.assert_not_called()
    bridge.assert_not_called()


def test_backup_hash_and_archive_use_identical_snapshot(tmp_path):
    kb, chats = tmp_path / "kb", tmp_path / "chats"
    kb.mkdir(); chats.mkdir()
    source = kb / "knowledge.json"
    source.write_text('{"version":1}', encoding="utf-8")
    manager = BackupManager(kb, chats)
    original_hash = manager._sha256
    def modify_live_file_after_hash(path):
        digest = original_hash(path)
        source.write_text('{"version":2}', encoding="utf-8")
        return digest
    with patch.object(manager, "_sha256", modify_live_file_after_hash):
        backup = manager.create_backup(["memories"])
    with zipfile.ZipFile(manager._backup_path(backup["backup_id"])) as archive:
        data = archive.read("knowledge/knowledge.json")
        assert hashlib.sha256(data).hexdigest() == backup["files"][0]["sha256"]
        assert json.loads(data) == {"version": 1}
    preview = manager.preview_restore(backup["backup_id"], ["memories"])
    restored = manager.restore(backup["backup_id"], None, preview["confirmation_token"])
    assert restored["selected_scopes"] == ["memories"]
    assert json.loads(source.read_text()) == {"version": 1}


def test_restore_refreshes_goals_then_allows_another_save(tmp_path):
    from saya_app.routers import backups, goals as goals_api
    goals = GoalManager(str(tmp_path / "kb"))
    checkins = CheckInManager(str(tmp_path / "kb"))
    first = goals.add_goal("before")
    checkins.add_task("daily")
    manager = BackupManager(tmp_path / "kb", tmp_path / "chats")
    backup = manager.create_backup(["checkins"])
    goals.update_goal(first["id"], {"title": "after"})
    preview = manager.preview_restore(backup["backup_id"], ["checkins"])
    with (patch.object(backups, "backup_manager", manager), patch("core_services.goal_manager", goals),
          patch("core_services.checkin_manager", checkins), patch.object(goals_api, "goal_manager", goals),
          patch.object(goals_api, "checkin_manager", checkins)):
        result = backups.restore_backup(backup["backup_id"], {"confirmation_token": preview["confirmation_token"]})
        assert result["runtime_reloaded"] == ["checkins"]
        assert goals_api.get_goals()[0]["title"] == "before"
    goals.add_goal("next")
    assert [g["title"] for g in GoalManager(str(tmp_path / "kb")).goals] == ["before", "next"]


def test_valid_backup_can_recover_a_corrupt_live_file(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    source = kb / "life_quests.json"
    source.write_text('[{"id":"a","title":"saved"}]', encoding="utf-8")
    manager = BackupManager(kb, tmp_path / "chats")
    backup = manager.create_backup(["checkins"])
    source.write_text('{"truncated":', encoding="utf-8")
    preview = manager.preview_restore(backup["backup_id"], ["checkins"])
    result = manager.restore(backup["backup_id"], ["checkins"], preview["confirmation_token"])
    assert json.loads(source.read_text())[0]["title"] == "saved"
    with zipfile.ZipFile(manager._backup_path(result["safety_backup_id"])) as archive:
        assert archive.read("knowledge/life_quests.json") == b'{"truncated":'


def test_failed_writer_does_not_roll_back_another_successful_writer(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from saya_app.services import state_storage
    goals = GoalManager(str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    replace = state_storage.os.replace
    def fail_first(source, target):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
            raise OSError("first writer failed")
        return replace(source, target)
    with patch.object(state_storage.os, "replace", fail_first), ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(goals.add_goal, "failed")
        assert entered.wait(5)
        second = pool.submit(goals.add_goal, "saved")
        release.set()
        with pytest.raises(StateSaveError): first.result(timeout=5)
        second.result(timeout=5)
    assert [g["title"] for g in goals.goals] == ["saved"]
    assert [g["title"] for g in GoalManager(str(tmp_path)).goals] == ["saved"]


@pytest.mark.parametrize("operation", ["add", "edit", "delete"])
def test_goal_save_failure_rolls_back_disk_and_memory(tmp_path, operation):
    goals = GoalManager(str(tmp_path))
    item = goals.add_goal("original")
    before = copy.deepcopy(goals.goals)
    data = Path(goals.file_path).read_bytes()
    with patch("saya_app.services.state_storage.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(StateSaveError):
            if operation == "add": goals.add_goal("new")
            elif operation == "edit": goals.update_goal(item["id"], {"status": "completed"})
            else: goals.delete_goal(item["id"])
    assert goals.goals == before
    assert Path(goals.file_path).read_bytes() == data
    assert not list(tmp_path.glob("*.tmp"))
    goals.add_goal("retry")
    assert len(GoalManager(str(tmp_path)).goals) == 2


@pytest.mark.parametrize("operation", ["add", "update", "delete", "day", "entry"])
def test_checkin_save_failure_rolls_back(tmp_path, operation):
    manager = CheckInManager(str(tmp_path))
    task = manager.add_task("daily")
    before = copy.deepcopy(manager.data)
    data = Path(manager.file_path).read_bytes()
    with patch("saya_app.services.state_storage.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(StateSaveError):
            if operation == "add": manager.add_task("new")
            elif operation == "update": manager.update_task(task["id"], {"title": "changed"})
            elif operation == "delete": manager.delete_task(task["id"])
            elif operation == "entry": manager.record_app_entry()
            else: manager.save_day(completions={task["id"]: True}, note="changed")
    assert manager.data == before
    assert Path(manager.file_path).read_bytes() == data
    assert CheckInManager(str(tmp_path)).data == before


def test_interrupted_json_write_cannot_truncate_saved_goals(tmp_path):
    goals = GoalManager(str(tmp_path))
    goals.add_goal("original")
    original = Path(goals.file_path).read_bytes()
    def partial_dump(value, handle, **kwargs):
        handle.write('[{"partial":')
        raise OSError("disk full")
    with patch("saya_app.services.state_storage.json.dump", partial_dump):
        with pytest.raises(StateSaveError): goals.add_goal("new")
    assert Path(goals.file_path).read_bytes() == original
    assert len(goals.goals) == 1


def test_goal_reopen_and_recomplete_preserve_history_not_old_date(tmp_path):
    goals = GoalManager(str(tmp_path))
    item = goals.add_goal("goal")
    goals.update_goal(item["id"], {"status": "completed", "achieved_at": "2000-01-01"})
    goals.update_goal(item["id"], {"status": "active"})
    assert item["achieved_at"] is None and item["completed_at"] is None
    goals.update_goal(item["id"], {"status": "failed", "failed_at": "2001-01-01"})
    goals.update_goal(item["id"], {"status": "completed"})
    assert item["achieved_at"] not in {"2000-01-01", "2001-01-01"}
    assert item["failed_at"] is None
    completed_at = item["completed_at"]
    goals.update_goal(item["id"], {"title": "edited"})
    assert item["completed_at"] == completed_at
    assert len(item["status_history"]) == 4
    assert item["status_history"][1]["achieved_at"] == "2000-01-01"
    assert GoalManager(str(tmp_path)).goals[0]["status_history"] == item["status_history"]


@pytest.mark.parametrize("endpoint", ["/api/goals/add", "/api/checkins/tasks/add"])
def test_save_failure_is_an_explicit_http_error(tmp_path, endpoint):
    from saya_app.routers import goals, checkins
    with (patch.object(goals, "goal_manager", GoalManager(str(tmp_path / "goals"))),
          patch.object(checkins, "checkin_manager", CheckInManager(str(tmp_path / "checkins"))),
          patch("saya_app.services.state_storage.os.replace", side_effect=OSError("disk full"))):
        response = TestClient(SERVER.app, raise_server_exceptions=False).post(endpoint, json={"title": "test"})
    assert response.status_code == 507
    assert response.json()["saved"] is False
    assert "detail" in response.json()


@pytest.mark.parametrize("claim,evidence,reason", [
    ("Orion supports offline mode", "Orion does not support offline mode.", "negation_mismatch"),
    ("星辰软件支持离线模式", "星辰软件不支持离线模式。", "negation_mismatch"),
    ("Orion revenue increased by 20% in 2025", "Orion revenue decreased by 20% in 2025.", "direction_mismatch"),
    ("星辰软件收入增长20%", "星辰软件收入下降20%。", "direction_mismatch"),
    ("Orion latency was 20 seconds", "Orion latency was 20 milliseconds.", "unit_mismatch"),
    ("Orion revenue increased by 20%", "Vega revenue increased by 20%.", "subject_mismatch"),
])
def test_contradictory_citations_never_pass(claim, evidence, reason):
    source = WebSource(1, "Fixture", "https://example.invalid", "example.invalid", status="read", text=evidence)
    report = validate_response_citations(claim + " [1]", [source])
    assert report["status"] == "needs_review"
    citation = report["claims"][0]["citations"][0]
    assert reason in citation["conflicts"]
    assert citation["support"] == "unsupported"


def test_matching_negative_claim_and_evidence_are_not_flagged():
    source = WebSource(1, "Fixture", "https://example.invalid", "example.invalid", status="read",
                       text="Orion does not support offline mode. Other products support online mode.")
    report = validate_response_citations("Orion does not support offline mode [1]", [source])
    assert report["status"] == "pass"
    assert report["claims"][0]["citations"][0]["conflicts"] == []
    assert report["method"] == "deterministic_lexical"
    assert report["limitation"]


@pytest.mark.parametrize("claim,evidence", [
    ("Orion revenue increased while costs decreased", "Orion revenue increased while costs decreased."),
    ("Orion risk fell by 20%", "Orion risk fell by 20%."),
    ("Orion latency was 20 ms", "Orion latency was 20 milliseconds."),
    ("The Orion supports offline mode", "Orion supports offline mode."),
])
def test_matching_evidence_does_not_trigger_conflict_rules(claim, evidence):
    source = WebSource(1, "Fixture", "https://example.invalid", "example.invalid", status="read", text=evidence)
    report = validate_response_citations(claim + " [1]", [source])
    assert report["status"] == "pass"
    assert report["claims"][0]["citations"][0]["conflicts"] == []
