"""Idiomatic ``comfy_sdk`` exceptions.

These wrap the protocol-level ``comfy_low.ApiError`` codes with names an
integrator catches directly (``JobFailed``, ``QueueFull``, ...). ``to_sdk_error``
maps a raised ``ApiError`` to the right subclass; anything unmapped stays a
``ComfyError`` carrying the original code.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from comfy_low.errors import ApiError
from comfy_low.models import JobError


class ComfyError(Exception):
    """Base for every SDK-level error."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.details = details


class Unauthorized(ComfyError):
    """The surface rejected the request for lack of a valid key.

    Comfy Cloud and serverless require a key; a self-hosted proxy needs none.
    """


class Forbidden(ComfyError):
    pass


class NotFound(ComfyError):
    pass


class InvalidWorkflow(ComfyError):
    """Structural/validation failure; ``details`` carries per-node errors."""


class WorkflowFormatUi(InvalidWorkflow):
    """UI-export JSON was submitted instead of the API-format graph."""


class MissingAsset(ComfyError):
    """A ``core/ASSET`` reference was not usable (unknown/unscanned/not owned)."""


class HashMismatch(ComfyError):
    """Uploaded bytes did not match the declared ``expected_hash``."""


class BlobNotFound(ComfyError):
    """from-hash / existence probe found no blob the caller can mint from."""


class IdempotencyConflict(ComfyError):
    """A concurrent retry of the same idempotency key is still in flight."""


class IdempotencyKeyReuse(ComfyError):
    """The same idempotency key was reused with a different body."""


class InsufficientCredits(ComfyError):
    pass


class QueueFull(ComfyError):
    """Backpressure: the queue is full. ``retry_after`` is seconds to wait."""

    def __init__(self, message: str, *, retry_after: int, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class JobFailed(ComfyError):
    """A job reached a non-success terminal state.

    ``error`` carries the node-level detail (``.code``, ``.node_id``,
    ``.message``, ``.traceback``) when the platform provided one.
    """

    def __init__(self, message: str, *, error: JobError | None = None) -> None:
        super().__init__(message, code=(error.code if error else "job_failed"))
        self.error = error


_BY_CODE: dict[str, type[ComfyError]] = {
    "invalid_workflow": InvalidWorkflow,
    "workflow_format_ui": WorkflowFormatUi,
    "missing_asset": MissingAsset,
    "hash_mismatch": HashMismatch,
    "blob_not_found": BlobNotFound,
    "idempotency_key_reuse": IdempotencyKeyReuse,
    "idempotency_conflict": IdempotencyConflict,
    "insufficient_credits": InsufficientCredits,
    "not_found": NotFound,
    "unauthorized": Unauthorized,
    "forbidden": Forbidden,
}


def to_sdk_error(exc: ApiError) -> ComfyError:
    """Translate a protocol ``ApiError`` into the idiomatic SDK exception."""
    if exc.code == "queue_full":
        return QueueFull(
            exc.message,
            retry_after=exc.retry_after or 0,
            code=exc.code,
            http_status=exc.http_status,
            details=exc.details,
        )
    cls = _BY_CODE.get(exc.code, ComfyError)
    if cls is Unauthorized and not _has_hint(exc):
        # Preserve the demo client's helpful hint about the missing key.
        pass
    return cls(
        exc.message,
        code=exc.code,
        http_status=exc.http_status,
        details=exc.details,
    )


def _has_hint(exc: ApiError) -> bool:
    return bool(exc.message)


@contextmanager
def translating() -> Iterator[None]:
    """Re-raise any protocol ``ApiError`` as its idiomatic SDK exception.

    Wrap the SDK-level operations that call ``comfy_low`` with this so integrators
    only ever catch ``comfy_sdk`` exceptions (``MissingAsset``, ``HashMismatch``,
    ``NotFound``, ...), never the raw protocol error.
    """
    try:
        yield
    except ApiError as exc:
        raise to_sdk_error(exc) from exc
