"""The hosted deployment is the default; every other target passes its own URL."""

from __future__ import annotations

from comfy_sdk import COMFY_CLOUD_BASE_URL, AsyncComfy, Comfy

CLOUD_JOB_URL = COMFY_CLOUD_BASE_URL + "/api/v2/jobs/j1"
LOCAL = "http://127.0.0.1:8189"


def test_constant_points_at_comfy_cloud() -> None:
    assert COMFY_CLOUD_BASE_URL == "https://cloud.comfy.org"


def test_defaults_to_comfy_cloud_when_no_url_given() -> None:
    with Comfy(api_key="comfyui-test") as client:
        assert client._low._p.url("/jobs/j1") == CLOUD_JOB_URL


def test_explicit_base_url_still_wins() -> None:
    """Self-hosted callers, and the positional form every existing caller uses."""
    with Comfy(LOCAL, "comfyui-test") as client:
        assert client._low._p.url("/jobs/j1") == LOCAL + "/api/v2/jobs/j1"


async def test_async_defaults_to_comfy_cloud_when_no_url_given() -> None:
    async with AsyncComfy(api_key="comfyui-test") as client:
        assert client._low._p.url("/jobs/j1") == CLOUD_JOB_URL


async def test_async_explicit_base_url_still_wins() -> None:
    async with AsyncComfy(LOCAL, "comfyui-test") as client:
        assert client._low._p.url("/jobs/j1") == LOCAL + "/api/v2/jobs/j1"
