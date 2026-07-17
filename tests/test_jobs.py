"""Job submission and lifecycle: poll-authoritative run, idempotent replay,
backpressure retry, and error -> typed-exception mapping.
"""

from __future__ import annotations

import pytest

import comfy_sdk.client as _client_module
from comfy_low.errors import IdempotencyKeyReuse as LowIdempotencyKeyReuse
from comfy_sdk import (
    Comfy,
    IdempotencyKeyReuse,
    InvalidWorkflow,
    JobFailed,
    MissingAsset,
    QueueFull,
    Unauthorized,
    WorkflowFormatUi,
)


def _wf(client: Comfy):
    return client.workflows.from_json({"3": {"class_type": "KSampler", "inputs": {}}})


def test_run_completes_via_polling_when_sse_absent(server, tmp_path) -> None:
    # run() never touches the SSE stream — completion rests on polling.
    server.state.polls_to_succeed = 3
    with Comfy(server.base_url) as client:
        job = client.run(_wf(client))
        assert job.status == "succeeded"
        outs = job.get_outputs("13")
        assert len(outs) == 1
        dest = outs[0].to_file(tmp_path / "out.png")
        assert dest.read_bytes() == server.state.content_bytes
    assert server.state.events_connect_count == 0  # SSE never used


def test_idempotent_submit_rejects_reused_key(server) -> None:
    # Keys are single-use (reject-on-duplicate, no replay): reusing the same
    # explicit key raises IdempotencyKeyReuse rather than replaying the job.
    with Comfy(server.base_url) as client:
        wf = _wf(client)
        j1 = client.submit(wf, idempotency_key="stable-key-123")
        assert j1.id.startswith("job_")
        with pytest.raises(IdempotencyKeyReuse):
            client.submit(wf, idempotency_key="stable-key-123")
    # Both POSTs reached the server; the second was rejected, not replayed.
    assert server.state.submit_count == 2


def test_low_level_submit_rejects_reused_key(server) -> None:
    # The low layer surfaces the protocol-level IdempotencyKeyReuse on reuse;
    # post_jobs returns a Job directly (there is no replay flag / header).
    with Comfy(server.base_url) as client:
        job = client._low.post_jobs({"a": 1}, idempotency_key="k")
        assert job.id.startswith("job_")
        with pytest.raises(LowIdempotencyKeyReuse):
            client._low.post_jobs({"a": 1}, idempotency_key="k")


def test_queue_full_retries_with_retry_after(server) -> None:
    server.state.queue_full_times = 2  # 429 twice, then 201
    with Comfy(server.base_url) as client:
        job = client.submit(_wf(client))
    assert job.id.startswith("job_")
    assert server.state.submit_count == 3  # two rejections + one success


def test_queue_full_gives_up_once_retry_budget_elapses(server, monkeypatch) -> None:
    # The server never clears backpressure. Before this test, only the
    # "eventually succeeds" retry path was covered — nothing proved the client
    # stops retrying and surfaces `QueueFull` instead of looping forever once
    # the retry budget (`_QUEUE_RETRY_BUDGET`) has elapsed.
    server.state.queue_full_times = 1_000_000
    monkeypatch.setattr(_client_module, "_QUEUE_RETRY_BUDGET", -1.0)  # already elapsed
    with Comfy(server.base_url) as client:
        with pytest.raises(QueueFull):
            client.submit(_wf(client))
    # Exactly one attempt reached the server: the budget was spent before the
    # first retry check, so there is no second POST.
    assert server.state.submit_count == 1


def test_missing_asset_maps_to_typed_exception(server) -> None:
    server.state.job_error = (422, "missing_asset")
    with Comfy(server.base_url) as client:
        with pytest.raises(MissingAsset):
            client.submit(_wf(client))


def test_invalid_workflow_maps_to_typed_exception(server) -> None:
    server.state.job_error = (422, "invalid_workflow")
    with Comfy(server.base_url) as client:
        with pytest.raises(InvalidWorkflow):
            client.submit(_wf(client))


def test_ui_format_workflow_rejected_client_side(server) -> None:
    with Comfy(server.base_url) as client:
        wf = client.workflows.from_json({"nodes": [], "links": [], "last_node_id": 0})
        with pytest.raises(WorkflowFormatUi):
            client.submit(wf)
    # Rejected locally — nothing was sent to the server.
    assert server.state.submit_count == 0


def test_failed_job_raises_job_failed(server) -> None:
    server.state.polls_to_succeed = 1
    server.state.terminal_status = "failed"
    with Comfy(server.base_url) as client:
        with pytest.raises(JobFailed):
            client.run(_wf(client))


def test_unauthorized_when_key_required_but_missing(server) -> None:
    server.state.require_auth = True
    with Comfy(server.base_url) as client:  # no api_key
        with pytest.raises(Unauthorized):
            client.submit(_wf(client))


def test_authorized_when_key_present(server) -> None:
    server.state.require_auth = True
    with Comfy(server.base_url, api_key="ck_test") as client:
        job = client.submit(_wf(client))
    assert job.id.startswith("job_")
