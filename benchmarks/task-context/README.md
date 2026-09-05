# Task-context benchmark

This read-only benchmark quantifies how much task context dDuo would deliver as
`full`, `working`, `compact`, and automatic briefing views. It accepts a saved
response from `GET /projects/{project_id}/tasks`, performs no network requests,
and never writes to PostgreSQL or Qdrant.

Run it from the repository root:

```sh
.venv/bin/python benchmarks/task-context/run.py /private/path/tasks.json \
  --require-reduction 0.80
```

`--require-reduction` compares the automatic briefing with the same 12 ordered
active tasks serialized in full. The report separately compares a full task list
(all full task responses) with the new default (all active compact cards). An
explicit `list_tasks(scope=all, detail=full)` remains intentionally unlimited,
so it is measured but is not confused with either default-flow gate.

No customer corpus, fingerprint or measured report is distributed. Regression
tests generate invented tasks in memory; they check arithmetic and output
bounds, not real-world retrieval quality. Keep reports from real projects private.

The output contains only aggregate byte/token estimates and a SHA-256 corpus
identifier. It deliberately contains no task title, description, attachment,
project ID, or other project content. A semantic Recall@K evaluation is a
separate quality gate: it requires a reviewed query-to-task ground-truth file
and an isolated restored project, because generating a credible ground truth
from the indexed task text itself would produce a misleadingly easy score.

For that separate gate, prepare the disposable corpus with
`--semantic-documents-output`, create a reviewed private JSON file containing
`{"queries": [{"query": "...", "task_id": "..."}]}`, and run
`semantic_recall.py` only in an isolated environment that already has the
embedding credential. Neither private input belongs in the repository; retain
only the aggregate result.
