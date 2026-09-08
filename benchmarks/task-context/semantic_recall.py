#!/usr/bin/env python3
"""Run task Recall@K against a private prepared corpus using OpenAI embeddings."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from openai import OpenAI


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def evaluate(
    documents: list[dict],
    queries: list[dict],
    vectors: list[list[float]],
    *,
    threshold: float,
) -> dict:
    document_vectors = vectors[: len(documents)]
    query_vectors = vectors[len(documents) :]
    ranks: list[int | None] = []
    target_scores: list[float] = []
    for query, query_vector in zip(queries, query_vectors, strict=True):
        ranked = sorted(
            (
                (cosine(query_vector, vector), document["task_id"])
                for document, vector in zip(documents, document_vectors, strict=True)
            ),
            reverse=True,
        )
        target_id = query["task_id"]
        rank = next(
            (index for index, (_, task_id) in enumerate(ranked, start=1) if task_id == target_id),
            None,
        )
        score = next(score for score, task_id in ranked if task_id == target_id)
        ranks.append(rank)
        target_scores.append(score)
    count = len(queries)
    return {
        "query_count": count,
        "recall_at_1": round(sum(rank == 1 for rank in ranks) / count, 6),
        "recall_at_5": round(sum(rank is not None and rank <= 5 for rank in ranks) / count, 6),
        "mean_reciprocal_rank": round(
            sum(1 / rank for rank in ranks if rank is not None) / count,
            6,
        ),
        "threshold": threshold,
        "targets_above_threshold": round(
            sum(score >= threshold for score in target_scores) / count,
            6,
        ),
        "target_score_min": round(min(target_scores), 6),
        "target_score_p50": round(sorted(target_scores)[count // 2], 6),
        "worst_rank": max(rank or count for rank in ranks),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--model", default="text-embedding-3-large")
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--require-recall-at-5", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    corpus_bytes = args.corpus.read_bytes()
    query_bytes = args.ground_truth.read_bytes()
    corpus = json.loads(corpus_bytes)
    ground_truth = json.loads(query_bytes)
    documents = [
        item
        for item in corpus["documents"]
        if item.get("status") not in {"done", "cancelled"}
    ]
    queries = ground_truth["queries"]
    task_ids = {document["task_id"] for document in documents}
    if not queries or any(query.get("task_id") not in task_ids for query in queries):
        raise ValueError("every ground-truth query must target an indexed active task")
    values = [document["text"] for document in documents] + [query["query"] for query in queries]
    response = OpenAI().embeddings.create(model=args.model, input=values)
    vectors = [item.embedding for item in response.data]
    metrics = evaluate(documents, queries, vectors, threshold=args.threshold)
    result = {
        "schema_version": "task-semantic-recall-v1",
        "model": args.model,
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "ground_truth_sha256": hashlib.sha256(query_bytes).hexdigest(),
        "indexed_task_count": len(documents),
        **metrics,
        "provider_input_tokens": getattr(response.usage, "prompt_tokens", None),
    }
    if (
        args.require_recall_at_5 is not None
        and result["recall_at_5"] < args.require_recall_at_5
    ):
        raise SystemExit(
            f"Recall@5 below gate: {result['recall_at_5']:.3f} "
            f"< {args.require_recall_at_5:.3f}"
        )
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
