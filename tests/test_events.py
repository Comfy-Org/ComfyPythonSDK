"""Typed SSE events and live reconnect-with-no-replay."""

from __future__ import annotations

from comfy_sdk import Comfy, OutputReady, Progress, StatusChange


def _wf(client: Comfy):
    return client.workflows.from_json({"3": {"class_type": "KSampler", "inputs": {}}})


def test_typed_events_stream_to_terminal(server) -> None:
    with Comfy(server.base_url) as client:
        job = client.submit(_wf(client))
        seen = list(job.events())

    kinds = [type(e).__name__ for e in seen]
    assert "Progress" in kinds
    assert "OutputReady" in kinds
    # The stream ends on the terminal status event.
    assert isinstance(seen[-1], StatusChange)
    assert seen[-1].status == "succeeded"

    progress = [e for e in seen if isinstance(e, Progress)][0]
    assert 0.0 <= progress.value <= 1.0
    output_ready = [e for e in seen if isinstance(e, OutputReady)][0]
    assert output_ready.output.node_id == "13"


def test_sse_reconnect_with_no_replay(server) -> None:
    # First stream drops after one progress frame with no terminal; the client
    # must reconnect (fresh live frames, nothing replayed) and finish.
    server.state.sse_mode = "reconnect"
    # Keep the poll-authoritative backstop reporting "running" so the client
    # reconnects to the stream rather than short-circuiting to terminal on poll.
    server.state.polls_to_succeed = 1000
    with Comfy(server.base_url) as client:
        job = client.submit(_wf(client))
        seen = list(job.events())

    # Two physical connections were made to the events endpoint.
    assert server.state.events_connect_count == 2
    # Completed on a terminal status.
    assert isinstance(seen[-1], StatusChange)
    assert seen[-1].status == "succeeded"
    # No replay: the first connection's single progress frame is not duplicated
    # by a cursor-based resume. The 2nd connection sends its own fresh progress.
    progresses = [e for e in seen if isinstance(e, Progress)]
    assert len(progresses) == 2  # one per connection, not a replayed backlog


def test_preview_event_decodes_base64(server) -> None:
    from base64 import b64encode

    from comfy_low.sse import RawEvent
    from comfy_sdk.events import event_from_raw

    raw = RawEvent(
        event="preview",
        data={
            "node_id": "12",
            "content_type": "image/jpeg",
            "data_base64": b64encode(b"jpeg-bytes").decode(),
        },
    )
    ev = event_from_raw(raw, output_binder=lambda m: m)
    assert ev.data == b"jpeg-bytes"
    assert ev.node_id == "12"
