"""``comfy_sdk.__version__`` is derived from the installed distribution
metadata rather than hardcoded.

publish.yml stamps the release tag into pyproject.toml at build time and
nowhere else, so a literal in ``__init__.py`` would silently report the
placeholder version on every published release while the wheel metadata (and
the User-Agent, which already reads dist metadata) said something different.

``__version__`` is resolved once, at import time, so injecting a version means
patching the lookup *and* reloading the package — patching alone would assert
against the value bound when ``comfy_sdk`` was first imported.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError

import pytest

import comfy_sdk


@contextmanager
def reloaded_with(lookup: Callable[[str], str]) -> Iterator[str]:
    """Re-import ``comfy_sdk`` with ``importlib.metadata.version`` replaced,
    yielding the ``__version__`` it resolves to, then restore the real module.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("importlib.metadata.version", lookup)
        importlib.reload(comfy_sdk)
        try:
            yield comfy_sdk.__version__
        finally:
            # Undo the patch and reload again, so the rest of the suite sees
            # the genuine installed version rather than the injected one.
            mp.undo()
            importlib.reload(comfy_sdk)


def test_version_comes_from_the_distribution_metadata() -> None:
    with reloaded_with(lambda _name: "9.9.9") as version:
        assert version == "9.9.9"


def test_version_falls_back_when_not_installed() -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    with reloaded_with(missing) as version:
        assert version == "0+unknown"
