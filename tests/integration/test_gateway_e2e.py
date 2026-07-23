"""Live end-to-end test of the SDK against a serverless gateway deployment.

Exercises the image-edit flow the platform team verified by hand on
2026-07-23: upload an input asset, submit an SD1.5 img2img workflow that
references it, poll to a terminal state, download the output.

Skipped unless pointed at a live deployment:

    export COMFY_BASE_URL="https://stagingplatformapi.comfy.org/deployment/<dep_id>"
    export COMFY_API_KEY="comfyui-..."
    pytest tests/integration/test_gateway_e2e.py -v

Optional: COMFY_CKPT_NAME (defaults to the staging test distribution's
SD1.5 checkpoint). First run streams a full multipart upload; reruns hit
the by-hash dedup fast-path (the input image is deterministic), so both
upload paths get coverage across two runs.
"""

from __future__ import annotations

import os
import struct
import zlib

import pytest

from comfy_sdk import Comfy

BASE_URL = os.environ.get("COMFY_BASE_URL")
API_KEY = os.environ.get("COMFY_API_KEY")
CKPT_NAME = os.environ.get("COMFY_CKPT_NAME", "v1-5-pruned-emaonly.safetensors")
INPUT_NAME = "sdk_e2e_input.png"
JOB_TIMEOUT_S = 600  # cold start on a scale-to-zero deployment takes minutes

pytestmark = pytest.mark.skipif(
    not (BASE_URL and API_KEY),
    reason="set COMFY_BASE_URL and COMFY_API_KEY to run gateway e2e tests",
)


def _gradient_png(width: int = 512, height: int = 512) -> bytes:
    """A deterministic RGB gradient PNG, stdlib-only (no PIL dependency)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00"
        + bytes(
            v
            for x in range(width)
            for v in (x * 255 // width, y * 255 // height, 128)
        )
        for y in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _img2img_workflow(image_ref: object) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT_NAME}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image_ref}},
        "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "oil painting, thick brushstrokes, vivid colors", "clip": ["1", 1]},
        },
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality", "clip": ["1", 1]}},
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["3", 0],
                "seed": 42,
                "steps": 20,
                "cfg": 7,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 0.6,
            },
        },
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "sdk_e2e", "images": ["7", 0]}},
    }


@pytest.fixture(scope="module")
def client() -> Comfy:
    c = Comfy(BASE_URL, api_key=API_KEY)
    yield c
    c.close()


@pytest.fixture(scope="module")
def input_asset(client: Comfy, tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("inputs") / INPUT_NAME
    path.write_bytes(_gradient_png())
    asset = client.assets.from_file(path)
    asset.commit()
    return asset


def test_upload_dedup_roundtrip(client: Comfy, input_asset) -> None:
    again = client.assets.from_bytes(_gradient_png(), filename=INPUT_NAME)
    assert again.commit() == input_asset.commit()


def test_image_edit_by_name(client: Comfy, input_asset, tmp_path) -> None:
    wf = client.workflows.from_json(_img2img_workflow(INPUT_NAME))
    job = client.run(wf).wait(timeout=JOB_TIMEOUT_S)

    assert job.status == "succeeded", f"job {job.id} ended {job.status}: {job.error}"
    outputs = job.get_outputs("8") or job.outputs
    assert outputs, f"job {job.id} succeeded with no outputs"

    out_path = tmp_path / "sdk_e2e_out.png"
    outputs[0].to_file(out_path)
    data = out_path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 10_000


@pytest.mark.xfail(
    reason="gateway does not yet resolve core/ASSET references in the graph; "
    "it resolves string filename references only",
    strict=False,
)
def test_image_edit_by_asset_handle(client: Comfy, input_asset, tmp_path) -> None:
    wf = client.workflows.from_json(_img2img_workflow(INPUT_NAME))
    wf.set_input("2", "image", input_asset)
    job = client.run(wf).wait(timeout=JOB_TIMEOUT_S)

    assert job.status == "succeeded", f"job {job.id} ended {job.status}: {job.error}"
    assert job.outputs, f"job {job.id} succeeded with no outputs"
