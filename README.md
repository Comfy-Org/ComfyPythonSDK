# comfy-sdk (Python)

Python SDK for running ComfyUI workflows via the **Comfy API v2** — the same code
against a self-hosted ComfyUI, Comfy Cloud, or a serverless deployment, changing
only the base URL and an optional key.

```python
from comfy_sdk import Comfy

client = Comfy("http://127.0.0.1:8189")                 # self-hosted, no key
# client = Comfy("https://cloud.comfy.org", api_key="ck_...")   # Comfy Cloud

wf = client.workflows.from_file("workflow_api.json")

# Lazy asset handle: hashed locally with blake3, deduped against the server's
# fast-path (mint over existing bytes), or streamed-uploaded on a miss — then
# substituted into the graph as a core/ASSET reference.
asset = client.assets.from_file("photo.png")
wf.set_input("10", "image", asset)

result = client.run(wf)                                  # submit + poll-to-done
result.get_outputs("13")[0].to_file("out.png")
```

Live progress with a poll-authoritative backstop:

```python
job = client.submit(wf)
for event in job.events():          # SSE; live, auto-reconnect (no replay)
    match event:
        case Progress() as p:       print(f"{p.value:.0%} {p.message}")
        case Preview() as pv:       show(pv.to_pil())
        case OutputReady() as o:    o.output.to_file(f"partial/{o.output.name}")
        case StatusChange(status="succeeded"): break
result = job.result()               # raises JobFailed with node details on failure
```

An `AsyncComfy` mirrors the whole surface with `async def` / `async for`.

## Architecture — two layers

* **`comfy_low`** — generated protocol bindings. Pydantic v2 models generated
  from `spec/openapi.yaml` (`src/comfy_low/models/_generated.py`, committed;
  regenerate with `scripts/gen_models.sh`, CI fails on drift) plus a thin
  hand-written `httpx` transport (sync + async), one function per `operationId`,
  with the mandatory escape hatches: raw response access, unbuffered/streaming
  bodies, all headers, and per-request timeout/abort. Boring and replaceable.

* **`comfy_sdk`** — the idiomatic layer integrators import. This is where the
  value lives: blake3 content-addressed dedup-upload, `core/ASSET` substitution,
  idempotent submit, live SSE with reconnect, poll-authoritative `run()`,
  range-aware downloads, and typed exceptions mapping the error envelope.

The spec under `spec/` is a one-way vendored copy of the canonical Comfy API v2
contract — do not hand-edit it (see `spec/README.md`).

## Develop

```bash
pip install -e .[dev]
ruff check . && ruff format --check . && mypy src && pytest
python scripts/gen_models.sh     # regenerate comfy_low models from the spec
python scripts/check_drift.py    # verify the committed models match the spec
```
