"""The hosted deployment is the default; every other target passes its own URL."""

from __future__ import annotations

import pytest

from comfy_sdk import COMFY_CLOUD_BASE_URL, AsyncComfy, Comfy


def test_constant_points_at_comfy_cloud() -> None:
    assert COMFY_CLOUD_BASE_URL == "https://cloud.comfy.org"


@pytest.mark.parametrize("cls", [Comfy, AsyncComfy])
def test_defaults_to_comfy_cloud_when_no_url_given(cls: type) -> None:
    client = cls(api_key="comfyui-test")
    assert client._low._p.url("/jobs/j1") == COMFY_CLOUD_BASE_URL + "/api/v2/jobs/j1"


@pytest.mark.parametrize("cls", [Comfy, AsyncComfy])
def test_explicit_base_url_still_wins(cls: type) -> None:
    """Self-hosted callers, and the positional form every existing caller uses."""
    client = cls("http://127.0.0.1:8189", "comfyui-test")
    assert client._low._p.url("/jobs/j1") == "http://127.0.0.1:8189/api/v2/jobs/j1"
