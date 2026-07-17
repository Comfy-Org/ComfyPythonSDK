"""Thin ``comfy_low`` transport over httpx — sync and async.

One function per ``operationId`` in ``spec/openapi.yaml``, plus the mandatory
escape hatches the hand-written ``comfy_sdk`` layer builds on:

* **raw response access** — ``raw_request`` returns the fully-read ``httpx.Response``;
* **unbuffered / streaming bodies** — ``open`` yields a streaming response, and
  ``post_assets`` streams its multipart body instead of buffering the file;
* **all headers** — every method that needs them exposes the response headers;
* **per-request timeout / abort** — every method takes ``timeout`` and the raw
  httpx cancellation applies.

This layer contains no orchestration, retries, hashing, or reconnection — those
live in ``comfy_sdk``.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, BinaryIO
from urllib.parse import urlsplit

import httpx

from . import _multipart
from .errors import ApiError, error_from_envelope
from .models import Asset, Job
from .sse import RawEvent, SSEDecoder

_API = "/api/v2"
_UNSET = object()

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _retry_after(resp: httpx.Response) -> int | None:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _origin(url: str) -> tuple[str, str, int | None]:
    """Normalized ``(scheme, host, port)`` — the parts that define same-origin."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    port = parts.port
    if port is None:
        port = _DEFAULT_PORTS.get(scheme)
    return (scheme, (parts.hostname or "").lower(), port)


