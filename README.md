# comfy-sdk (Python)

Python SDK for running ComfyUI workflows via the **Comfy API v2** — the same code
against a self-hosted ComfyUI (through `comfy-api-proxy`), Comfy Cloud, or a
serverless deployment, changing only the base URL and key.

Design: `docs/sdk/plan.md` (two-layer SDK — generated protocol bindings + a
hand-written idiomatic layer). This repository currently holds the first-iteration
demo slice: submit a workflow, wait for it, download the outputs.

```python
from comfy_sdk import Comfy

client = Comfy("http://127.0.0.1:8189")          # local proxy
# client = Comfy("https://api.comfy.org", api_key="...")   # Comfy Cloud

job = client.run(workflow_api_json)
for out in job["outputs"]:
    client.download(out, out["name"])
```

Status: early. The generated protocol layer, file upload, live progress, and the
full idiomatic surface land per the plan.
