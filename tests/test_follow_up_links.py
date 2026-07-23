"""Follow-up links (``job.urls.*``) resolve against the origin, not base_url.

A server mounts the v2 contract wherever it likes — the serverless gateway
serves it under ``/deployment/{id}/api/v2`` — and its host-relative follow-up
links already include that mount prefix. Resolving them against ``base_url``
(which carries the same prefix) doubles it and 404s; they must resolve against
the scheme+authority only. Internal shorthand paths (``/jobs/…``, ``/assets…``)
keep resolving under ``base_url + /api/v2``.

Regression for the SDK↔gateway conformance bug found 2026-07-23: polling a
job submitted through a deployment-scoped base URL 404'd on refresh.
"""

from __future__ import annotations

from comfy_low.transport import _Prepared

GATEWAY_BASE = "https://stagingplatformapi.comfy.org/deployment/dep_123"
CLOUD_BASE = "https://api.comfy.org"


def test_gateway_self_link_resolves_against_origin() -> None:
    p = _Prepared(GATEWAY_BASE, "comfyui-k")
    link = "/deployment/dep_123/api/v2/jobs/j1"
    assert p.url(link) == "https://stagingplatformapi.comfy.org" + link


def test_gateway_internal_path_keeps_deployment_prefix() -> None:
    p = _Prepared(GATEWAY_BASE, "comfyui-k")
    assert p.url("/jobs/j1") == GATEWAY_BASE + "/api/v2/jobs/j1"
    assert p.url("/assets") == GATEWAY_BASE + "/api/v2/assets"


def test_cloud_self_link_unchanged() -> None:
    p = _Prepared(CLOUD_BASE, "comfyui-k")
    assert p.url("/api/v2/jobs/j1") == CLOUD_BASE + "/api/v2/jobs/j1"


def test_absolute_link_passes_through() -> None:
    p = _Prepared(GATEWAY_BASE, "comfyui-k")
    url = "https://elsewhere.example/api/v2/jobs/j1"
    assert p.url(url) == url


def test_auth_still_attaches_to_origin_resolved_links() -> None:
    p = _Prepared(GATEWAY_BASE, "comfyui-k")
    url = p.url("/deployment/dep_123/api/v2/jobs/j1")
    assert p.headers(url)["Authorization"] == "Bearer comfyui-k"
