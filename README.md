# comfy-sdk (Python)

Python SDK for running ComfyUI workflows via the **Comfy API v2**. The same
code runs against a self-hosted ComfyUI, Comfy Cloud, or a serverless
deployment — only the base URL and an optional API key change.

```python
from comfy_sdk import Comfy

client = Comfy("http://127.0.0.1:8189")                          # self-hosted, no key
# client = Comfy("https://api.comfy.org", api_key="ck_...")        # Comfy Cloud

wf = client.workflows.from_file("workflow_api.json")

# Lazy asset handle: hashed locally with blake3, deduped against the server's
# fast-path (mint over existing bytes), or streamed-uploaded on a miss — then
# substituted into the graph as a core/ASSET reference.
asset = client.assets.from_file("photo.png")
wf.set_input("10", "image", asset)

job = client.run(wf)                 # submit, then poll to a terminal state
job.get_outputs("13")[0].to_file("out.png")
```

## Requirements and install

Requires **Python 3.10+**. Dependencies: `httpx`, `blake3`, `pydantic` (v2).

```bash
pip install comfy-sdk
```

Releases are published to PyPI from a GitHub Release (tag `vX.Y.Z`) by
[`.github/workflows/publish.yml`](.github/workflows/publish.yml), using
PyPI's Trusted Publishing (OIDC) — no API token is stored in this repo.

To install from source instead (for local development, or to track an
unreleased commit):

```bash
git clone https://github.com/Comfy-Org/ComfyPythonSDK
cd ComfyPythonSDK
pip install -e .
# with everything needed to lint/type-check/test locally:
pip install -e ".[dev]"
```

`Preview.to_pil()` (decoding an in-progress SSE preview frame to a `PIL.Image`)
needs the optional `pil` extra: `pip install -e ".[pil]"`.

## Authentication — one client, per-surface key

| Surface | Example base URL | `api_key` |
|---|---|---|
| Self-hosted ComfyUI (behind the API proxy) | `http://127.0.0.1:8189` | Omit — no key is sent, even implicitly |
| Comfy Cloud | `https://api.comfy.org` | Required |
| Serverless deployment | `https://<deployment>.comfy.org` | Required |

```python
client = Comfy("http://127.0.0.1:8189")                       # self-hosted
client = Comfy("https://api.comfy.org", api_key="ck_...")     # Comfy Cloud / serverless
```

`AsyncComfy` takes the same two arguments. A key is only ever attached to
requests aimed at the configured `base_url`'s own origin — a server-returned
follow-up link (`job.urls.self`/`cancel`/`events`, or a redirected asset
download) pointing anywhere else never receives it.

## Assets and `core/ASSET`

`client.assets.from_file(...)` / `from_bytes(...)` / `from_stream(...)` /
`from_url(...)` return a **lazy** asset handle immediately — no network call
yet. Embed it directly into the workflow graph:

```python
asset = client.assets.from_file("photo.png")
wf.set_input("10", "image", asset)
```

On first use (submitting the workflow, or an explicit `asset.commit()`), the
SDK:

1. hashes the bytes locally with blake3;
2. probes the server's dedup fast-path — a `HEAD` existence check by hash,
   then a cheap `from-hash` mint if the server already has those bytes;
3. only streams a full multipart upload on a miss.

At submit time, every asset handle found anywhere in the graph is replaced by
a `core/ASSET` reference object (`{"__type": "core/ASSET", "info": {"id":
..., "hash": ..., "file_path": ...}}`), which the server resolves back to the
uploaded asset when it runs the workflow.

## Live progress

```python
job = client.submit(wf)
for event in job.events():          # SSE; live, auto-reconnecting (no replay)
    match event:
        case Progress() as p:       print(f"{p.value:.0%} {p.message}")
        case Preview() as pv:       show(pv.to_pil())
        case OutputReady() as o:    o.output.to_file(f"partial/{o.output.name}")
        case StatusChange(status="succeeded"): break
result = job.result()               # raises JobFailed with node details on failure
```

