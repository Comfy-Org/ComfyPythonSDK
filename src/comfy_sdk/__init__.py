"""Comfy SDK — the idiomatic Python client for the Comfy API v2.

The thick, hand-written layer integrators import. It runs an API-format workflow
against any Comfy API v2 surface (self-hosted proxy, Comfy Cloud, serverless) —
the only per-surface difference is the base URL and an optional key — and owns
everything a generator cannot produce: local blake3 dedup-upload, ``core/ASSET``
substitution, idempotent submit, live SSE with a poll-authoritative backstop,
range-aware downloads, and typed errors. It is layered over ``comfy_low`` (the
generated protocol bindings + thin transport).

Quickstart::

    from comfy_sdk import Comfy

    client = Comfy("http://127.0.0.1:8189")            # self-hosted, no key
    # client = Comfy("https://cloud.comfy.org", api_key="comfyui-...")

    wf = client.workflows.from_file("workflow_api.json")
    asset = client.assets.from_file("photo.png")       # lazy; uploaded on use
    wf.set_input("10", "image", asset)

    result = client.run(wf)                            # submit + poll-to-done
    result.get_outputs("13")[0].to_file("out.png")
"""

from __future__ import annotations

from .assets import Asset, AssetFactory, AsyncAsset, AsyncAssetFactory
from .client import AsyncComfy, Comfy
from .events import (
    Event,
    Log,
    OutputReady,
    Preview,
    Progress,
    StatusChange,
)
from .exceptions import (
    BlobNotFound,
    ComfyError,
    Forbidden,
    HashMismatch,
    IdempotencyKeyReuse,
    InsufficientCredits,
    InvalidWorkflow,
    JobFailed,
    MissingAsset,
    NotFound,
    QueueFull,
    Unauthorized,
    WorkflowFormatUi,
)
from .jobs import AsyncJob, Job
from .outputs import AsyncOutput, DownloadUrl, Output
from .workflows import Workflow, WorkflowFactory

__version__ = "0.1.0"

__all__ = [
    # clients
    "Comfy",
    "AsyncComfy",
    # assets / workflows / jobs / outputs
    "Asset",
    "AsyncAsset",
    "AssetFactory",
    "AsyncAssetFactory",
    "Workflow",
    "WorkflowFactory",
    "Job",
    "AsyncJob",
    "Output",
    "AsyncOutput",
    "DownloadUrl",
    # events
    "Event",
    "Progress",
    "Preview",
    "OutputReady",
    "StatusChange",
    "Log",
    # exceptions
    "ComfyError",
    "JobFailed",
    "QueueFull",
    "MissingAsset",
    "HashMismatch",
    "InvalidWorkflow",
    "WorkflowFormatUi",
    "BlobNotFound",
    "IdempotencyKeyReuse",
    "InsufficientCredits",
    "NotFound",
    "Unauthorized",
    "Forbidden",
]
