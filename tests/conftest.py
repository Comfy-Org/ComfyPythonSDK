"""A stdlib-only stub of the Comfy API v2 server, run in a background thread.

Keeps the SDK's own test suite independent of a real v2 server or proxy. Each
test configures ``server.state`` to drive a specific scenario (dedup hit, hash
mismatch, queue-full-then-ok, SSE reconnect, ...), then points a ``Comfy`` client
at ``server.base_url``.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest


@dataclass
class ServerState:
    # Blobs the platform "already has" (for the dedup fast-path).
    known_hashes: set[str] = field(default_factory=set)
    # The authoritative server-side hash returned for uploads.
    server_hash: str = "blake3:" + "ab" * 32
    # If set, POST /assets rejects with 409 hash_mismatch.
    reject_hash_mismatch: bool = False
    # Bytes served by GET /assets/{id}/content.
    content_bytes: bytes = b"\x89PNG-stub-output-bytes-0123456789"
    # Require an Authorization header (Cloud/serverless).
    require_auth: bool = False
    # POST /jobs returns 429 queue_full this many times before succeeding.
    queue_full_times: int = 0
    # POST /jobs returns this error envelope (status, code) instead of 201.
    job_error: tuple[int, str] | None = None
    # Number of GET /jobs/{id} polls before the job reports succeeded.
    polls_to_succeed: int = 1
    # Terminal status the job reaches.
    terminal_status: str = "succeeded"
    # SSE behavior: "reconnect" drops the first stream before terminal.
    sse_mode: str = "normal"

    # --- counters the tests assert on ---
    upload_count: int = 0
    from_hash_count: int = 0
    head_count: int = 0
    job_poll_count: int = 0
    events_connect_count: int = 0
    submit_count: int = 0
    last_workflow: dict[str, Any] | None = None
    # Idempotency-Key -> job id (for replay).
    idempotency: dict[str, str] = field(default_factory=dict)


def _asset_json(asset_id: str, hash_: str, created_new: bool, size: int) -> dict:
    return {
        "id": asset_id,
        "hash": hash_,
        "size_bytes": size,
        "content_type": "image/png",
        "file_path": "photo.png",
        "created_new": created_new,
        "created_at": "2026-07-10T18:00:00Z",
        "url": "http://example.invalid/blob",
        "url_expires_at": "2026-07-10T19:00:00Z",
    }


def _job_json(job_id: str, status: str, outputs: list[dict] | None = None) -> dict:
    return {
        "id": job_id,
        "status": status,
        "created_at": "2026-07-10T18:20:00Z",
        "expires_at": "2026-07-11T18:20:00Z",
        "queue_position": 0,
        "progress": None,
        "outputs": outputs or [],
        "error": None,
        "metrics": {"queue_ms": 9000, "execution_ms": None},
        "urls": {
            "self": f"/api/v2/jobs/{job_id}",
            "events": f"/api/v2/jobs/{job_id}/events",
            "cancel": f"/api/v2/jobs/{job_id}/cancel",
        },
    }


_OUTPUT = {
    "node_id": "13",
    "name": "out.png",
    "type": "image",
    "content_type": "image/png",
    "size_bytes": 33,
    "id": "asset_out_01",
    "hash": None,
    "url": "http://example.invalid/out",
    "url_expires_at": "2026-07-10T19:20:00Z",
}


def _make_handler(state: ServerState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        # -- helpers --
        def _json(self, status: int, payload: dict, headers: dict | None = None) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _err(self, status: int, code: str, message: str = "err") -> None:
            self._json(status, {"error": {"code": code, "message": message}})

        def _auth_ok(self) -> bool:
            if not state.require_auth:
                return True
            return bool(self.headers.get("Authorization"))

        def _read_body(self) -> bytes:
            n = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(n) if n else b""

        # -- HEAD --
        def do_HEAD(self) -> None:
            m = re.match(r"/api/v2/assets/by-hash/(.+)$", self.path)
            if m:
                state.head_count += 1
                hash_ = m.group(1)
                self.send_response(200 if hash_ in state.known_hashes else 404)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        # -- GET --
        def do_GET(self) -> None:
            if not self._auth_ok():
                self._err(401, "unauthorized", "no key")
                return

            m = re.match(r"/api/v2/assets/([^/]+)/content$", self.path)
            if m:
                self._serve_content()
                return
            m = re.match(r"/api/v2/assets/([^/]+)$", self.path)
            if m:
                self._json(200, _asset_json(m.group(1), state.server_hash, False, 33))
                return
            m = re.match(r"/api/v2/jobs/([^/]+)/events$", self.path)
            if m:
                self._serve_events(m.group(1))
                return
            m = re.match(r"/api/v2/jobs/([^/]+)$", self.path)
            if m:
                self._serve_job(m.group(1))
                return
            self._err(404, "not_found")

        def _serve_content(self) -> None:
            data = state.content_bytes
            rng = self.headers.get("Range")
            if rng:
                mm = re.match(r"bytes=(\d+)-(\d+)", rng)
                if mm:
                    start, end = int(mm.group(1)), int(mm.group(2))
                    chunk = data[start : end + 1]
                    self.send_response(206)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_job(self, job_id: str) -> None:
            state.job_poll_count += 1
            if state.job_poll_count >= state.polls_to_succeed:
                status = state.terminal_status
                outputs = [_OUTPUT] if status == "succeeded" else []
            else:
                status = "running"
                outputs = []
            self._json(200, _job_json(job_id, status, outputs))

        def _serve_events(self, job_id: str) -> None:
            state.events_connect_count += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def frame(event: str, data: dict) -> None:
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

            if state.sse_mode == "reconnect" and state.events_connect_count == 1:
                # First connection: a progress frame, then drop without terminal.
                frame("progress", {"value": 0.4, "nodes_done": 4, "nodes_total": 10})
                return
            # Normal (or the reconnect's 2nd connection): full run to terminal.
            frame("status", {"status": "running"})
            frame("progress", {"value": 0.5, "nodes_done": 5, "nodes_total": 10})
            frame("output", _OUTPUT)
            frame("status", {"status": state.terminal_status})

        # -- POST --
        def do_POST(self) -> None:
            if not self._auth_ok():
                self._read_body()
                self._err(401, "unauthorized", "no key")
                return

            if self.path == "/api/v2/assets":
                self._post_assets()
                return
            if self.path == "/api/v2/assets/from-hash":
                self._post_from_hash()
                return
            if self.path == "/api/v2/jobs":
                self._post_jobs()
                return
            m = re.match(r"/api/v2/jobs/([^/]+)/cancel$", self.path)
            if m:
                self._json(200, _job_json(m.group(1), "canceling"))
                return
            self._read_body()
            self._err(404, "not_found")

        def _post_assets(self) -> None:
            state.upload_count += 1
            body = self._read_body()
            if state.reject_hash_mismatch:
                self._err(409, "hash_mismatch", "bytes do not match expected_hash")
                return
            self._json(201, _asset_json("asset_uploaded_01", state.server_hash, True, len(body)))

        def _post_from_hash(self) -> None:
            state.from_hash_count += 1
            body = json.loads(self._read_body() or b"{}")
            if body.get("hash") in state.known_hashes:
                self._json(201, _asset_json("asset_dedup_01", body["hash"], False, 33))
            else:
                self._err(404, "blob_not_found", "no such blob")

        def _post_jobs(self) -> None:
            state.submit_count += 1
            body = json.loads(self._read_body() or b"{}")
            state.last_workflow = body.get("workflow")
            key = self.headers.get("Idempotency-Key")

            if key and key in state.idempotency:
                job_id = state.idempotency[key]
                self._json(
                    201,
                    _job_json(job_id, "queued"),
                    headers={"Idempotency-Replayed": "true"},
                )
                return

            if state.queue_full_times > 0:
                state.queue_full_times -= 1
                self._json(
                    429,
                    {"error": {"code": "queue_full", "message": "full"}},
                    headers={"Retry-After": "0"},
                )
                return

            if state.job_error is not None:
                status, code = state.job_error
                self._err(status, code, f"job error {code}")
                return

            job_id = f"job_{state.submit_count:02d}"
            if key:
                state.idempotency[key] = job_id
            self._json(201, _job_json(job_id, "queued"))

    return Handler


class _Server:
    def __init__(self, httpd: HTTPServer, state: ServerState) -> None:
        self._httpd = httpd
        self.state = state
        host, port = httpd.server_address[:2]
        self.base_url = f"http://{host}:{port}"


@pytest.fixture
def server():
    state = ServerState()
    httpd = HTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Server(httpd, state)
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
