"""The async client mirrors the sync surface against the same stub server."""

from __future__ import annotations

import pytest

from comfy_sdk import AsyncComfy, MissingAsset, StatusChange


def _wf(client: AsyncComfy):
    return client.workflows.from_json({"3": {"class_type": "KSampler", "inputs": {}}})


async def test_async_run_and_download(server, tmp_path) -> None:
    server.state.polls_to_succeed = 2
    async with AsyncComfy(server.base_url) as client:
        job = await client.run(_wf(client))
        assert job.status == "succeeded"
        out = job.get_outputs("13")[0]
        data = await out.to_bytes()
    assert data == server.state.content_bytes


async def test_async_events_stream_to_terminal(server) -> None:
    async with AsyncComfy(server.base_url) as client:
        job = await client.submit(_wf(client))
        seen = [e async for e in job.events()]
    assert isinstance(seen[-1], StatusChange)
    assert seen[-1].status == "succeeded"


async def test_async_dedup_fast_path(server, tmp_path) -> None:
    p = tmp_path / "photo.png"
    p.write_bytes(b"async-dedup-bytes")
    async with AsyncComfy(server.base_url) as client:
        asset = client.assets.from_file(p)
        server.state.known_hashes.add(asset.hash)
        asset_id = await asset.commit()
    assert asset_id == "asset_dedup_01"
    assert server.state.upload_count == 0


async def test_async_error_mapping(server) -> None:
    server.state.job_error = (422, "missing_asset")
    async with AsyncComfy(server.base_url) as client:
        with pytest.raises(MissingAsset):
            await client.submit(_wf(client))
