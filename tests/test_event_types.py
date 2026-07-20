"""Typed-event construction for the event kinds the stub server never emits.

The stub only drives progress/preview/output/status frames with well-formed
data; the `log` event type and the preview base64-decode guard had no coverage.
"""

from __future__ import annotations

from comfy_low.sse import RawEvent
from comfy_sdk.events import Log, Preview, event_from_raw


def _binder(model):  # only OutputReady needs a real binder; unused here
    raise AssertionError("output_binder should not be called for these events")


def test_log_event_decodes():
    ev = event_from_raw(RawEvent(event="log", data={"level": "warn", "message": "x"}), _binder)
    assert isinstance(ev, Log)
    assert ev.level == "warn" and ev.message == "x"


def test_log_event_defaults_missing_fields():
    ev = event_from_raw(RawEvent(event="log", data={}), _binder)
    assert isinstance(ev, Log)
    assert ev.level == "info" and ev.message == ""


def test_preview_survives_undecodable_base64():
    # An unpadded/invalid base64 payload must not raise — data falls back to b"".
    ev = event_from_raw(
        RawEvent(event="preview", data={"data_base64": "abcde", "content_type": "image/png"}),
        _binder,
    )
    assert isinstance(ev, Preview)
    assert ev.data == b""
    assert ev.content_type == "image/png"


def test_unknown_event_name_is_skipped():
    assert event_from_raw(RawEvent(event="mystery", data={}), _binder) is None
