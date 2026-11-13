import pytest

from radio_web_remote.audio import pcm
from radio_web_remote.audio.backend import _Fifo
from radio_web_remote.audio.devices import AudioDevice, AudioDeviceError, resolve


def test_frame_roundtrip_and_layout():
    data = pcm.tone(1000, 20)
    frame = pcm.encode_frame(pcm.TYPE_TX_AUDIO, 7, data)
    assert pcm.HEADER_SIZE == 12 and len(frame) == 12 + 640
    seq, rate, body = pcm.decode_frame(frame, pcm.TYPE_TX_AUDIO)
    assert (seq, rate, body) == (7, 16_000, data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f[:5],  # short
        lambda f: bytes([0x02]) + f[1:],  # wrong type
        lambda f: f[:8] + (8000).to_bytes(2, "little") + f[10:],  # wrong rate
        lambda f: f[:-2],  # length mismatch
    ],
)
def test_frame_rejects_bad_input(mutate):
    frame = pcm.encode_frame(pcm.TYPE_TX_AUDIO, 1, pcm.tone(1000, 20))
    with pytest.raises(pcm.AudioFrameError):
        pcm.decode_frame(mutate(frame), pcm.TYPE_TX_AUDIO)


def test_frame_size_cap():
    big = pcm.encode_frame(pcm.TYPE_TX_AUDIO, 1, bytes(2 * (pcm.MAX_FRAME_SAMPLES + 1)))
    with pytest.raises(pcm.AudioFrameError):
        pcm.decode_frame(big, pcm.TYPE_TX_AUDIO)


def test_peak_dbfs():
    assert pcm.peak_dbfs(bytes(640)) == -120.0
    assert pcm.peak_dbfs(pcm.tone(1000, 20, amplitude=0.5)) == pytest.approx(-6.0, abs=0.2)


def test_resample_lengths_and_level():
    x48 = pcm.tone(1000, 20, rate=48_000, amplitude=0.5)
    x16 = pcm.downsample3(x48)
    assert len(x16) == len(x48) // 3
    assert pcm.peak_dbfs(x16) == pytest.approx(-6.0, abs=0.5)  # 1 kHz passes the crude filter
    assert len(pcm.upsample3(x16)) == len(x16) * 3


def test_fifo_prefill_underrun_and_cap():
    f = _Fifo(max_bytes=1000, prefill_bytes=200)
    f.write(b"\x01" * 100)
    assert f.read(50) == bytes(50)  # still priming: silence
    f.write(b"\x02" * 150)
    assert f.read(100)[:1] == b"\x01"
    f.write(b"\x03" * 2000)  # far too much: oldest audio dropped, latency stays bounded
    assert f.buffered_ms <= 1000 / 2 / 16_000 * 1000 + 1e-9
    assert f.dropped_bytes > 0


DEVICES = [
    AudioDevice(0, "MacBook Pro Speakers", 0, 2, "Core Audio"),
    AudioDevice(3, "USB Audio CODEC", 0, 2, "Core Audio"),
    AudioDevice(4, "USB Audio CODEC", 2, 0, "Core Audio"),
    AudioDevice(6, "MacBook Pro Microphone", 1, 0, "Core Audio"),
]


def test_resolve_auto_finds_the_codec_per_direction():
    assert resolve("auto", "input", DEVICES) == 4
    assert resolve("auto", "output", DEVICES) == 3


def test_resolve_by_name_and_index():
    assert resolve("macbook pro mic", "input", DEVICES) == 6
    assert resolve(0, "output", DEVICES) == 0
    assert resolve(None, "input", DEVICES) is None


def test_resolve_errors():
    with pytest.raises(AudioDeviceError, match="index 0"):
        resolve(0, "input", DEVICES)  # speakers have no input
    two = [*DEVICES, AudioDevice(9, "USB Audio CODEC", 2, 0, "Core Audio")]
    with pytest.raises(AudioDeviceError, match="set it by index"):
        resolve("auto", "input", two)
    with pytest.raises(AudioDeviceError, match="no input device"):
        resolve("zzz", "input", DEVICES)
