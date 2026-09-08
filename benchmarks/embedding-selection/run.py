from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path

import numpy as np
import psutil

MODELS = [
    ("local", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
    ("local", "intfloat/multilingual-e5-large"),
    ("openai", "text-embedding-3-large"),
]


def embed(provider: str, model: str, texts: list[str], query=False):
    if provider == "openai":
        from openai import OpenAI

        response = OpenAI(api_key=os.environ.get("OPENAI_API_KEY")).embeddings.create(
            model=model, input=texts
        )
        return np.asarray([item.embedding for item in response.data], dtype=np.float32)
    from fastembed import TextEmbedding

    prefix = "query: " if query and "e5" in model else "passage: "
    values = [prefix + text for text in texts]
    return np.asarray(list(TextEmbedding(model_name=model).embed(values)), dtype=np.float32)


def evaluate(dataset: dict, provider: str, model: str) -> dict:
    started = time.perf_counter()
    process = psutil.Process()
    docs = embed(provider, model, [d["text"] for d in dataset["documents"]])
    index_seconds = time.perf_counter() - started
    docs /= np.linalg.norm(docs, axis=1, keepdims=True)
    latencies = []
    recalls = []
    reciprocal = []
    ndcgs = []
    ids = [d["id"] for d in dataset["documents"]]
    for _ in range(3):
        for query in dataset["queries"]:
            then = time.perf_counter()
            vector = embed(provider, model, [query["text"]], query=True)[0]
            vector /= np.linalg.norm(vector)
            ranked = [ids[i] for i in np.argsort(docs @ vector)[::-1][:10]]
            latencies.append(time.perf_counter() - then)
            relevant = set(query["relevant"])
            top5 = ranked[:5]
            recalls.append(len(relevant.intersection(top5)) / len(relevant))
            ranks = [ranked.index(item) + 1 for item in relevant if item in ranked]
            reciprocal.append(1 / min(ranks) if ranks else 0)
            gains = [1 / math.log2(i + 2) for i, item in enumerate(ranked) if item in relevant]
            ideal = sum(1 / math.log2(i + 2) for i in range(len(relevant)))
            ndcgs.append(sum(gains) / ideal)
    return {
        "provider": provider,
        "model": model,
        "recall_at_5": statistics.mean(recalls),
        "mrr_at_10": statistics.mean(reciprocal),
        "ndcg_at_10": statistics.mean(ndcgs),
        "index_seconds": index_seconds,
        "latency_p50": float(np.percentile(latencies, 50)),
        "latency_p95": float(np.percentile(latencies, 95)),
        "rss_mb": process.memory_info().rss / 1024 / 1024,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="benchmark-results/embedding-selection.json")
    parser.add_argument("--skip-openai", action="store_true")
    args = parser.parse_args()
    dataset = json.loads((Path(__file__).parent / "dataset.json").read_text())
    results = []
    for provider, model in MODELS:
        if provider == "openai" and args.skip_openai:
            continue
        results.append(evaluate(dataset, provider, model))
        print(json.dumps(results[-1], indent=2))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"dataset_version": dataset["version"], "results": results}, indent=2))


if __name__ == "__main__":
    main()
