"""``comfy_sdk.__version__`` is derived from the installed distribution
metadata rather than hardcoded.

publish.yml stamps the release tag into pyproject.toml at build time and
nowhere else, so a literal in ``__init__.py`` would silently report the
placeholder version on every published release while the wheel metadata (and
the User-Agent, which already reads dist metadata) said something different.
"""

from __future__ import annotations

from importlib.metadata import version as pkg_version

import comfy_sdk


def test_version_matches_installed_distribution_metadata() -> None:
    assert comfy_sdk.__version__ == pkg_version("comfy-sdk")
