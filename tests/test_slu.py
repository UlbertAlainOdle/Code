"""SLU layer: JSON contract validation, heuristic backend, corpus capture.

This is the seam where the fine-tuned small understanding model plugs in -
see saya_app/services/slu.py for the interface and the dataset/training
scripts in scripts/.
"""

import json

import pytest

from saya_app.services import slu


# ------------------------------------------------------------ contract

def test_validate_segments_accepts_a_wellformed_answer():
    payload = {
        "segments": [{
            "type": "mixed", "text": "过来陪我看风景", "route": "direct_action",
            "direct_action": "come Doctor", "goal": None,
        }],
    }
    normalized = slu.validate_segments(payload)
    assert normalized["segments"][0]["route"] == "direct_action"
    assert normalized["segments"][0]["direct_action"] == "come Doctor"


def test_validate_segments_repairs_optional_fields_and_drops_bad_entries():
    payload = {
        "segments": [
            {"type": "instruction", "route": "goal",
             "goal": {"type": "obtain", "target": "IRON_INGOT", "count": "3"}},
            {"type": "junk", "route": "chat"},          # invalid type -> dropped
            {"type": "chat", "route": "unknown"},        # invalid route -> dropped
            "not-a-dict",                                # dropped
        ],
    }
    normalized = slu.validate_segments(payload)
    assert len(normalized["segments"]) == 1
    segment = normalized["segments"][0]
    assert segment["goal"]["target"] == "iron_ingot"
    assert segment["goal"]["count"] == 3
    assert segment["direct_action"] is None


def test_validate_segments_rejects_structural_garbage():
    assert slu.validate_segments(None) is None
    assert slu.validate_segments("hello") is None
    assert slu.validate_segments({"segments": []}) is None
    assert slu.validate_segments({"segments": [{"type": "junk", "route": "junk"}]}) is None


def test_clean_action_rejects_unsafe_shapes():
    assert slu._clean_action("follow Doctor") == "follow Doctor"
    assert slu._clean_action("") is None
    assert slu._clean_action('say {"a": 1}') is None
    assert slu._clean_action("x" * 200) is None


def test_extract_json_survives_thinking_blocks():
    answer = '<think>用户想要石头，也许 {石: 1} 这样的映射？</think>\n' \
             '{"segments":[{"type":"instruction","route":"direct_action","direct_action":"collect cobblestone 8"}]}'
    payload = slu._extract_json(answer)
    assert payload["segments"][0]["direct_action"] == "collect cobblestone 8"


def test_catalog_hint_lists_canonical_names_with_chinese():
    hint = slu.catalog_hint()
    assert "cobblestone" in hint and "石头" in hint
    assert "禁止中文" in hint


# ------------------------------------------------- heuristic backend

def test_heuristic_labels_direct_actions():
    segments = slu.heuristic_segments("挖点石头")
    assert segments["segments"][0]["route"] == "direct_action"
    assert segments["segments"][0]["direct_action"] == "collect cobblestone 8"


def test_heuristic_labels_goals_from_task_intents():
    segments = slu.heuristic_segments("给我一瓶治疗药水")
    segment = segments["segments"][0]
    assert segment["route"] == "goal"
    assert segment["goal"]["type"] == "obtain_and_deliver"
    assert segment["goal"]["target"] == "potion/healing"


def test_heuristic_returns_none_for_plain_chat():
    assert slu.heuristic_segments("今天天气真不错啊") is None
    assert slu.heuristic_segments("我不需要治疗药水了") is None


# ------------------------------------------------- corpus

def test_record_turn_appends_jsonl_and_opts_out(monkeypatch, tmp_path):
    monkeypatch.setenv("SAYA_SLU_CORPUS", "1")  # conftest disables globally
    corpus = tmp_path / "utterances.jsonl"
    monkeypatch.setattr(slu, "CORPUS_FILE", corpus)
    monkeypatch.setattr(slu, "CORPUS_DIR", tmp_path)

    assert slu.record_turn("挖点铁矿", {"route": "task", "commands": ["collect iron_ore 1"]},
                           source="minecraft")
    assert slu.record_turn("今天天气不错", {"route": "chat"}, source="minecraft")
    rows = slu.load_corpus()
    assert [row["route"] for row in rows] == ["task", "chat"]
    assert rows[0]["text"] == "挖点铁矿"

    monkeypatch.setenv("SAYA_SLU_CORPUS", "0")
    assert slu.record_turn("被开关拦住", {"route": "chat"}) is False
    assert len(slu.load_corpus()) == 2