class _Prepared:
    """Sans-IO request building shared by both transports."""

    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._base_origin = _origin(self.base_url)

    def url(self, path: str) -> str:
        # A path may be an absolute follow-up link (job.urls.*) or an API path.
        if path.startswith("http"):
            return path
        if path.startswith("/api/"):
            return self.base_url + path
        return self.base_url + _API + path

    def headers(self, url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        h: dict[str, str] = {}
        # Only authenticate when a key is set: a local proxy fronts a ComfyUI
        # with no auth, so we never leak credentials it does not want. And only
        # attach it when the resolved request URL is same-origin as base_url:
        # server-returned absolute follow-up links (job.urls.self/cancel/events)
        # must not carry the key to a different scheme/host/port. Relative paths
        # are always resolved under base_url, so they are unaffected.
        if self.api_key and _origin(url) == self._base_origin:
            h["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            h.update(extra)
        return h

    def parse_or_raise(self, resp: httpx.Response, ok: tuple[int, ...]) -> dict[str, Any]:
        if resp.status_code in ok:
            if resp.content:
                return resp.json()
            return {}
        body: dict[str, Any] | None
        try:
            body = resp.json()
        except Exception:
            body = None
        raise error_from_envelope(resp.status_code, body, retry_after=_retry_after(resp))


def _new_boundary() -> str:
    return "----comfy" + secrets.token_hex(16)


async def _async_multipart_body(chunks: Iterator[bytes]) -> AsyncIterator[bytes]:
    """Bridge the sync multipart chunk generator into an async byte stream.

    ``_multipart.build_multipart`` reads the file with blocking ``read()`` calls
    (it has to — file objects are sync). ``httpx.AsyncClient`` requires an async
    iterable body, so each ``next()`` (which may block on a file read) is run in
    a worker thread via ``asyncio.to_thread`` — that keeps the event loop free
    while still streaming chunk-by-chunk instead of buffering the whole file.
    """
    it = iter(chunks)
    while True:
        chunk = await asyncio.to_thread(next, it, None)
        if chunk is None:
            return
        yield chunk


class ComfyLow:
    """Synchronous protocol bindings."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float | None = 30.0,
    ) -> None:
        self._p = _Prepared(base_url, api_key)
        self._own_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    # -- lifecycle --------------------------------------------------------
    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> ComfyLow:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- escape hatches ---------------------------------------------------
    def raw_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        content: Any = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        """Fully-read raw response — headers, status, and body all accessible."""
        kw: dict[str, Any] = {}
        if timeout is not _UNSET:
            kw["timeout"] = timeout
        url = self._p.url(path)
        return self._client.request(
            method,
            url,
            headers=self._p.headers(url, headers),
            json=json,
            content=content,
            **kw,
        )

    @contextmanager
    def open(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        content: Any = None,
        timeout: Any = _UNSET,
    ) -> Iterator[httpx.Response]:
        """Streaming (unbuffered) response for binary and SSE bodies."""
        kw: dict[str, Any] = {}
        if timeout is not _UNSET:
            kw["timeout"] = timeout
        url = self._p.url(path)
        with self._client.stream(
            method,
            url,
            headers=self._p.headers(url, headers),
            json=json,
            content=content,
            **kw,
        ) as resp:
            yield resp

    # -- assets -----------------------------------------------------------
    def post_assets(
        self,
        file: BinaryIO,
        content_type: str,
        file_path: str,
        *,
        expected_hash: str | None = None,
        tags: list[str] | None = None,
        idempotency_key: str | None = None,
        file_size: int | None = None,
        timeout: Any = _UNSET,
    ) -> Asset:
        """POST /api/v2/assets — streaming multipart upload."""
        if file_size is None:
            file_size = _multipart.file_size_of(file)
        fields: list[tuple[str, str]] = [
            ("content_type", content_type),
            ("file_path", file_path),
        ]
        if expected_hash is not None:
            fields.append(("expected_hash", expected_hash))
        if tags:
            # One part per tag — repeating the field name is the multipart/form
            # convention for a list, and a dict would silently drop all but one.
            fields.extend(("tags", t) for t in tags)
        boundary = _new_boundary()
        body, length = _multipart.build_multipart(
            boundary,
            fields=fields,
            file_name=file_path,
            file_obj=file,
            file_content_type=content_type,
            file_size=file_size,
        )
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if length is not None:
            headers["Content-Length"] = str(length)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        resp = self.raw_request("POST", "/assets", headers=headers, content=body, timeout=timeout)
        data = self._p.parse_or_raise(resp, (200, 201, 202))
        return Asset.model_validate(data)

    def asset_from_hash(
        self,
        hash: str,
        *,
        file_path: str | None = None,
        tags: list[str] | None = None,
        timeout: Any = _UNSET,
    ) -> Asset:
        """POST /api/v2/assets/from-hash — dedup mint over existing bytes."""
        payload: dict[str, Any] = {"hash": hash}
        if file_path is not None:
            payload["file_path"] = file_path
        if tags is not None:
            payload["tags"] = tags
        resp = self.raw_request("POST", "/assets/from-hash", json=payload, timeout=timeout)
        data = self._p.parse_or_raise(resp, (200, 201))
        return Asset.model_validate(data)

    def head_asset_by_hash(self, hash: str, *, timeout: Any = _UNSET) -> bool:
        """HEAD /api/v2/assets/by-hash/{hash} — existence probe."""
        resp = self.raw_request("HEAD", f"/assets/by-hash/{hash}", timeout=timeout)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        return bool(self._p.parse_or_raise(resp, (200,)))  # raises typed error

    def get_asset(self, asset_id: str, *, timeout: Any = _UNSET) -> Asset:
        """GET /api/v2/assets/{id} — metadata with a fresh content URL."""
        resp = self.raw_request("GET", f"/assets/{asset_id}", timeout=timeout)
        return Asset.model_validate(self._p.parse_or_raise(resp, (200,)))

    @contextmanager
    def get_asset_content(
        self,
        asset_id: str,
        *,
        range: tuple[int, int] | None = None,
        timeout: Any = _UNSET,
    ) -> Iterator[httpx.Response]:
        """GET /api/v2/assets/{id}/content — raw, streamed, range-aware body.

        Yields the streaming response (escape hatch); the caller iterates bytes.
        """
        headers: dict[str, str] = {}
        if range is not None:
            headers["Range"] = f"bytes={range[0]}-{range[1]}"
        with self.open(
            "GET", f"/assets/{asset_id}/content", headers=headers, timeout=timeout
        ) as resp:
            if resp.status_code not in (200, 206):
                resp.read()
                self._p.parse_or_raise(resp, (200, 206))
            yield resp

    # -- jobs -------------------------------------------------------------
    def post_jobs(
        self,
        workflow: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        timeout: Any = _UNSET,
    ) -> Job:
        """POST /api/v2/jobs."""
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        resp = self.raw_request(
            "POST",
            "/jobs",
            headers=headers,
            json={"workflow": workflow},
            timeout=timeout,
        )
        data = self._p.parse_or_raise(resp, (201,))
        return Job.model_validate(data)

    def get_job(self, job_id_or_url: str, *, timeout: Any = _UNSET) -> Job:
        """GET /api/v2/jobs/{id} (or an absolute self link)."""
        path = job_id_or_url if _looks_like_path(job_id_or_url) else f"/jobs/{job_id_or_url}"
        resp = self.raw_request("GET", path, timeout=timeout)
        return Job.model_validate(self._p.parse_or_raise(resp, (200,)))

    def get_job_events(self, job_id_or_url: str, *, timeout: Any = None) -> Iterator[RawEvent]:
        """GET /api/v2/jobs/{id}/events — raw live SSE iterator (escape hatch).

        No reconnection here; a single connection's frames. ``comfy_sdk`` adds the
        reconnect loop. ``timeout=None`` by default (an idle stream must not time
        out mid-job).
        """
        path = job_id_or_url if _looks_like_path(job_id_or_url) else f"/jobs/{job_id_or_url}/events"
        headers = {"Accept": "text/event-stream"}
        decoder = SSEDecoder()
        with self.open("GET", path, headers=headers, timeout=timeout) as resp:
            if resp.status_code != 200:
                resp.read()
                self._p.parse_or_raise(resp, (200,))
            for line in resp.iter_lines():
                yield from decoder.push(line)

    def cancel_job(self, job_id_or_url: str, *, timeout: Any = _UNSET) -> Job:
        """POST /api/v2/jobs/{id}/cancel — idempotent."""
        path = job_id_or_url if _looks_like_path(job_id_or_url) else f"/jobs/{job_id_or_url}/cancel"
        resp = self.raw_request("POST", path, timeout=timeout)
        return Job.model_validate(self._p.parse_or_raise(resp, (200,)))


class AsyncComfyLow:
    """Asynchronous protocol bindings — mirrors :class:`ComfyLow`."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = 30.0,
    ) -> None:
        self._p = _Prepared(base_url, api_key)
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncComfyLow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def raw_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        content: Any = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        kw: dict[str, Any] = {}
        if timeout is not _UNSET:
            kw["timeout"] = timeout
        url = self._p.url(path)
        return await self._client.request(
            method,
            url,
            headers=self._p.headers(url, headers),
            json=json,
            content=content,
            **kw,
        )

    @asynccontextmanager
    async def open(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        content: Any = None,
        timeout: Any = _UNSET,
    ) -> AsyncIterator[httpx.Response]:
        kw: dict[str, Any] = {}
        if timeout is not _UNSET:
            kw["timeout"] = timeout
        url = self._p.url(path)
        async with self._client.stream(
            method,
            url,
            headers=self._p.headers(url, headers),
            json=json,
            content=content,
            **kw,
        ) as resp:
            yield resp

    async def post_assets(
        self,
        file: BinaryIO,
        content_type: str,
        file_path: str,
        *,
        expected_hash: str | None = None,
        tags: list[str] | None = None,
        idempotency_key: str | None = None,
        file_size: int | None = None,
        timeout: Any = _UNSET,
    ) -> Asset:
        if file_size is None:
            file_size = _multipart.file_size_of(file)
        fields: list[tuple[str, str]] = [
            ("content_type", content_type),
            ("file_path", file_path),
        ]
        if expected_hash is not None:
            fields.append(("expected_hash", expected_hash))
        if tags:
            # One part per tag — see the sync ``post_assets`` for why a dict is wrong.
            fields.extend(("tags", t) for t in tags)
        boundary = _new_boundary()
        body, length = _multipart.build_multipart(
            boundary,
            fields=fields,
            file_name=file_path,
            file_obj=file,
            file_content_type=content_type,
            file_size=file_size,
        )
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if length is not None:
            headers["Content-Length"] = str(length)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        # AsyncClient requires an async-iterable body: build_multipart only ever
        # produces a sync generator (file reads are inherently sync), so bridge
        # it — see _async_multipart_body — instead of handing httpx a sync
        # generator, which raises RuntimeError the moment it tries to send.
        resp = await self.raw_request(
            "POST",
            "/assets",
            headers=headers,
            content=_async_multipart_body(body),
            timeout=timeout,
        )
        data = self._p.parse_or_raise(resp, (200, 201, 202))
        return Asset.model_validate(data)

    async def asset_from_hash(
        self,
        hash: str,
        *,
        file_path: str | None = None,
        tags: list[str] | None = None,
        timeout: Any = _UNSET,
    ) -> Asset:
        payload: dict[str, Any] = {"hash": hash}
        if file_path is not None:
            payload["file_path"] = file_path
        if tags is not None:
            payload["tags"] = tags
        resp = await self.raw_request("POST", "/assets/from-hash", json=payload, timeout=timeout)
        data = self._p.parse_or_raise(resp, (200, 201))
        return Asset.model_validate(data)

    async def head_asset_by_hash(self, hash: str, *, timeout: Any = _UNSET) -> bool:
        resp = await self.raw_request("HEAD", f"/assets/by-hash/{hash}", timeout=timeout)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        return bool(self._p.parse_or_raise(resp, (200,)))

    async def get_asset(self, asset_id: str, *, timeout: Any = _UNSET) -> Asset:
        resp = await self.raw_request("GET", f"/assets/{asset_id}", timeout=timeout)
        return Asset.model_validate(self._p.parse_or_raise(resp, (200,)))

    @asynccontextmanager
    async def get_asset_content(
        self,
        asset_id: str,
        *,
        range: tuple[int, int] | None = None,
        timeout: Any = _UNSET,
    ) -> AsyncIterator[httpx.Response]:
        headers: dict[str, str] = {}
        if range is not None:
            headers["Range"] = f"bytes={range[0]}-{range[1]}"
        async with self.open(
            "GET", f"/assets/{asset_id}/content", headers=headers, timeout=timeout
        ) as resp:
            if resp.status_code not in (200, 206):
                await resp.aread()
                self._p.parse_or_raise(resp, (200, 206))
            yield resp

    async def post_jobs(
        self,
        workflow: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        timeout: Any = _UNSET,
    ) -> Job:
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        resp = await self.raw_request(
            "POST",
            "/jobs",
            headers=headers,
            json={"workflow": workflow},
            timeout=timeout,
        )
        data = self._p.parse_or_raise(resp, (201,))
        return Job.model_validate(data)

    async def get_job(self, job_id_or_url: str, *, timeout: Any = _UNSET) -> Job:
        path = job_id_or_url if _looks_like_path(job_id_or_url) else f"/jobs/{job_id_or_url}"
        resp = await self.raw_request("GET", path, timeout=timeout)
        return Job.model_validate(self._p.parse_or_raise(resp, (200,)))

    async def get_job_events(
        self, job_id_or_url: str, *, timeout: Any = None
    ) -> AsyncIterator[RawEvent]:
        path = job_id_or_url if _looks_like_path(job_id_or_url) else f"/jobs/{job_id_or_url}/events"
        headers = {"Accept": "text/event-stream"}
        decoder = SSEDecoder()
        async with self.open("GET", path, headers=headers, timeout=timeout) as resp:
            if resp.status_code != 200:
                await resp.aread()
                self._p.parse_or_raise(resp, (200,))
            async for line in resp.aiter_lines():
                for event in decoder.push(line):
                    yield event

    async def cancel_job(self, job_id_or_url: str, *, timeout: Any = _UNSET) -> Job:
        path = job_id_or_url if _looks_like_path(job_id_or_url) else f"/jobs/{job_id_or_url}/cancel"
        resp = await self.raw_request("POST", path, timeout=timeout)
        return Job.model_validate(self._p.parse_or_raise(resp, (200,)))


def _looks_like_path(s: str) -> bool:
    return s.startswith("http") or s.startswith("/")


__all__ = ["ComfyLow", "AsyncComfyLow", "ApiError"]
