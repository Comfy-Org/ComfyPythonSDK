"""Tests for the comfy_sdk client against a stdlib-only stub HTTP server.

These tests exercise the SDK end to end (submit -> poll -> download) without
depending on a real Comfy API v2 server or the comfy-api-proxy, so the SDK's
own test suite stays independent of that project.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from comfy_sdk import Comfy, Unauthorized


class _StubState:
    """Tracks how many times the job has been polled, to simulate progress."""

    def __init__(self) -> None:
        self.poll_count = 0


def _make_handler(state: _StubState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_: str, *args: Any) -> None:
            # Silence the default request logging so test output stays clean.
            pass

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}

            if self.path == "/api/v2/jobs":
                workflow = body.get("workflow", {})
                if workflow.get("_ui_format"):
                    # Simulates the proxy rejecting a workflow submitted in
                    # UI (export) format instead of API format.
                    self._send_json(
                        422,
                        {
                            "error": {
                                "message": (
                                    "workflow must be submitted in API format, not UI format"
                                )
                            }
                        },
                    )
                    return
                if workflow.get("_requires_auth") and not self.headers.get("Authorization"):
                    # Simulates a surface (Comfy Cloud / serverless) that
                    # requires an API key and none was sent.
                    self._send_json(401, {"error": {"message": "unauthorized"}})
                    return
                if workflow.get("_requires_auth") and self.headers.get("Authorization") == (
                    "Bearer wrong-key"
                ):
                    # Simulates a surface rejecting an invalid key.
                    self._send_json(403, {"error": {"message": "forbidden"}})
                    return
                self._send_json(
                    201,
                    {
                        "id": "job-1",
                        "status": "queued",
                        "urls": {"self": "/api/v2/jobs/job-1"},
                    },
                )
                return

            self._send_json(404, {"error": {"message": "not found"}})

        def do_GET(self) -> None:
            if self.path == "/api/v2/jobs/job-1":
                state.poll_count += 1
                if state.poll_count < 2:
                    self._send_json(
                        200,
                        {
                            "id": "job-1",
                            "status": "running",
                            "urls": {"self": "/api/v2/jobs/job-1"},
                        },
                    )
                else:
                    self._send_json(
                        200,
                        {
                            "id": "job-1",
                            "status": "succeeded",
                            "urls": {"self": "/api/v2/jobs/job-1"},
                            "outputs": [
                                {
                                    "name": "out.png",
                                    "url": "/api/v2/jobs/job-1/output/out.png",
                                }
                            ],
                        },
                    )
                return

            if self.path == "/api/v2/jobs/job-1/output/out.png":
                body = b"\x89PNG-fake-bytes"
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self._send_json(404, {"error": {"message": "not found"}})

    return Handler


@pytest.fixture
def stub_server():
    state = _StubState()
    handler = _make_handler(state)
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _base_url(server: HTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"


def test_submit_poll_download_happy_path(stub_server, tmp_path) -> None:
    server, _ = stub_server
    client = Comfy(_base_url(server))

    job = client.run({"nodes": {}}, timeout=5.0, poll=0.05)

    assert job["status"] == "succeeded"
    assert job["outputs"][0]["name"] == "out.png"

    dest = tmp_path / "out.png"
    result_path = client.download(job["outputs"][0], str(dest))

    assert result_path == str(dest)
    assert dest.read_bytes() == b"\x89PNG-fake-bytes"


def test_submit_rejects_ui_format_workflow(stub_server) -> None:
    server, _ = stub_server
    client = Comfy(_base_url(server))

    with pytest.raises(RuntimeError) as exc_info:
        client.submit({"_ui_format": True})

    assert "422" in str(exc_info.value)
    assert "UI format" in str(exc_info.value)


def test_submit_raises_unauthorized_when_no_api_key_set(stub_server) -> None:
    server, _ = stub_server
    client = Comfy(_base_url(server))  # no api_key

    with pytest.raises(Unauthorized) as exc_info:
        client.submit({"_requires_auth": True})

    assert "401" in str(exc_info.value)
    assert "no API key was set" in str(exc_info.value)


def test_submit_raises_unauthorized_when_api_key_rejected(stub_server) -> None:
    server, _ = stub_server
    client = Comfy(_base_url(server), api_key="wrong-key")

    with pytest.raises(Unauthorized) as exc_info:
        client.submit({"_requires_auth": True})

    assert "403" in str(exc_info.value)
    assert "API key was rejected" in str(exc_info.value)
