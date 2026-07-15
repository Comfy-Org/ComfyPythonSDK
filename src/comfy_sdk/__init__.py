"""Comfy SDK (first-iteration demo slice).

One tiny, hand-written client that runs a workflow against ANY Comfy API v2
surface — the local proxy or Comfy Cloud — changing only the base URL and key.
This is the thin start of the two-layer SDK; the generated protocol layer and the
full idiomatic surface come later.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Any

_TERMINAL = {"succeeded", "failed", "expired", "canceled"}


class JobFailed(Exception):
    def __init__(self, error: dict[str, Any] | None) -> None:
        self.error = error or {}
        super().__init__(self.error.get("message", "job failed"))


class Unauthorized(Exception):
    """The surface rejected the request for lack of a valid API key."""


class Comfy:
    """Client for a Comfy API v2 surface.

    ``api_key`` is optional and surface-dependent. A self-hosted ComfyUI has no
    authentication, so the local proxy needs no key — leave it unset. Comfy Cloud
    and serverless deployments DO require a key; pass it here. When a key is set
    it is sent as a bearer token; when it isn't, no authentication header is sent
    at all (so nothing is leaked to a local proxy). If a surface rejects the
    request for lack of a key, the client raises ``Unauthorized`` with a hint
    rather than a bare HTTP error.
    """

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any, bytes]:
        # A follow-up link may be absolute or root-relative; resolve against base.
        url = path if path.startswith("http") else self.base + path
        data = None
        headers = {}
        if body is not None:
            data = __import__("json").dumps(body).encode()
            headers["Content-Type"] = "application/json"
        # Only authenticate when a key is set: a local proxy fronts a ComfyUI
        # that has no auth, so we never send credentials it doesn't want.
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "")
                parsed = __import__("json").loads(raw) if "application/json" in ctype else None
                return r.status, parsed, raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                parsed = __import__("json").loads(raw)
            except Exception:
                parsed = None
            return e.code, parsed, raw

    def _check_auth(self, status: int) -> None:
        if status in (401, 403):
            hint = (
                "no API key was set — Comfy Cloud and serverless deployments "
                "require one: Comfy(base_url, api_key=...)"
                if not self.api_key
                else "the API key was rejected (check it is valid for this surface)"
            )
            raise Unauthorized(f"{status}: {hint}")

    def submit(self, workflow: dict[str, Any]) -> dict[str, Any]:
        status, job, _ = self._request("POST", "/api/v2/jobs", {"workflow": workflow})
        self._check_auth(status)
        if status != 201:
            raise RuntimeError(f"submit failed ({status}): {job}")
        return job

    def get_job(self, job: dict[str, Any]) -> dict[str, Any]:
        # Follow the embedded self link rather than building the path ourselves.
        status, fresh, _ = self._request("GET", job["urls"]["self"])
        self._check_auth(status)
        if status != 200:
            raise RuntimeError(f"get_job failed ({status}): {fresh}")
        return fresh

    def run(
        self, workflow: dict[str, Any], timeout: float = 120.0, poll: float = 0.5
    ) -> dict[str, Any]:
        job = self.submit(workflow)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get_job(job)
            if job["status"] in _TERMINAL:
                if job["status"] != "succeeded":
                    raise JobFailed(job.get("error"))
                return job
            time.sleep(poll)
        raise TimeoutError(f"job {job['id']} did not finish in {timeout}s")

    def download(self, output: dict[str, Any], dest_path: str) -> str:
        status, _, raw = self._request("GET", output["url"])
        if status != 200:
            raise RuntimeError(f"download failed ({status})")
        with open(dest_path, "wb") as f:
            f.write(raw)
        return dest_path