def test_load_corpus_skips_malformed_lines(monkeypatch, tmp_path):
    corpus = tmp_path / "utterances.jsonl"
    corpus.write_text('{"text": "ok", "route": "chat"}\n{broken json\n\n', encoding="utf-8")
    monkeypatch.setattr(slu, "CORPUS_FILE", corpus)
    rows = slu.load_corpus()
    assert len(rows) == 1 and rows[0]["text"] == "ok"


def test_handle_message_wrapper_records_corpus(monkeypatch, tmp_path):
    monkeypatch.setenv("SAYA_SLU_CORPUS", "1")  # conftest disables globally
    """The public handle_message appends one corpus row per turn, fail-silent."""
    from saya_app.services.conversation import ConversationService

    corpus = tmp_path / "utterances.jsonl"
    monkeypatch.setattr(slu, "CORPUS_FILE", corpus)
    monkeypatch.setattr(slu, "CORPUS_DIR", tmp_path)

    service = ConversationService()
    result = service.handle_message("desktop", "Tester", "follow Doctor")
    # Without a live worker the command is recognized but not deliverable;
    # either way the utterance lands in the corpus with its routing truth.
    assert result["route"] in ("task", "clarify")
    rows = slu.load_corpus()
    assert len(rows) == 1
    assert rows[0]["text"] == "follow Doctor"
    assert rows[0]["route"] == result["route"]


# ------------------------------------------------- dataset builder

def test_build_slu_dataset_from_synthetic_corpus(tmp_path, monkeypatch):
    import subprocess
    import sys

    corpus = tmp_path / "utterances.jsonl"
    rows = [
        {"text": "挖点铁矿", "route": "task", "commands": ["collect iron_ore 8"],
         "direct_action": None, "goal": None},
        {"text": "给我一瓶治疗药水", "route": "goal", "commands": [],
         "direct_action": None,
         "goal": {"type": "obtain_and_deliver", "target": "potion/healing",
                  "count": 1, "recipient": "Doctor"}},
        {"text": "今天天气真不错", "route": "chat", "commands": [],
         "direct_action": None, "goal": None},
    ]
    corpus.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                      encoding="utf-8")
    out = tmp_path / "dataset.jsonl"
    from pathlib import Path as _P
    real_script = _P(__file__).resolve().parents[1] / "scripts" / "build_slu_dataset.py"

    result = subprocess.run(
        [sys.executable, "-B", str(real_script), "--source", str(corpus), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    dataset = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(dataset) == 3
    messages = dataset[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[2]["role"] == "assistant"
    payload = json.loads(messages[2]["content"])
    assert payload["segments"][0]["route"] in ("task", "goal", "chat")


def test_synthesize_corpus_rows_are_labeled_and_verified(tmp_path):
    """The template synthesizer self-labels via the deterministic layers and
    only keeps rows whose labels match the template's expectation."""
    import subprocess
    import sys
    from pathlib import Path as _P

    out = tmp_path / "synthetic.jsonl"
    real_script = _P(__file__).resolve().parents[1] / "scripts" / "synthesize_slu_corpus.py"
    result = subprocess.run(
        [sys.executable, "-B", str(real_script), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) >= 300
    routes = {row["route"] for row in rows}
    assert {"goal", "direct_action", "chat"} <= routes
    for row in rows:
        if row["route"] == "goal":
            assert row["goal"]["target"] and " " not in row["goal"]["target"]
        elif row["route"] == "direct_action":
            assert row["direct_action"]
        else:
            assert row["direct_action"] is None and row["goal"] is None
