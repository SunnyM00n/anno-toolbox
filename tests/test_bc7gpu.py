"""BC7 on the graphics card (models/bc7gpu.py) and the choice between it and the CPU encoder (textures)."""

import threading
import warnings

import pytest

from annotoolbox.infra import tools
from annotoolbox.infra.tools import ToolResult
from annotoolbox.models import bc7encode, bc7gpu, textures

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

CPU, GPU = b"cpu-bytes", b"gpu-bytes"


@pytest.fixture
def encoders(monkeypatch, tmp_path):
    """A GPU on adapter 0 and both encoders replaced by stand-ins that record the sizes they get."""
    calls: dict[str, list] = {"cpu": [], "gpu": []}
    monkeypatch.setenv(bc7gpu.ADAPTERS_ENV, "0")
    monkeypatch.delenv(textures.ENCODER_ENV, raising=False)
    monkeypatch.setattr(bc7gpu, "MIN_TEXELS", 32 * 32)
    monkeypatch.setattr(bc7gpu, "_texconv", lambda: None)
    monkeypatch.setattr(textures, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(textures, "_cpu_bc7", lambda levels, srgb: (calls["cpu"].append(levels[0].shape[:2]), CPU)[1])
    monkeypatch.setattr(bc7gpu, "encode", lambda dds, srgb: (calls["gpu"].append(len(dds)), GPU)[1])
    return calls


def pixels(size: int):
    return np.full((size, size, 4), 90, np.uint8)


def test_a_large_picture_goes_to_the_gpu_a_small_one_to_the_cpu(encoders):
    assert textures.rgba_to_bc7(pixels(64)) == GPU
    assert textures.rgba_to_bc7(pixels(16)) == CPU
    assert len(encoders["gpu"]) == 1 and encoders["cpu"] == [(16, 16)]


def test_vector_data_never_goes_to_the_gpu(encoders):
    """Normal maps lose 4 to 10 dB on the GPU encoder (texture-pipeline "GPU encoder")."""
    with textures.vector_data():
        assert textures.rgba_to_bc7(pixels(64)) == CPU
    assert textures.rgba_to_bc7(pixels(64)) == GPU


@pytest.mark.parametrize("value", ["cpu", "texconv"])
def test_the_switch_keeps_every_texture_on_the_cpu(encoders, monkeypatch, value):
    monkeypatch.setenv(textures.ENCODER_ENV, value)
    assert textures.rgba_to_bc7(pixels(64)) == CPU
    assert not textures.gpu_on()


def test_no_adapter_means_the_cpu(encoders, monkeypatch):
    monkeypatch.setenv(bc7gpu.ADAPTERS_ENV, "none")
    assert textures.rgba_to_bc7(pixels(64)) == CPU


def test_a_gpu_error_falls_back_to_the_cpu_and_is_not_cached(encoders, monkeypatch, tmp_path):
    if tools.find_tool("texconv") is None:  # a picture file's CPU way is texconv's (the CI machines have none)
        pytest.skip("texconv is not installed")

    def fails(dds, srgb):
        raise bc7gpu.GpuError("device lost")

    monkeypatch.setattr(bc7gpu, "encode", fails)
    src = tmp_path / "big.png"
    Image.fromarray(pixels(64), "RGBA").save(src)
    with pytest.warns(RuntimeWarning, match="CPU"):
        textures.png_to_bc7(src, tmp_path / "a_0.dds")
    assert (tmp_path / "a_0.dds").read_bytes() == CPU
    assert not list((tmp_path / "cache").rglob("*.dds"))  # its key says GPU: CPU bytes may not sit under it
    monkeypatch.setattr(bc7gpu, "encode", lambda dds, srgb: GPU)
    textures.png_to_bc7(src, tmp_path / "b_0.dds")
    assert (tmp_path / "b_0.dds").read_bytes() == GPU


def test_the_cache_keys_differ_with_and_without_the_gpu(encoders, monkeypatch):
    if not bc7encode.available():
        pytest.skip("the in-process encoder (etcpak) is not installed")
    with_gpu = textures.encoder_id(), textures._cache_key("png", b"x", False, False, 0)
    monkeypatch.setenv(textures.ENCODER_ENV, "cpu")
    without = textures.encoder_id(), textures._cache_key("png", b"x", False, False, 0)
    assert with_gpu[0] != without[0] and with_gpu[1] != without[1]
    assert bc7gpu.identity() in with_gpu[0] and bc7gpu.identity() not in without[0]


def test_the_adapters_are_probed_once_and_handed_to_the_workers(monkeypatch):
    monkeypatch.delenv(bc7gpu.ADAPTERS_ENV)
    probes = []
    monkeypatch.setattr(bc7gpu, "probe", lambda: (probes.append(1), "0,2")[1])
    assert bc7gpu.adapters() == [0, 2]
    assert bc7gpu.adapters() == [0, 2]
    assert probes == [1]
    assert bc7gpu.os.environ[bc7gpu.ADAPTERS_ENV] == "0,2"  # a worker started now inherits it


def fake_adapters(monkeypatch, cards: dict):
    """``run_encode`` on made-up adapters: index -> (name, a list of the bytes its successive runs write)."""
    monkeypatch.setattr(bc7gpu, "_texconv", lambda: bc7gpu.Path("texconv.exe"))

    def run(texconv, index, dds, srgb):
        if index not in cards:
            return None, None
        name, runs = cards[index]
        return name, runs.pop(0) if len(runs) > 1 else runs[0]

    monkeypatch.setattr(bc7gpu, "run_encode", run)


def test_the_probe_takes_the_card_then_warp(monkeypatch):
    fake_adapters(monkeypatch, {0: ("Some GPU", [b"a"]), 1: (bc7gpu.WARP, [b"a"])})
    assert bc7gpu.probe() == "0,1"


@pytest.mark.parametrize(
    "cards",
    [
        {0: ("Some GPU", [b"a", b"b"])},  # two runs, two results
        {0: ("Some GPU", [b"a"]), 1: (bc7gpu.WARP, [b"b"])},  # the card and WARP disagree
        {0: (bc7gpu.WARP, [b"a"])},  # WARP alone is slower than the CPU encoder
        {},
    ],
)
def test_the_probe_refuses_a_gpu_it_cannot_trust(monkeypatch, cards):
    fake_adapters(monkeypatch, cards)
    assert bc7gpu.probe() == "none"


def test_encode_tries_the_next_adapter_and_writes_the_cpu_encoders_header(monkeypatch, tmp_path):
    monkeypatch.setenv(bc7gpu.ADAPTERS_ENV, "0,2")
    monkeypatch.setattr(bc7gpu, "_texconv", lambda: tmp_path / "texconv.exe")
    tried = []
    texconv_head = bytearray(bc7encode.dds_header(8, 8, 4, False))
    texconv_head[144:148] = (3).to_bytes(4, "little")  # texconv notes the alpha mode; ours leaves it at 0

    def run(texconv, index, dds, srgb):
        tried.append(index)
        return (None, None) if index == 0 else (bc7gpu.WARP, bytes(texconv_head) + b"BLOCKS")

    monkeypatch.setattr(bc7gpu, "run_encode", run)
    levels = [np.zeros((8 >> i, 8 >> i, 4), np.uint8) for i in range(4)]
    out = bc7gpu.encode(textures._rgba_dds(levels, False), False)
    assert tried == [0, 2]
    assert out == bc7encode.dds_header(8, 8, 4, False) + b"BLOCKS"
    monkeypatch.setattr(bc7gpu, "run_encode", lambda *a: (None, None))
    with pytest.raises(bc7gpu.GpuError):
        bc7gpu.encode(textures._rgba_dds(levels, False), False)


def test_a_run_texconv_did_on_its_cpu_codec_is_no_gpu_result(monkeypatch, tmp_path):
    """texconv takes its CPU codec without an error when the adapter is missing: other bytes."""

    def texconv(lines):
        def run(exe, args, cwd=None, timeout=None):
            out = bc7gpu.Path(args[args.index("-o") + 1]) / "levels.dds"
            out.write_bytes(b"DDS data")
            return ToolResult(0, lines, "")

        return run

    monkeypatch.setattr(
        bc7gpu, "run_tool", texconv("WARNING: DirectCompute is not available, using BC6H / BC7 CPU codec")
    )
    assert bc7gpu.run_encode(tmp_path / "texconv.exe", 7, b"x", False) == (None, None)
    monkeypatch.setattr(bc7gpu, "run_tool", texconv('[Using DirectCompute 5.0 on "Some GPU"]'))
    assert bc7gpu.run_encode(tmp_path / "texconv.exe", 0, b"x", False) == ("Some GPU", b"DDS data")


def test_at_most_jobs_gpu_encodes_run_at_once(monkeypatch, tmp_path):
    monkeypatch.setattr(bc7gpu, "slot_folder", lambda: tmp_path / "slots")
    monkeypatch.setattr(bc7gpu, "SLOT_WAIT", 0.01)
    monkeypatch.setenv(bc7gpu.JOBS_ENV, "1")
    entered = threading.Event()

    def second():
        with bc7gpu.slot():
            entered.set()

    with bc7gpu.slot() as first:
        assert first == 0
        worker = threading.Thread(target=second)
        worker.start()
        assert not entered.wait(0.3)  # the one slot is taken: the second encode waits, it does not go elsewhere
    assert entered.wait(5)
    worker.join(5)
    monkeypatch.setenv(bc7gpu.JOBS_ENV, "2")
    with bc7gpu.slot() as a, bc7gpu.slot() as b:
        assert {a, b} == {0, 1}


def test_the_conftest_keeps_the_gpu_off():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert not textures.gpu_on()
