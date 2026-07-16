"""The bearer token must never leak to a host other than the configured
``base_url``.

``job.urls.self`` / ``job.urls.events`` / ``job.urls.cancel`` are server-returned
absolute URLs that ``Job.refresh()`` / ``events()`` / ``cancel()`` hand straight
to the transport (see ``comfy_sdk/jobs.py``). Before this fix, ``_Prepared``
attached ``Authorization: Bearer <key>`` to *any* absolute URL with no origin
check — a malicious or misconfigured server could point a job's follow-up link
at an attacker-controlled host and have the client hand it the credential.
"""

from __future__ import annotations

from comfy_low.transport import ComfyLow


def test_absolute_url_same_origin_still_gets_bearer_token(server) -> None:
    server.state.require_auth = True
    with ComfyLow(server.base_url, api_key="ck_test") as low:
        # A same-origin absolute URL (exactly the shape job.urls.self takes).
        low.get_job(f"{server.base_url}/api/v2/jobs/whatever")
    assert server.state.last_auth_header == "Bearer ck_test"


def test_absolute_url_cross_origin_does_not_get_bearer_token(server, second_server) -> None:
    # The client is configured against `server` with a real key. A job's
    # `urls.self` pointing at `second_server` — a different origin — must NOT
    # receive that key, even though it's a plain absolute URL our own transport
    # is asked to fetch (not an httpx redirect, where httpx already strips auth
    # on its own).
    with ComfyLow(server.base_url, api_key="ck_super_secret") as low:
        low.get_job(f"{second_server.base_url}/api/v2/jobs/whatever")
    assert second_server.state.last_auth_header == ""
    # And the same client still authenticates correctly against its own origin.
    with ComfyLow(server.base_url, api_key="ck_super_secret") as low:
        low.get_job(f"{server.base_url}/api/v2/jobs/whatever")
    assert server.state.last_auth_header == "Bearer ck_super_secret"


def test_relative_path_resolved_against_base_url_still_gets_token(server) -> None:
    # A plain job id (not a URL) resolves under base_url — unaffected by the fix.
    server.state.require_auth = True
    with ComfyLow(server.base_url, api_key="ck_test") as low:
        low.get_job("whatever")
    assert server.state.last_auth_header == "Bearer ck_test"
