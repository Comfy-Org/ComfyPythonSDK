"""Local blake3 content hashing.

The hash is computed client-side purely as a *dedup hint* — the server always
recomputes it from the received bytes and that value is authoritative. We stream
the file in chunks so hashing a multi-GB input never buffers it whole.
"""

from __future__ import annotations

from typing import BinaryIO

from blake3 import blake3

_CHUNK = 1024 * 1024


def hash_bytes(data: bytes) -> str:
    """``blake3:<hex>`` of an in-memory buffer."""
    return "blake3:" + blake3(data).hexdigest()


def hash_stream(stream: BinaryIO) -> str:
    """``blake3:<hex>`` of a seekable stream, read in chunks.

    Rewinds to position 0 first and leaves the stream at 0 afterwards so the same
    handle can then be uploaded from the start.
    """
    stream.seek(0)
    hasher = blake3()
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        hasher.update(chunk)
    stream.seek(0)
    return "blake3:" + hasher.hexdigest()


def hash_file(path: str) -> str:
    """``blake3:<hex>`` of a file on disk, streamed."""
    with open(path, "rb") as fh:
        return hash_stream(fh)
