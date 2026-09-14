"""seal_listening_test.py: N-way sealing and REAL metadata stripping.

Found in Codex's review of the SAM_V2 candidate plan (2026-09-10): the prior
version's docstring claimed metadata was not copied across ("the WAVs are
written fresh") while the code used shutil.copyfile - a raw byte copy that
carries every chunk of the source file across, including anything that could
disclose which side a clip is. This pins the fix with a real WAV carrying an
identifying LIST/INFO chunk.
"""

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import seal_listening_test as SLT  # noqa: E402


def _sine_wav(path: Path, freq: float, seconds: float = 0.2, sr: int = 44100) -> None:
    import numpy as np
    import soundfile as sf

    t = np.arange(int(sr * seconds)) / sr
    y = 0.2 * np.sin(2 * np.pi * freq * t)
    sf.write(str(path), np.stack([y, y], axis=1), sr, subtype="PCM_16")


def _append_identifying_list_chunk(path: Path, marker: bytes) -> None:
    """Hand-append a RIFF LIST/INFO/INAM chunk carrying `marker` after an
    existing, valid WAV file - simulating the kind of DAW/export metadata a
    real render might carry (title, source project name, anything that could
    disclose which side a blind clip is). Also fixes up the outer RIFF size
    so the result is still a valid, readable WAV."""
    raw = bytearray(path.read_bytes())
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"

    inam_data = marker + (b"\x00" if len(marker) % 2 else b"")
    inam_chunk = b"INAM" + struct.pack("<I", len(marker)) + inam_data
    list_body = b"INFO" + inam_chunk
    list_chunk = b"LIST" + struct.pack("<I", len(list_body)) + list_body

    raw.extend(list_chunk)
    new_riff_size = len(raw) - 8
    raw[4:8] = struct.pack("<I", new_riff_size)
    path.write_bytes(bytes(raw))


