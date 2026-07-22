"""to_sdk_error must map the server's 404 codes to the typed NotFound."""

from __future__ import annotations

import pytest

from comfy_low.errors import ApiError
from comfy_sdk.exceptions import NotFound, to_sdk_error


@pytest.mark.parametrize("code", ["not_found", "job_not_found", "asset_not_found"])
def test_404_codes_map_to_notfound(code: str) -> None:
    # public-api returns entity-specific 404 codes (job_not_found / asset_not_found)
    # alongside the spec's generic not_found; all must raise the typed NotFound.
    err = to_sdk_error(ApiError("not found", code=code, http_status=404))
    assert isinstance(err, NotFound)
    assert err.code == code
