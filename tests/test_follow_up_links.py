"""Follow-up links (``job.urls.*``) resolve against the origin, not base_url.

The serverless gateway serves each deployment on its own subdomain
(``https://{dep_id}.run.comfy.app``), where the base URL carries no path and
origin resolution is trivially right. The rule still matters for any base URL
that does carry a path (a proxy mounting the contract under a prefix): the
server's host-relative follow-up links already include that mount prefix, so
resolving them against ``base_url`` doubles it and 404s; they must resolve
against the scheme+authority only. Internal shorthand paths (``/jobs/…``,
``/assets…``) keep resolving under ``base_url + /api/v2``.
"""

from __future__ import annotations

from comfy_low.transport import _Prepared

SUBDOMAIN_BASE = "https://dep-123.stg.run.comfy.app"
PATH_MOUNTED_BASE = "https://proxy.example/deployment/dep_123"
CLOUD_BASE = "https://cloud.comfy.org"


def test_gateway_self_link_resolves_against_origin() -> None:
    p = _Prepared(PATH_MOUNTED_BASE, "comfyui-k")
    link = "/deployment/dep_123/api/v2/jobs/j1"
    assert p.url(link) == "https://proxy.example" + link


def test_subdomain_links_resolve_against_base() -> None:
    p = _Prepared(SUBDOMAIN_BASE, "comfyui-k")
    assert p.url("/api/v2/jobs/j1") == SUBDOMAIN_BASE + "/api/v2/jobs/j1"
    assert p.url("/jobs/j1") == SUBDOMAIN_BASE + "/api/v2/jobs/j1"


def test_gateway_internal_path_keeps_deployment_prefix() -> None:
    p = _Prepared(PATH_MOUNTED_BASE, "comfyui-k")
    assert p.url("/jobs/j1") == PATH_MOUNTED_BASE + "/api/v2/jobs/j1"
    assert p.url("/assets") == PATH_MOUNTED_BASE + "/api/v2/assets"


def test_cloud_self_link_unchanged() -> None:
    p = _Prepared(CLOUD_BASE, "comfyui-k")
    assert p.url("/api/v2/jobs/j1") == CLOUD_BASE + "/api/v2/jobs/j1"


def test_absolute_link_passes_through() -> None:
    p = _Prepared(PATH_MOUNTED_BASE, "comfyui-k")
    url = "https://elsewhere.example/api/v2/jobs/j1"
    assert p.url(url) == url


def test_auth_still_attaches_to_origin_resolved_links() -> None:
    p = _Prepared(PATH_MOUNTED_BASE, "comfyui-k")
    url = p.url("/deployment/dep_123/api/v2/jobs/j1")
    assert p.headers(url)["Authorization"] == "Bearer comfyui-k"
