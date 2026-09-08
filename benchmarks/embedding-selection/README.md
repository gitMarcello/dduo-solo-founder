# Embedding selection benchmark

The version 2 seed contains ten entirely fictional Italian/English greenhouse
memories and eight labelled queries. It contains no customer data or results.
`run.py` compares MiniLM, E5 large and OpenAI `text-embedding-3-large` through
FastEmbed/ONNX and the OpenAI API. This optional development experiment does
not change the product's embedding provider.

```bash
uv run --extra benchmark python benchmarks/embedding-selection/run.py
```

Set `OPENAI_API_KEY` privately before running; API calls incur charges. Use
`--skip-openai` to test only local models. Results go to the ignored
`benchmark-results/` directory. The small synthetic corpus is a smoke test,
not evidence of production quality or a hardware requirement. Assess any
provider change on a separate, authorized evaluation corpus.
