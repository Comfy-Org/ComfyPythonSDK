"""The clients integrators import: :class:`Comfy` (sync) and :class:`AsyncComfy`.

Both expose the same surface — ``assets`` / ``workflows`` / ``jobs`` constructor
namespaces plus ``submit`` / ``run`` — over a shared sans-IO core. Only the
awaiting methods are duplicated; the rules (idempotency, 429 backoff, asset
materialization, UI-format detection) live in ``_core`` and are called from both.

Per-surface key behavior is inherited from ``comfy_low``: pass ``api_key`` for
Comfy Cloud / serverless; leave it unset for a self-hosted proxy that has no auth
(no credentials are then sent).
"""

from __future__ import annotations

import time
from typing import Any

from comfy_low.errors import ApiError
from comfy_low.transport import AsyncComfyLow, ComfyLow

from . import _core
from .assets import AssetFactory, AsyncAssetFactory
from .exceptions import QueueFull, WorkflowFormatUi, to_sdk_error
from .jobs import AsyncJob, AsyncJobFactory, Job, JobFactory
from .workflows import Workflow, WorkflowFactory

# How long to keep retrying a full queue before giving up (seconds).
_QUEUE_RETRY_BUDGET = 60.0
_DEFAULT_RETRY_AFTER = 2


def _guard_ui_format(workflow: Workflow) -> None:
    if _core.looks_like_ui_format(workflow.json):
        raise WorkflowFormatUi(
            "workflow is in UI-export format (nodes/links/last_node_id); submit "
            "the API-format graph instead",
            code="workflow_format_ui",
            http_status=422,
        )


class Comfy:
    """Synchronous Comfy API v2 client."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout: float | None = 30.0,
    ) -> None:
        self._low = ComfyLow(base_url, api_key, timeout=timeout)
        self.assets = AssetFactory(self._low)
        self.workflows = WorkflowFactory()
        self.jobs = JobFactory(self._low)

    def close(self) -> None:
        self._low.close()

    def __enter__(self) -> Comfy:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _materialize(self, workflow: Workflow) -> dict[str, Any]:
        """Commit every embedded asset handle and substitute ``core/ASSET`` refs."""
        handles = _core.find_asset_handles(workflow.json)
        refs: dict[int, dict[str, Any]] = {}
        for h in handles:
            h.commit()
            refs[id(h)] = h.as_reference()
        return _core.substitute_asset_handles(workflow.json, refs)

    def submit(self, workflow: Workflow, *, idempotency_key: str | None = None) -> Job:
        """Submit a workflow. Retries ``queue_full`` with ``Retry-After``.

        Sends an auto-generated ``Idempotency-Key`` so the server rejects an
        accidental exact resend of *this* request (``422 idempotency_key_reuse``)
        instead of creating a duplicate job. Each call mints a fresh key, so
        calling ``submit()`` again is a distinct submission — to make a retry
        idempotent, pass an explicit ``idempotency_key`` and reuse it. Note a
        reused key is *rejected*, not replayed: on reuse, catch the error and
        poll/list for the job the first attempt already created.
        """
        _guard_ui_format(workflow)
        graph = self._materialize(workflow)
        key = idempotency_key or _core.new_idempotency_key()
        deadline = time.monotonic() + _QUEUE_RETRY_BUDGET
        while True:
            try:
                model = self._low.post_jobs(graph, idempotency_key=key)
                return Job(self._low, model)
            except ApiError as exc:
                err = to_sdk_error(exc)
                if isinstance(err, QueueFull) and time.monotonic() < deadline:
                    time.sleep(err.retry_after or _DEFAULT_RETRY_AFTER)
                    continue
                raise err from exc

    def run(self, workflow: Workflow, *, timeout: float | None = None) -> Job:
        """Submit, then poll to terminal (authoritative). Raises on failure."""
        job = self.submit(workflow)
        return job.result() if timeout is None else _run_with_timeout(job, timeout)


def _run_with_timeout(job: Job, timeout: float) -> Job:
    from ._core import SUCCESS
    from .exceptions import JobFailed

    job.wait(timeout=timeout)
    if job.status != SUCCESS:
        raise JobFailed(f"job {job.id} ended {job.status}", error=job.error)
    return job


class AsyncComfy:
    """Asynchronous Comfy API v2 client — mirrors :class:`Comfy`."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout: float | None = 30.0,
    ) -> None:
        self._low = AsyncComfyLow(base_url, api_key, timeout=timeout)
        self.assets = AsyncAssetFactory(self._low)
        self.workflows = WorkflowFactory()
        self.jobs = AsyncJobFactory(self._low)

    async def aclose(self) -> None:
        await self._low.aclose()

    async def __aenter__(self) -> AsyncComfy:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _materialize(self, workflow: Workflow) -> dict[str, Any]:
        handles = _core.find_asset_handles(workflow.json)
        refs: dict[int, dict[str, Any]] = {}
        for h in handles:
            await h.commit()
            refs[id(h)] = await h.as_reference()
        return _core.substitute_asset_handles(workflow.json, refs)

    async def submit(self, workflow: Workflow, *, idempotency_key: str | None = None) -> AsyncJob:
        import asyncio

        _guard_ui_format(workflow)
        graph = await self._materialize(workflow)
        key = idempotency_key or _core.new_idempotency_key()
        deadline = time.monotonic() + _QUEUE_RETRY_BUDGET
        while True:
            try:
                model = await self._low.post_jobs(graph, idempotency_key=key)
                return AsyncJob(self._low, model)
            except ApiError as exc:
                err = to_sdk_error(exc)
                if isinstance(err, QueueFull) and time.monotonic() < deadline:
                    await asyncio.sleep(err.retry_after or _DEFAULT_RETRY_AFTER)
                    continue
                raise err from exc

    async def run(self, workflow: Workflow, *, timeout: float | None = None) -> AsyncJob:
        from ._core import SUCCESS
        from .exceptions import JobFailed

        job = await self.submit(workflow)
        if timeout is None:
            return await job.result()
        await job.wait(timeout=timeout)
        if job.status != SUCCESS:
            raise JobFailed(f"job {job.id} ended {job.status}", error=job.error)
        return job
