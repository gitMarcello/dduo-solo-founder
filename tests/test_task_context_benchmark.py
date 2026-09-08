from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "task-context" / "run.py"


def load_benchmark():
    spec = importlib.util.spec_from_file_location("task_context_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_semantic_benchmark():
    path = ROOT / "benchmarks" / "task-context" / "semantic_recall.py"
    spec = importlib.util.spec_from_file_location("task_semantic_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_task_context_benchmark_emits_only_aggregate_bounded_metrics():
    module = load_benchmark()
    secret = "PROJECT_CONTENT_MUST_NOT_LEAK"
    source = json.dumps(
        {
            "items": [
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "project_id": "p1",
                    "kind": "task",
                    "title": "Large task",
                    "description": secret * 5_000,
                    "completion_evidence": secret * 2_000,
                    "objective": "漢" * 2_000,
                    "next_action": "🚀" * 2_000,
                    "labels": ["large"],
                    "dependencies": [],
                    "status": "in_progress",
                    "priority": "high",
                    "version": 1,
                    "attachments": [],
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")
    result = module.measure(source)
    assert result["task_count"] == 1
    assert result["compact_all"]["utf8_bytes"] < result["working_all"]["utf8_bytes"]
    assert result["working_all"]["utf8_bytes"] < result["legacy_default_list"]["utf8_bytes"]
    assert result["default_list"]["utf8_bytes"] < result["legacy_default_list"]["utf8_bytes"]
    assert result["automatic_briefing"]["selected_tasks"] == 1
    assert (
        result["automatic_briefing"]["utf8_bytes"]
        < result["legacy_automatic_briefing"]["utf8_bytes"]
    )
    assert secret not in json.dumps(result)


def test_synthetic_corpus_uses_homologous_baselines_and_honest_math():
    module = load_benchmark()
    # Deliberately verbose invented tasks exercise compression, not real-world quality.
    source = json.dumps(
        {
            "items": [
                {
                    "id": f"synthetic-task-{index}",
                    "project_id": "synthetic-project",
                    "kind": "task",
                    "title": f"Synthetic task {index}",
                    "description": "Invented acceptance criteria for a sample workflow. " * 300,
                    "completion_evidence": "Synthetic verification notes. " * 100,
                    "next_action": "Review the sample result.",
                    "status": "todo" if index < 16 else "done",
                    "priority": "medium",
                    "labels": [],
                    "dependencies": [],
                    "attachments": [],
                    "version": 1,
                }
                for index in range(40)
            ]
        }
    ).encode("utf-8")
    result = module.measure(source)
    assert result["schema_version"] == "task-context-benchmark-v2"
    assert result["task_count"] == 40
    assert result["active_task_count"] == 16
    assert result["legacy_automatic_briefing"]["selected_tasks"] == 12
    assert result["automatic_briefing"]["selected_tasks"] == 12
    assert result["legacy_default_list"]["selected_tasks"] == 40
    assert result["default_list"]["selected_tasks"] == 16

    automatic_expected = 1 - (
        result["automatic_briefing"]["utf8_bytes"]
        / result["legacy_automatic_briefing"]["utf8_bytes"]
    )
    default_expected = 1 - (
        result["default_list"]["utf8_bytes"] / result["legacy_default_list"]["utf8_bytes"]
    )
    assert result["automatic_briefing"]["reduction_fraction"] == round(automatic_expected, 6)
    assert result["default_list"]["reduction_fraction"] == round(default_expected, 6)
    assert result["automatic_briefing"]["reduction_fraction"] >= 0.80
    assert result["default_list"]["reduction_fraction"] >= 0.95
    assert set(result) == {
        "schema_version",
        "corpus_sha256",
        "task_count",
        "active_task_count",
        "legacy_default_list",
        "default_list",
        "compact_all",
        "working_all",
        "legacy_automatic_briefing",
        "automatic_briefing",
    }


def test_semantic_recall_metrics_rank_targets_without_exposing_queries():
    module = load_semantic_benchmark()
    documents = [{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}]
    secret_query = "PRIVATE_QUERY_TEXT"
    queries = [
        {"task_id": "a", "query": secret_query},
        {"task_id": "c", "query": secret_query},
    ]
    vectors = [
        [1.0, 0.0],
        [0.0, 1.0],
        [-1.0, 0.0],
        [0.9, 0.1],
        [-0.9, 0.1],
    ]
    result = module.evaluate(documents, queries, vectors, threshold=0.35)
    assert result["recall_at_1"] == 1.0
    assert result["recall_at_5"] == 1.0
    assert result["targets_above_threshold"] == 1.0
    assert secret_query not in json.dumps(result)
