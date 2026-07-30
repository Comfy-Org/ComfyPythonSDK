"""Job handles — the resumable, poll-authoritative core of the SDK.

A :class:`Job` is rehydratable purely from its ID. ``wait`` polls
``GET /api/v2/jobs/{id}`` with adaptive backoff as the source of truth for
terminal status and outputs, so a stream that is throttled, dropped, or
permanently unavailable never stalls completion. ``events`` is the live SSE
stream on top: typed, auto-reconnecting (no replay — the stream carries no
cursor), with the poll path as its backstop.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from comfy_low.models import Job as LowJob
from comfy_low.models import Output as LowOutput
from comfy_low.transport import AsyncComfyLow, ComfyLow

from . import _core
from .events import Event, StatusChange, event_from_raw
from .exceptions import JobFailed, translating
from .outputs import AsyncOutput, Output

_RECONNECT_PAUSE = 0.1


class Job:
    """Synchronous job handle."""

    def __init__(self, low: ComfyLow, model: LowJob) -> None:
        self._low = low
        self._model = model

    # -- state ------------------------------------------------------------
    @property
    def id(self) -> str:
        return self._model.id

    @property
    def status(self) -> str:
        return self._model.status.value

    @property
    def outputs(self) -> list[Output]:
        return [Output(o, self._low) for o in self._model.outputs]

    @property
    def error(self) -> Any:
        return self._model.error

    def get_outputs(self, node_id: str) -> list[Output]:
        """The outputs produced by one node, in server order.

        Reads the state already on this handle — it does not re-fetch, so call
        :meth:`result` or :meth:`wait` first. An unknown ``node_id``, or a node
        that produced nothing, gives an empty list rather than raising.
        """
        return [Output(o, self._low) for o in self._model.outputs if o.node_id == node_id]

    def _bind_output(self, model: LowOutput) -> Output:
        return Output(model, self._low)

    # -- polling (authoritative) -----------------------------------------
    def refresh(self) -> Job:
        """Re-fetch authoritative job state once, in place, and return ``self``.

        This is the source of truth that live events are reconciled against;
        one call, no waiting. Use :meth:`wait` to poll until terminal.
        """
        with translating():
            self._model = self._low.get_job(self._model.urls.self or self._model.id)
        return self

    def wait(self, timeout: float | None = None) -> Job:
        """Poll to a terminal state (adaptive backoff). Raises ``TimeoutError``."""
        deadline = None if timeout is None else time.monotonic() + timeout
        backoff = _core.backoff_schedule()
        while True:
            self.refresh()
            if _core.is_terminal(self.status):
                return self
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"job {self.id} not terminal after {timeout}s (status={self.status})"
                )
            time.sleep(next(backoff))

    def result(self) -> Job:
        """Wait for terminal, then raise ``JobFailed`` unless it succeeded."""
        self.wait()
        if self.status != _core.SUCCESS:
            raise JobFailed(f"job {self.id} ended {self.status}", error=self._model.error)
        return self

    def cancel(self) -> Job:
        """Request cancellation, updating this handle with the returned state.

        Returns ``self``. Cancellation is a request, not a guarantee: a job that
        already reached a terminal state stays in it, so check :attr:`status`
        rather than assuming the job stopped.
        """
        with translating():
            self._model = self._low.cancel_job(self._model.urls.cancel or self._model.id)
        return self

    # -- live events (best-effort, reconnecting) --------------------------
    def events(self) -> Iterator[Event]:
        """Typed live event iterator. Auto-reconnects with no replay; falls back
        to polling to detect terminal status if the stream ends early.
        """
        events_url = self._model.urls.events or self._model.id
        while True:
            terminal_seen = False
            try:
                for raw in self._low.get_job_events(events_url):
                    ev = event_from_raw(raw, self._bind_output)
                    if ev is None:
                        continue
                    if isinstance(ev, StatusChange) and _core.is_terminal(ev.status):
                        terminal_seen = True
                        yield ev
                        return
                    yield ev
            except (httpx.HTTPError, httpx.StreamError):
                pass  # connection dropped mid-stream — reconnect below
            if terminal_seen:
                return
            # Stream ended without a terminal frame. Poll the authoritative state:
            # stop if already terminal, else reconnect for fresh live frames.
            self.refresh()
            if _core.is_terminal(self.status):
                yield StatusChange(status=self.status)
                return
            time.sleep(_RECONNECT_PAUSE)

    def __repr__(self) -> str:
        return f"Job(id={self.id!r}, status={self.status!r})"


class AsyncJob:
    """Asynchronous job handle — mirrors :class:`Job`."""

    def __init__(self, low: AsyncComfyLow, model: LowJob) -> None:
        self._low = low
        self._model = model

    @property
    def id(self) -> str:
        return self._model.id

    @property
    def status(self) -> str:
        return self._model.status.value

    @property
    def outputs(self) -> list[AsyncOutput]:
        return [AsyncOutput(o, self._low) for o in self._model.outputs]

    @property
    def error(self) -> Any:
        return self._model.error

    def get_outputs(self, node_id: str) -> list[AsyncOutput]:
        """:meth:`Job.get_outputs`, bound to async outputs. Not a coroutine —
        it reads state already on the handle, so no ``await``.
        """
        return [AsyncOutput(o, self._low) for o in self._model.outputs if o.node_id == node_id]

    def _bind_output(self, model: LowOutput) -> AsyncOutput:
        return AsyncOutput(model, self._low)

    async def refresh(self) -> AsyncJob:
        """Async :meth:`Job.refresh` — one authoritative re-fetch, in place."""
        with translating():
            self._model = await self._low.get_job(self._model.urls.self or self._model.id)
        return self

    async def wait(self, timeout: float | None = None) -> AsyncJob:
        """Async :meth:`Job.wait` — poll to terminal. Raises ``TimeoutError``."""
        import asyncio

        deadline = None if timeout is None else time.monotonic() + timeout
        backoff = _core.backoff_schedule()
        while True:
            await self.refresh()
            if _core.is_terminal(self.status):
                return self
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"job {self.id} not terminal after {timeout}s (status={self.status})"
                )
            await asyncio.sleep(next(backoff))

    async def result(self) -> AsyncJob:
        """Async :meth:`Job.result` — wait, then raise ``JobFailed`` unless it succeeded."""
        await self.wait()
        if self.status != _core.SUCCESS:
            raise JobFailed(f"job {self.id} ended {self.status}", error=self._model.error)
        return self

    async def cancel(self) -> AsyncJob:
        """Async :meth:`Job.cancel` — a request, not a guarantee; check :attr:`status`."""
        with translating():
            self._model = await self._low.cancel_job(self._model.urls.cancel or self._model.id)
        return self

    async def events(self) -> AsyncIterator[Event]:
        """Async :meth:`Job.events` — typed live stream, auto-reconnecting with
        no replay and the poll path as its backstop.
        """
        import asyncio

        events_url = self._model.urls.events or self._model.id
        while True:
            terminal_seen = False
            try:
                async for raw in self._low.get_job_events(events_url):
                    ev = event_from_raw(raw, self._bind_output)
                    if ev is None:
                        continue
                    if isinstance(ev, StatusChange) and _core.is_terminal(ev.status):
                        terminal_seen = True
                        yield ev
                        return
                    yield ev
            except (httpx.HTTPError, httpx.StreamError):
                pass
            if terminal_seen:
                return
            await self.refresh()
            if _core.is_terminal(self.status):
                yield StatusChange(status=self.status)
                return
            await asyncio.sleep(_RECONNECT_PAUSE)

    def __repr__(self) -> str:
        return f"AsyncJob(id={self.id!r}, status={self.status!r})"


class JobFactory:
    """``client.jobs`` — rehydrate a :class:`Job` from its ID."""

    def __init__(self, low: ComfyLow) -> None:
        self._low = low

    def get(self, job_id: str) -> Job:
        return Job(self._low, self._low.get_job(job_id))


class AsyncJobFactory:
    def __init__(self, low: AsyncComfyLow) -> None:
        self._low = low

    async def get(self, job_id: str) -> AsyncJob:
        return AsyncJob(self._low, await self._low.get_job(job_id))