def test_three_way_seals_n_plus_one_clips_with_the_named_twin(tmp_path):
    a = tmp_path / "A.wav"
    b = tmp_path / "B.wav"
    c = tmp_path / "C.wav"
    for p, freq in ((a, 220.0), (b, 330.0), (c, 440.0)):
        _sine_wav(p, freq)

    out_dir = tmp_path / "sealed"
    # main() reads sys.argv directly; drive it that way like the CLI does.
    import contextlib
    import io

    argv = [
        "seal_listening_test.py",
        "--side", f"A={a}", "--side", f"B={b}", "--side", f"C={c}",
        "--twin-of", "B", "--out-dir", str(out_dir), "--seed", "42",
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = SLT.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    listen = out_dir / "Listen"
    clips = sorted(listen.glob("Clip *.wav"))
    assert len(clips) == 4  # 3 sides + 1 twin

    import json
    mapping = json.loads((out_dir / "_sealed" / "MAPPING.json").read_text())["mapping"]
    sides_seen = sorted(m["side"] for m in mapping)
    assert sides_seen == ["A", "B", "B_twin", "C"]


def test_twin_is_a_genuine_duplicate_of_the_named_side(tmp_path):
    import numpy as np
    import soundfile as sf

    a = tmp_path / "A.wav"
    b = tmp_path / "B.wav"
    _sine_wav(a, 220.0)
    _sine_wav(b, 330.0)

    out_dir = tmp_path / "sealed"
    argv = [
        "seal_listening_test.py",
        "--side", f"A={a}", "--side", f"B={b}",
        "--twin-of", "A", "--out-dir", str(out_dir), "--seed", "7",
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = SLT.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    import json
    mapping = json.loads((out_dir / "_sealed" / "MAPPING.json").read_text())["mapping"]
    a_clips = [m["clip"] for m in mapping if m["side"] in ("A", "A_twin")]
    assert len(a_clips) == 2
    data0, sr0 = sf.read(str(out_dir / "Listen" / a_clips[0]))
    data1, sr1 = sf.read(str(out_dir / "Listen" / a_clips[1]))
    assert sr0 == sr1
    assert np.allclose(data0, data1)


def test_metadata_does_not_survive_the_reseal(tmp_path):
    """The bug this whole file exists to pin: a raw byte-copy carries the
    source's LIST/INFO chunk (and anything else) straight through, which
    could disclose which side a clip is. The re-encoded output must not."""
    a = tmp_path / "A.wav"
    b = tmp_path / "B.wav"
    _sine_wav(a, 220.0)
    _sine_wav(b, 330.0)

    marker = b"SAM_V1_CANDIDATE_DO_NOT_SHIP_BLIND"
    _append_identifying_list_chunk(a, marker)
    assert marker in a.read_bytes()  # sanity: the fixture actually carries it

    out_dir = tmp_path / "sealed"
    argv = [
        "seal_listening_test.py",
        "--side", f"A={a}", "--side", f"B={b}",
        "--twin-of", "B", "--out-dir", str(out_dir), "--seed", "3",
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = SLT.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    for clip in (out_dir / "Listen").glob("Clip *.wav"):
        assert marker not in clip.read_bytes(), (
            f"{clip} still carries the source's identifying metadata - "
            "the reseal is not actually blind")


def test_how_to_listen_does_not_name_the_twins_side(tmp_path):
    """Codex review, 2026-09-14, FATAL-1: the instructions used to say e.g.
    "the 'A' side, duplicated" - naming which side was the twin let a
    listener infer that side's identity the moment they spotted the matching
    pair, without ever opening the sealed mapping. Pins that the twin's own
    label string no longer appears in the instructions."""
    a = tmp_path / "A.wav"
    b = tmp_path / "B.wav"
    _sine_wav(a, 220.0)
    _sine_wav(b, 330.0)

    out_dir = tmp_path / "sealed"
    argv = [
        "seal_listening_test.py",
        "--side", f"A={a}", "--side", f"B={b}",
        "--twin-of", "A", "--out-dir", str(out_dir), "--seed", "11",
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = SLT.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    text = (out_dir / "Listen" / "HOW TO LISTEN.txt").read_text(encoding="utf-8")
    assert "'A'" not in text and '"A"' not in text and "A side" not in text
    assert "duplicate" in text.lower()  # still explains a twin exists


def test_rejects_side_renders_with_different_sample_rates(tmp_path):
    """Codex review, 2026-09-14, MINOR-1: a differing technical format
    (sample rate/channels/subtype) survives re-encoding and could itself be
    a side tell even with content and metadata otherwise blind."""
    import numpy as np
    import soundfile as sf

    a = tmp_path / "A.wav"
    b = tmp_path / "B.wav"
    _sine_wav(a, 220.0, sr=44100)
    t = np.arange(int(48000 * 0.2)) / 48000
    y = 0.2 * np.sin(2 * np.pi * 330.0 * t)
    sf.write(str(b), np.stack([y, y], axis=1), 48000, subtype="PCM_16")  # different SR

    argv = [
        "seal_listening_test.py",
        "--side", f"A={a}", "--side", f"B={b}",
        "--twin-of", "A", "--out-dir", str(tmp_path / "sealed"), "--seed", "1",
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        with pytest.raises(SystemExit, match="do not share the same"):
            SLT.main()
    finally:
        sys.argv = old_argv


def test_rerunning_with_fewer_sides_does_not_leave_a_stale_clip(tmp_path):
    """Codex review, 2026-09-14, MAJOR-3: reusing --out-dir across runs used
    to leave an earlier run's extra Clip N.wav sitting in Listen/, unmapped
    by the new run's MAPPING.json."""
    a = tmp_path / "A.wav"
    b = tmp_path / "B.wav"
    c = tmp_path / "C.wav"
    for p, freq in ((a, 220.0), (b, 330.0), (c, 440.0)):
        _sine_wav(p, freq)

    out_dir = tmp_path / "sealed"
    import contextlib
    import io

    def _run(sides):
        argv = ["seal_listening_test.py"]
        for label, path in sides:
            argv += ["--side", f"{label}={path}"]
        argv += ["--twin-of", sides[0][0], "--out-dir", str(out_dir), "--seed", "5"]
        old_argv = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return SLT.main()
        finally:
            sys.argv = old_argv

    assert _run([("A", a), ("B", b), ("C", c)]) == 0  # 4 clips (3 + twin)
    assert _run([("A", a), ("B", b)]) == 0  # 3 clips (2 + twin) - fewer sides

    clips = sorted((out_dir / "Listen").glob("Clip *.wav"))
    assert len(clips) == 3

    import json
    mapping = json.loads((out_dir / "_sealed" / "MAPPING.json").read_text())["mapping"]
    mapped_names = {m["clip"] for m in mapping}
    assert mapped_names == {p.name for p in clips}  # every clip on disk is mapped, nothing stale


def test_rejects_a_twin_of_label_that_was_not_supplied(tmp_path):
    a = tmp_path / "A.wav"
    b = tmp_path / "B.wav"
    _sine_wav(a, 220.0)
    _sine_wav(b, 330.0)
    argv = [
        "seal_listening_test.py",
        "--side", f"A={a}", "--side", f"B={b}",
        "--twin-of", "Z", "--out-dir", str(tmp_path / "sealed"), "--seed", "1",
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        with pytest.raises(SystemExit):
            SLT.main()
    finally:
        sys.argv = old_argv
