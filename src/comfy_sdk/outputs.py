"""Output handles — typed, range-aware download over an asset id.

An output is an asset: the bytes are retrievable via ``getAssetContent`` (which
serves directly or ``302``-redirects to a signed URL) for as long as the job is
retained. ``to_file`` streams to disk in chunks; ``to_bytes`` buffers.
"""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import BinaryIO

from comfy_low.models import Output as LowOutput
from comfy_low.transport import AsyncComfyLow, ComfyLow

_CHUNK = 64 * 1024


class Output:
    """A single job output, downloadable via the sync transport."""

    def __init__(self, model: LowOutput, low: ComfyLow) -> None:
        self._model = model
        self._low = low

    @property
    def node_id(self) -> str:
        return self._model.node_id

    @property
    def name(self) -> str:
        return self._model.name

    @property
    def type(self) -> str:
        return self._model.type.value

    @property
    def id(self) -> str:
        return self._model.id

    @property
    def size_bytes(self) -> int:
        return self._model.size_bytes

    @property
    def content_type(self) -> str:
        return self._model.content_type

    def to_file(self, path: str | PathLike[str], *, range: tuple[int, int] | None = None) -> Path:
        dest = Path(path)
        with self._low.get_asset_content(self._model.id, range=range) as resp:
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(_CHUNK):
                    fh.write(chunk)
        return dest

    def to_stream(self, stream: BinaryIO, *, range: tuple[int, int] | None = None) -> int:
        written = 0
        with self._low.get_asset_content(self._model.id, range=range) as resp:
            for chunk in resp.iter_bytes(_CHUNK):
                stream.write(chunk)
                written += len(chunk)
        return written

    def to_bytes(self, *, range: tuple[int, int] | None = None) -> bytes:
        buf = bytearray()
        with self._low.get_asset_content(self._model.id, range=range) as resp:
            for chunk in resp.iter_bytes(_CHUNK):
                buf.extend(chunk)
        return bytes(buf)

    def __repr__(self) -> str:
        return f"Output(node_id={self.node_id!r}, name={self.name!r}, id={self.id!r})"


class AsyncOutput:
    """A single job output, downloadable via the async transport."""

    def __init__(self, model: LowOutput, low: AsyncComfyLow) -> None:
        self._model = model
        self._low = low

    @property
    def node_id(self) -> str:
        return self._model.node_id

    @property
    def name(self) -> str:
        return self._model.name

    @property
    def type(self) -> str:
        return self._model.type.value

    @property
    def id(self) -> str:
        return self._model.id

    @property
    def size_bytes(self) -> int:
        return self._model.size_bytes

    @property
    def content_type(self) -> str:
        return self._model.content_type

    async def to_file(
        self, path: str | PathLike[str], *, range: tuple[int, int] | None = None
    ) -> Path:
        dest = Path(path)
        async with self._low.get_asset_content(self._model.id, range=range) as resp:
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes(_CHUNK):
                    fh.write(chunk)
        return dest

    async def to_bytes(self, *, range: tuple[int, int] | None = None) -> bytes:
        buf = bytearray()
        async with self._low.get_asset_content(self._model.id, range=range) as resp:
            async for chunk in resp.aiter_bytes(_CHUNK):
                buf.extend(chunk)
        return bytes(buf)

    def __repr__(self) -> str:
        return f"AsyncOutput(node_id={self.node_id!r}, name={self.name!r}, id={self.id!r})"