`job.events()` reconnects automatically if the stream drops, but never
replays a frame you've already seen (the stream carries no cursor). That's
why polling stays authoritative: `job.wait()` / `job.result()` (and
`client.run()`, which is `submit()` + `result()`) always fall back to
`GET /jobs/{id}` to decide when a job is really done — use `events()` for
live UI feedback, and `wait()`/`result()`/`run()` for the definitive answer.
`job.status` is the current status string; `job.outputs` is the full list of
output handles regardless of which node produced them (`job.get_outputs(node_id)`
filters to one node, as in the quickstart above).

## Sync and async

`Comfy` and `AsyncComfy` expose the identical surface — swap the import and
add `await` / `async for`:

```python
from comfy_sdk import AsyncComfy

async def main() -> None:
    async with AsyncComfy("http://127.0.0.1:8189") as client:
        wf = client.workflows.from_file("workflow_api.json")
        job = await client.run(wf)
        await job.outputs[0].to_file("out.png")
```

## Typed errors

`comfy_sdk` translates the API's error envelope into a small set of
exceptions, all importable from the top-level package and all subclasses of
`ComfyError`:

- `Unauthorized`, `Forbidden`, `NotFound` — auth and lookup failures.
- `InvalidWorkflow`, `WorkflowFormatUi` — the graph itself was rejected;
  `WorkflowFormatUi` specifically means a UI-export (`nodes`/`links`/
  `last_node_id`) was submitted instead of the API-format graph — the SDK
  catches this locally before it ever reaches the server.
- `MissingAsset` — a `core/ASSET` reference could not be resolved.
- `HashMismatch`, `BlobNotFound` — asset upload/dedup failures.
- `IdempotencyKeyReuse` — the `Idempotency-Key` was reused. `submit()` (and
  `run()`) attach a fresh key to every call, so an accidental exact resend never
  runs the workflow twice. Keys are single-use — reject-on-duplicate, there is
  no replay — so if you pass your own `idempotency_key=` and reuse it, the second
  call raises this. After an ambiguous failure (e.g. a timeout where you don't
  know if the job was created), poll or list your jobs rather than resubmitting
  with the same key.
- `InsufficientCredits` — the account can't afford the job.
- `QueueFull` — backpressure; carries `.retry_after` seconds. `client.submit`
  already retries this automatically for a bounded budget before giving up
  and raising it.
- `JobFailed` — a job reached a non-`succeeded` terminal state; `.error`
  carries node-level detail when the platform provided one.

```python
from comfy_sdk import JobFailed, QueueFull, Unauthorized

try:
    result = client.run(wf)
except JobFailed as e:
    print(e.error)
except Unauthorized:
    print("check your api_key")
```

## Architecture — two layers

* **`comfy_low`** — generated protocol bindings. Pydantic v2 models generated
  from `spec/openapi.yaml` (`src/comfy_low/models/_generated.py`, committed;
  regenerate with `scripts/gen_models.sh`, CI fails on drift) plus a thin
  hand-written `httpx` transport (sync + async), one function per `operationId`,
  with the mandatory escape hatches: raw response access, unbuffered/streaming
  bodies, all headers, and per-request timeout/abort. Boring and replaceable.

* **`comfy_sdk`** — the idiomatic layer integrators import. This is where the
  value lives: blake3 content-addressed dedup-upload, `core/ASSET`
  substitution, idempotent submit, live SSE with reconnect, poll-authoritative
  `run()`, range-aware downloads, and typed exceptions mapping the error
  envelope.

`spec/openapi.yaml` is a one-way vendored copy of the canonical Comfy API v2
contract — do not hand-edit it (see `spec/README.md`). It's synced
periodically from that canonical contract, stripped of anything tagged
`internal`, and pinned by `spec/VERSION`.

## Related projects

Part of the same SDK family: a TypeScript client with the equivalent surface
for JS/Node integrators, and the local API proxy that fronts a self-hosted
ComfyUI instance with this same v2 contract (`comfy-api-proxy` in the
`servers` list of `spec/openapi.yaml`).

## Development

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src
pytest -v
```

Regenerating and checking the vendored protocol layer (a separate CI job):

```bash
pip install -e ".[codegen]"
python scripts/gen_models.sh     # regenerate comfy_low models from spec/openapi.yaml
python scripts/check_drift.py    # same check CI runs; fails if committed models drifted
```
