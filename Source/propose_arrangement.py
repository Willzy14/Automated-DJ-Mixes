"""Propose and apply an arrangement for a Sections .als.

Given a Sections .als (with corrected chops, no arrangement) and the matching
sections JSON, this script:

  1. Computes ideal track positions using natural-fill alignment
  2. Analyses each overlap zone for loop requirements
  3. Consults pair_history.jsonl for similar past transitions
  4. Applies position shifts + loop extensions to the ALS
  5. Outputs an arrangement report for Sam to review

This is the PROPOSE mode of the /arrange-mix skill. LEARN mode lives in
learn_from_correction.py.

Usage:
    python Source/propose_arrangement.py <sections.als> <sections.json> <output.als>

Options:
    --history PATH     Path to pair_history.jsonl (default: auto-detect)
    --report PATH      Path to write ARRANGEMENT_REPORT.json (default: next to output)
    --dry-run          Print decisions without modifying the ALS

Can also be imported:
    from propose_arrangement import propose_arrangement, ArrangementPlan
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from automated_dj_mixes.sequencer import compatibility_score as _compat_score
except ImportError:
    _compat_score = None

# Reuse proven line-based ALS patching from apply_loops
from apply_loops import (
    LoopSpec,
    _match_track,        # canonical track matcher — DO NOT redefine locally
    _normalise,
    apply_loops,
    compress_als,
    decompress_als,
    find_track_line_ranges,
    remove_named_clips,
    shift_track_clips,
)


# -- Constants ----------------------------------------------------------------

# Overlap constraints (beats)
MIN_OVERLAP_BEATS = 64   # 16 bars - minimum for a usable transition
MAX_OVERLAP_BEATS = 192  # 48 bars - longer than this is unusual

# Loop granularity - all loops must be multiples of this
LOOP_GRANULARITY = 4     # 1 bar at 4/4

# Target overlap when extending with loops
TARGET_OVERLAP_BEATS = 128  # 32 bars - comfortable default

# How far to search in pair_history for similar transitions
BPM_MATCH_TOLERANCE = 2.0  # +/- BPM for "similar" pairs


# -- Data structures ----------------------------------------------------------

@dataclass
class TrackInfo:
    """Track with its section structure and arrangement position."""
    name: str
    sections: list[dict]
    arr_start: float       # beat where first clip starts on timeline
    arr_end: float         # beat where last clip ends on timeline
    source_end: float = 0  # last source beat (from sections JSON)
    camelot: str | None = None
    bpm: float | None = None
    energy: float | None = None
    intro_skip_bars: int = 0

    @property
    def track_length(self) -> float:
        return self.arr_end - self.arr_start

    @property
    def source_length(self) -> float:
        if not self.sections:
            return 0
        return self.sections[-1]["source_end_beats"] - self.sections[0]["source_start_beats"]


@dataclass
class OverlapAnalysis:
    """Analysis of one pair's overlap zone."""
    out_track: str
    in_track: str
    pair_index: int
    overlap_start: float
    overlap_end: float
    overlap_beats: float
    overlap_bars: float
    status: str            # "ok", "short", "none"
    out_tail_loop: LoopSpec | None = None
    in_intro_loop: LoopSpec | None = None
    shift_delta: float = 0.0
    similar_pairs: list[dict] = field(default_factory=list)
    notes: str = ""


@dataclass
class ArrangementPlan:
    """Full arrangement plan for a mix."""
    tracks: list[TrackInfo]
    overlaps: list[OverlapAnalysis]
    shifts: list[tuple[str, float]]      # (track_name, delta_beats)
    loops: list[LoopSpec]
    notes: list[str] = field(default_factory=list)


# -- Section helpers ----------------------------------------------------------

def _label(sec: dict) -> str:
    return sec.get("label", "").lower()


def ordered_tracks(sections: dict) -> list[TrackInfo]:
    """Build TrackInfo list from sections JSON, sorted by arrangement start."""
    tracks: list[TrackInfo] = []
    for name, secs in sections.items():
        if not secs:
            continue
        tracks.append(TrackInfo(
            name=name,
            sections=secs,
            arr_start=secs[0]["arr_time"],
            arr_end=secs[-1]["arr_end"],
            source_end=secs[-1]["source_end_beats"],
        ))
    tracks.sort(key=lambda t: t.arr_start)
    return tracks


def first_drop_source(track: TrackInfo) -> float | None:
    """Source-beat position of the track's first drop section."""
    for s in track.sections:
        if _label(s) == "drop":
            return s["source_start_beats"]
    return None


def first_rise_source(track: TrackInfo) -> float | None:
    """Source-beat of the first energy rise after the intro.

    Returns the start of the first build or drop — whichever comes first.
    For tracks with a build (intro → build → drop), this returns the
    build start so the build develops over the transition and the drop
    hits cleanly after the outgoing has faded.
    For tracks without a build (intro → drop), returns the drop start.
    """
    for s in track.sections:
        if _label(s) in ("build", "drop"):
            return s["source_start_beats"]
    return None


def first_drop_arr(track: TrackInfo) -> float | None:
    """Arrangement-beat position of the track's first drop section."""
    for s in track.sections:
        if _label(s) == "drop":
            return s["arr_time"]
    return None


def last_natural_swap(track: TrackInfo) -> float:
    """Source-beat of the LAST fill/break/build before outro.

    This is the outgoing's natural "hand-off" point - where its energy
    starts to wind down and is the ideal place for the incoming to take over.
    Matches the logic from arrange_sections.py.
    """
    # Find outro index
    outro_idx = len(track.sections)
    for i, s in enumerate(track.sections):
        if _label(s) == "outro":
            outro_idx = i
            break

    # Walk backward from outro looking for fill/break/build
    for s in reversed(track.sections[:outro_idx]):
        if _label(s) in ("fill", "break", "braak", "build"):
            return s["source_start_beats"]

    # Fallback: outro start itself (or last section)
    if outro_idx < len(track.sections):
        return track.sections[outro_idx]["source_start_beats"]
    return track.sections[-1]["source_start_beats"]


def outro_start_source(track: TrackInfo) -> float | None:
    """Source-beat where the outro starts. None if no outro."""
    for s in track.sections:
        if _label(s) == "outro":
            return s["source_start_beats"]
    return None


# Energy proxy by section type (higher = more energy)
_SECTION_ENERGY = {
    "drop": 10,
    "build": 6,
    "fill": 4,
    "break": 3,
    "outro": 2,
    "intro": 2,
}


def best_swap_source(track: TrackInfo) -> float | None:
    """Find the best swap point: the boundary with the biggest bass drop.

    Evaluates three candidates near the end of the track:
      1. End of the last drop (where it transitions to something lower)
      2. End of the last fill (where it transitions to something lower)
      3. Start of the outro

    Picks whichever has the biggest energy contrast (high -> low).
    That's where the bass drops out — the natural DJ hand-off point.
    """
    secs = track.sections
    if len(secs) < 2:
        return None

    # Build boundary list: (source_beat, energy_before, energy_after, before_type)
    boundaries = []
    for i in range(len(secs) - 1):
        before = secs[i]
        after = secs[i + 1]
        b_type = _label(before)
        a_type = _label(after)
        e_before = _SECTION_ENERGY.get(b_type, 5)
        e_after = _SECTION_ENERGY.get(a_type, 5)
        boundaries.append({
            "src": after["source_start_beats"],
            "delta": e_before - e_after,
            "before_type": b_type,
            "after_type": a_type,
        })

    # Three candidates: last drop end, last fill end, outro start
    last_drop_end = None
    last_fill_end = None
    outro_start = None

    for b in boundaries:
        if b["before_type"] == "drop" and b["delta"] > 0:
            last_drop_end = b  # keeps overwriting → last one wins
        if b["before_type"] == "fill" and b["delta"] > 0:
            last_fill_end = b
        if b["after_type"] == "outro":
            outro_start = b

    # Pick the one with the highest energy delta
    best = None
    for candidate in (last_drop_end, last_fill_end, outro_start):
        if candidate is None:
            continue
        if candidate["delta"] <= 0:
            continue
        if best is None or candidate["delta"] > best["delta"]:
            best = candidate

    if best:
        return best["src"]
    return None


def intro_sections(track: TrackInfo) -> list[dict]:
    """Return all intro sections at the start of the track."""
    intros = []
    for s in track.sections:
        if _label(s) in ("intro", "fill", "break", "braak", "build"):
            intros.append(s)
        elif _label(s) == "drop":
            break  # Stop at first drop
        else:
            intros.append(s)  # Include unlabelled pre-drop sections
    return intros


def pre_outro_section(track: TrackInfo) -> dict | None:
    """Return the last non-outro section (the tail that gets looped)."""
    outro_idx = len(track.sections)
    for i, s in enumerate(track.sections):
        if _label(s) == "outro":
            outro_idx = i
            break

    if outro_idx > 0:
        return track.sections[outro_idx - 1]
    return None


# -- Alignment computation ---------------------------------------------------

def compute_natural_positions(tracks: list[TrackInfo]) -> list[tuple[str, float, float, float]]:
    """Compute ideal arrangement positions using dual-cut alignment.

    For each pair: outgoing's biggest bass-drop boundary aligns with
    incoming's first energy rise (build or drop after intro).

    The build develops over the transition while the outgoing fades,
    then the incoming's drop hits cleanly after the outgoing is gone.

    Returns: [(track_name, current_arr_start, new_arr_start, delta_beats), ...]
    """
    result: list[tuple[str, float, float, float]] = []

    for i, track in enumerate(tracks):
        current_arr = track.arr_start

        if i == 0:
            # First track stays where it is
            new_arr = current_arr
        else:
            prev_track = tracks[i - 1]
            prev_new_arr = result[-1][2]  # new arr_start of previous track

            # Outgoing's best bass-drop boundary = the swap point (dual cut)
            swap_src = best_swap_source(prev_track)
            if swap_src is None:
                swap_src = outro_start_source(prev_track)
            if swap_src is None:
                swap_src = last_natural_swap(prev_track)  # fallback

            # Incoming's first energy rise (build or drop after intro)
            rise_src = first_rise_source(track)
            if rise_src is None:
                rise_src = 0.0  # No rise found, align from start

            # Position incoming so its first energy rise lands at
            # outgoing's swap — build develops over the transition,
            # drop hits after the outgoing fades
            new_arr = prev_new_arr + swap_src - rise_src

            # Ensure tracks don't go backwards
            new_arr = max(new_arr, prev_new_arr)

            # No overlap cap — trust the natural dual-cut alignment.
            # Short overlaps are handled by _plan_loop_extensions().

        delta = new_arr - current_arr
        result.append((track.name, current_arr, new_arr, delta))

    return result


# -- Overlap analysis ---------------------------------------------------------

def analyse_overlap(out_track: TrackInfo, in_track: TrackInfo,
                    pair_index: int) -> OverlapAnalysis:
    """Analyse the overlap between two positioned tracks.

    Determines if overlap is sufficient, and if not, what loops are needed.
    """
    ov_start = in_track.arr_start
    ov_end = out_track.arr_end
    ov_beats = ov_end - ov_start
    ov_bars = ov_beats / 4

    status = "ok"
    notes_parts: list[str] = []

    if ov_beats <= 0:
        status = "none"
        notes_parts.append("No overlap - tracks don't intersect")
    elif ov_beats < MIN_OVERLAP_BEATS:
        status = "short"
        notes_parts.append(
            "Overlap {:.0f} beats ({:.0f} bars) < minimum {:.0f} beats".format(
                ov_beats, ov_bars, MIN_OVERLAP_BEATS))

    analysis = OverlapAnalysis(
        out_track=out_track.name,
        in_track=in_track.name,
        pair_index=pair_index,
        overlap_start=ov_start,
        overlap_end=ov_end,
        overlap_beats=ov_beats,
        overlap_bars=ov_bars,
        status=status,
        notes="; ".join(notes_parts) if notes_parts else "",
    )

    # If overlap is too short, compute loop extensions
    if status in ("short", "none"):
        _plan_loop_extensions(out_track, in_track, analysis)

    return analysis


def _clean_tail_loop(wav_path, section: dict, loop_len: int,
                     bpm: float) -> tuple[float, float] | None:
    """Pick a dead-air-free `loop_len`-beat window inside `section`.

    Reuses the tuned amplitude_analysis.find_clean_loop_window (walks back
    from the section end skipping any sub-silence frames) so a tail loop
    never lands on a dissipating/silent region. Returns (src_start_beat,
    src_end_beat) in beats, or None to keep the section-default region.
    """
    try:
        from automated_dj_mixes.amplitude_analysis import find_clean_loop_window
        spb = 60.0 / bpm
        res = find_clean_loop_window(
            wav_path,
            section["source_start_beats"] * spb,
            section["source_end_beats"] * spb,
            int(loop_len),
            bpm,
        )
    except Exception as exc:
        print(f"    [loop] quality check skipped: {exc}")
        return None
    if not res:
        return None
    clean_start_sec, _ = res
    start_beat = float(round((clean_start_sec / spb) / LOOP_GRANULARITY)
                       * LOOP_GRANULARITY)
    return start_beat, start_beat + loop_len


def _plan_loop_extensions(out_track: TrackInfo, in_track: TrackInfo,
                          analysis: OverlapAnalysis) -> None:
    """Determine what loops are needed to bring overlap to target.

    Strategy matches Sam's V20 patterns:
    - Outgoing tail loops: repeat last N beats of pre-outro section
    - Incoming intro loops: repeat first N beats of intro
    - Split the deficit roughly evenly between outgoing and incoming
    """
    current_overlap = max(analysis.overlap_beats, 0)
    deficit = TARGET_OVERLAP_BEATS - current_overlap

    if deficit <= 0:
        return

    # Round deficit up to LOOP_GRANULARITY
    deficit = ((int(deficit) + LOOP_GRANULARITY - 1) // LOOP_GRANULARITY) * LOOP_GRANULARITY

    # Split deficit: prefer extending outgoing tail first, then incoming intro
    out_extension = 0
    in_extension = 0

    # -- Outgoing tail loop --
    tail = pre_outro_section(out_track)
    if tail:
        # V20 pattern: loop the last N beats of the pre-outro section
        # Typical: 4-beat kick pattern loops (Adam Ten) or 16-beat phrase loops
        tail_len = tail["source_end_beats"] - tail["source_start_beats"]

        # Choose loop length: prefer 4 beats for short sections, 16 for longer
        if tail_len >= 16:
            loop_len = 16
        elif tail_len >= 8:
            loop_len = 8
        else:
            loop_len = 4

        # How many reps to cover half the deficit?
        half_deficit = deficit // 2
        out_reps = max(1, half_deficit // loop_len)
        out_extension = out_reps * loop_len

        # Source region: last loop_len beats of the section, UNLESS the
        # outgoing track carries a loop_source_sec hint, which directs where
        # in the source the loop comes from (Sam's eye on the waveform).
        hint_sec = getattr(out_track, "loop_source_sec", None)
        if hint_sec and out_track.bpm:
            hint_beat = hint_sec * out_track.bpm / 60.0
            src_start = float(round(hint_beat / LOOP_GRANULARITY) * LOOP_GRANULARITY)
            src_end = src_start + loop_len
        else:
            src_end = tail["source_end_beats"]
            src_start = src_end - loop_len
            # Quality gate: avoid looping a dissipating/silent tail. Search
            # the pre-outro section for a dead-air-free window of the same
            # length; fall back to the default region if none is found.
            wav = getattr(out_track, "wav_path", None)
            if wav and out_track.bpm:
                cleaned = _clean_tail_loop(wav, tail, loop_len, out_track.bpm)
                if cleaned:
                    src_start, src_end = cleaned
                    print("    [loop] clean tail window -> {:.0f}-{:.0f}b".format(
                        src_start, src_end))

        # Insert position: BEFORE the outro starts. This produces the
        # musically-correct sequence:
        #     ... drop_N  →  tail_loop × out_reps  →  outro_N  →  end
        # rather than the broken sequence (which we used to produce):
        #     ... drop_N  →  outro_N  →  tail_loop × out_reps  →  end
        # which makes energy go full → fade → BACK to full, which is wrong.
        #
        # We have to push the outro itself back by `out_extension` beats to
        # make room — that shift is encoded in `shifts_before_insert` so
        # apply_loops applies it before inserting the new clips.
        outro_sec = next((s for s in out_track.sections
                          if _label(s) == "outro"), None)
        if outro_sec is not None:
            insert_beat = outro_sec["arr_time"]
            outro_shift = [(outro_sec.get("name", "outro_1"),
                            float(out_extension))]
            # Reflect the shift in our in-memory model so downstream
            # bookkeeping (track length, subsequent overlap calcs, report)
            # stays consistent.
            outro_sec["arr_time"] += out_extension
            outro_sec["arr_end"] += out_extension
            out_track.arr_end = max(out_track.arr_end,
                                    outro_sec["arr_end"])
        else:
            # No outro section — fall back to old behaviour, append at end.
            insert_beat = out_track.arr_end
            outro_shift = []

        analysis.out_tail_loop = LoopSpec(
            track_name=out_track.name,
            source_beat_start=src_start,
            source_beat_end=src_end,
            count=out_reps,
            insert_at_beat=insert_beat,
            clip_name="{}_tail_loop".format(tail.get("name", "tail")),
            shifts_before_insert=outro_shift,
        )

    # -- Incoming intro loop --
    intro_secs = intro_sections(in_track)
    if intro_secs:
        remaining = deficit - out_extension
        if remaining > 0:
            # V20 pattern: loop first 16-32 beats of intro
            # Route 94 skipped bar 0, used 16-32; EMM used 0-32
            first_intro = intro_secs[0]
            intro_len = first_intro["source_end_beats"] - first_intro["source_start_beats"]

            if intro_len >= 32:
                loop_len = 32
            elif intro_len >= 16:
                loop_len = 16
            elif intro_len >= 8:
                loop_len = 8
            else:
                loop_len = 4

            in_reps = max(1, remaining // loop_len)
            in_extension = in_reps * loop_len

            # Source region: first loop_len beats of intro
            src_start = first_intro["source_start_beats"]
            src_end = src_start + loop_len

            # Insert position: BEFORE the current first clip
            # This means we need to shift all existing clips later
            # For now, insert at current arr_start (clips will be shifted)
            insert_beat = in_track.arr_start - in_extension

            analysis.in_intro_loop = LoopSpec(
                track_name=in_track.name,
                source_beat_start=src_start,
                source_beat_end=src_end,
                count=in_reps,
                insert_at_beat=insert_beat,
                clip_name="{}_intro_loop".format(first_intro.get("name", "intro")),
            )

    # Update analysis notes
    parts = []
    if out_extension > 0:
        parts.append("out +{:.0f}b tail loop ({:.0f}b x {:d})".format(
            out_extension,
            analysis.out_tail_loop.source_beat_end - analysis.out_tail_loop.source_beat_start,
            analysis.out_tail_loop.count))
    if in_extension > 0:
        parts.append("in +{:.0f}b intro loop ({:.0f}b x {:d})".format(
            in_extension,
            analysis.in_intro_loop.source_beat_end - analysis.in_intro_loop.source_beat_start,
            analysis.in_intro_loop.count))
    if parts:
        new_overlap = current_overlap + out_extension + in_extension
        analysis.notes += "; loops: " + ", ".join(parts)
        analysis.notes += " -> {:.0f}b overlap".format(new_overlap)


# -- Pair history matching ----------------------------------------------------

def load_pair_history(path: Path | None) -> list[dict]:
    """Load pair_history.jsonl. Returns empty list if not found."""
    if path is None:
        # Auto-detect
        candidates = [
            Path("Documentation/Mix Patterns Library/pair_history.jsonl"),
            Path(__file__).parent.parent / "Documentation" / "Mix Patterns Library" / "pair_history.jsonl",
        ]
        for c in candidates:
            if c.exists():
                path = c
                break
    if path is None or not path.exists():
        return []

    pairs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def _structure_signature(sections: list[str]) -> tuple[int, int, int, int]:
    """Compact fingerprint of a section structure: (drops, breaks, fills, intros)."""
    drops = sum(1 for s in sections if s.lower().startswith("drop"))
    breaks = sum(1 for s in sections if s.lower().startswith(("break", "braak")))
    fills = sum(1 for s in sections if s.lower().startswith("fill"))
    intros = sum(1 for s in sections if s.lower().startswith("intro"))
    return (drops, breaks, fills, intros)


def find_similar_pairs(out_track: TrackInfo, in_track: TrackInfo,
                       bpm: float, history: list[dict],
                       max_results: int = 3) -> list[dict]:
    """Find similar past transitions from pair_history.

    Similarity: BPM match (weight 0.3) + structure shape match (weight 0.7).
    """
    if not history:
        return []

    out_names = [s.get("name", "") for s in out_track.sections]
    in_names = [s.get("name", "") for s in in_track.sections]
    out_sig = _structure_signature(out_names)
    in_sig = _structure_signature(in_names)

    scored: list[tuple[float, dict]] = []

    for pair in history:
        # BPM score: 1.0 if exact, 0.0 if > tolerance
        pair_bpm = pair.get("bpm_out", 0)
        bpm_diff = abs(bpm - pair_bpm)
        if bpm_diff > BPM_MATCH_TOLERANCE:
            bpm_score = 0.0
        else:
            bpm_score = 1.0 - (bpm_diff / BPM_MATCH_TOLERANCE)

        # Structure score: compare signatures
        pair_out_struct = pair.get("out_structure", [])
        pair_in_struct = pair.get("in_structure", [])
        pair_out_sig = _structure_signature(pair_out_struct)
        pair_in_sig = _structure_signature(pair_in_struct)

        # Simple distance: sum of absolute differences in each count
        out_dist = sum(abs(a - b) for a, b in zip(out_sig, pair_out_sig))
        in_dist = sum(abs(a - b) for a, b in zip(in_sig, pair_in_sig))
        total_dist = out_dist + in_dist
        # Normalise: max possible distance ~ 30, so /30 gives 0-1 range
        struct_score = max(0.0, 1.0 - total_dist / 30.0)

        total = 0.3 * bpm_score + 0.7 * struct_score
        if total > 0.1:  # minimum threshold
            scored.append((total, pair))

    scored.sort(key=lambda x: -x[0])
    return [pair for _, pair in scored[:max_results]]


# -- Main orchestrator --------------------------------------------------------

def propose_arrangement(als_path: Path, sections_path: Path,
                        output_path: Path,
                        history_path: Path | None = None,
                        hints_path: Path | None = None,
                        dry_run: bool = False) -> ArrangementPlan:
    """Propose and optionally apply a full arrangement.

    Steps:
      1. Load sections JSON -> build track list
      2. Enrich tracks with hint metadata (intro_skip_bars, etc.)
      3. Compute natural positions (shifts)
      4. Analyse overlaps, determine loop requirements
      5. Consult pair history for similar transitions
      6. Apply shifts + loops to the ALS (unless dry_run)
      7. Return the arrangement plan
    """
    # -- Load sections JSON --
    with open(sections_path, encoding="utf-8") as f:
        sections = json.load(f)

    tracks = ordered_tracks(sections)

    # -- Enrich from hints --
    hints: dict = {}
    if hints_path and hints_path.exists():
        try:
            hints = json.loads(hints_path.read_text(encoding="utf-8"))
            print("Loaded hints from {}".format(hints_path.name))
        except Exception:
            pass
    # -- Enrich from MIK DB (key, BPM, energy) --
    try:
        from automated_dj_mixes.mik_reader import read_mik_db_track
        from automated_dj_mixes.sequencer import key_to_camelot
        audio_dir = als_path.parent.parent / "Audio"
        if not audio_dir.exists():
            audio_dir = als_path.parent / "Audio"
        for t in tracks:
            wav_name = t.name if t.name.endswith(".wav") else t.name + ".wav"
            wav_path = audio_dir / wav_name
            if wav_path.exists():
                t.wav_path = wav_path
                mik = read_mik_db_track(wav_path)
                if mik:
                    if mik.key:
                        t.camelot = key_to_camelot(mik.key) or mik.key
                    if mik.bpm:
                        t.bpm = mik.bpm
                    if mik.energy is not None:
                        t.energy = mik.energy
    except ImportError:
        pass

    for t in tracks:
        hint = hints.get(t.name) or hints.get(t.name + ".wav") or {}
        t.intro_skip_bars = hint.get("intro_skip_bars", 0)
        t.loop_source_sec = hint.get("loop_source_sec")
        if t.intro_skip_bars:
            skip_beats = t.intro_skip_bars * 4
            # Capture the clip names of the sections we're dropping so their
            # clips can be removed from the .als (otherwise the trimmed intro
            # still plays — it's only dropped from the alignment maths here).
            t.dropped_clip_names = [s.get("name", "") for s in t.sections
                                    if s["source_end_beats"] <= skip_beats]
            t.sections = [s for s in t.sections
                          if s["source_end_beats"] > skip_beats]
            if t.sections:
                t.arr_start = t.sections[0]["arr_time"]
            print("  {} — skipping first {} bars (intro trim, {} clip(s))".format(
                t.name[:40], t.intro_skip_bars, len(t.dropped_clip_names)))
    print("Loaded {} tracks from sections JSON".format(len(tracks)))
    for i, t in enumerate(tracks):
        meta = ""
        if t.camelot:
            meta += " {}".format(t.camelot)
        if t.bpm:
            meta += " {:.0f}BPM".format(t.bpm)
        if t.energy is not None:
            meta += " E{:.0f}".format(t.energy)
        print("  {:>2d}. {} ({:.0f} - {:.0f}, {:.0f}b, {} sections{})".format(
            i + 1, t.name[:50], t.arr_start, t.arr_end,
            t.track_length, len(t.sections), meta))

    # -- Compute natural positions --
    print("\n--- Natural-fill alignment ---")
    positions = compute_natural_positions(tracks)
    shifts: list[tuple[str, float]] = []
    for name, cur, new, delta in positions:
        marker = " ** SHIFT **" if abs(delta) > 0.5 else ""
        print("  {} : {:.0f} -> {:.0f} ({:+.0f}){}".format(
            name[:45], cur, new, delta, marker))
        if abs(delta) > 0.5:
            shifts.append((name, delta))

    # Apply shifts to track positions (in-memory, for overlap analysis)
    for track in tracks:
        for name, delta in shifts:
            if track.name == name:
                track.arr_start += delta
                track.arr_end += delta
                # Update section arr_time/arr_end to match
                for s in track.sections:
                    s["arr_time"] += delta
                    s["arr_end"] += delta
                break

    # -- Analyse overlaps --
    print("\n--- Overlap analysis ---")
    history = load_pair_history(history_path)
    if history:
        print("  Loaded {} pairs from history".format(len(history)))

    overlaps: list[OverlapAnalysis] = []
    all_loops: list[LoopSpec] = []

    for i in range(len(tracks) - 1):
        out_t = tracks[i]
        in_t = tracks[i + 1]

        analysis = analyse_overlap(out_t, in_t, i + 1)

        # Check pair history for similar transitions
        bpm = out_t.bpm or in_t.bpm or 128.0
        similar = find_similar_pairs(out_t, in_t, bpm, history)
        analysis.similar_pairs = similar

        if similar:
            verdicts = [p.get("verdict", "?") for p in similar[:3]]
            analysis.notes += "; similar pairs: {}".format(
                ", ".join("{}:{}".format(p.get("pair_index", "?"), v)
                          for p, v in zip(similar[:3], verdicts)))

        overlaps.append(analysis)

        # Collect loops
        if analysis.out_tail_loop:
            all_loops.append(analysis.out_tail_loop)
        if analysis.in_intro_loop:
            all_loops.append(analysis.in_intro_loop)

        # Print summary
        status_icon = {"ok": "+", "short": "!", "none": "X"}
        print("  T{} [{}] {} -> {}".format(
            analysis.pair_index,
            status_icon.get(analysis.status, "?"),
            out_t.name[:30],
            in_t.name[:30]))
        print("      overlap: {:.0f}b ({:.0f} bars) - {}".format(
            analysis.overlap_beats, analysis.overlap_bars, analysis.status))
        if analysis.notes:
            print("      {}".format(analysis.notes))

    plan = ArrangementPlan(
        tracks=tracks,
        overlaps=overlaps,
        shifts=shifts,
        loops=all_loops,
    )

    # -- Apply to ALS (unless dry-run) --
    if not dry_run:
        print("\n--- Applying to ALS ---")
        lines = decompress_als(als_path)
        print("  Read {} lines from {}".format(len(lines), als_path.name))

        # Step 0: Remove clips for intro-skipped sections so the trimmed
        # intro doesn't play (intro_skip_bars). Done before shifts so only the
        # remaining clips get repositioned.
        for track in tracks:
            dropped = getattr(track, "dropped_clip_names", None)
            if dropped:
                n = remove_named_clips(lines, track.name, dropped)
                if n:
                    print("  Removed {} intro clip(s) from '{}' (intro skip)".format(
                        n, track.name[:40]))

        # Step 1: Apply position shifts
        if shifts:
            als_tracks = find_track_line_ranges(lines)
            for track_name, delta in shifts:
                matched = _match_track(track_name, als_tracks)
                if matched:
                    start, end, tname = matched
                    shift_track_clips(lines, start, end, delta)
                    # Print BOTH request and matched names so mismatches
                    # surface in the log (the 22.05.26 'Your Love' bug
                    # was hidden behind 40-char truncation).
                    if _normalise(track_name) != _normalise(tname):
                        print("  ⚠  Shifted [request='{}' → matched='{}'] by {:+.0f}".format(
                            track_name[:60], tname[:60], delta))
                    else:
                        print("  Shifted '{}' by {:+.0f} beats".format(
                            tname[:60], delta))
                else:
                    print("  WARNING: track '{}' not found for shift".format(
                        track_name))

        # Step 2: Apply loop extensions
        if all_loops:
            print("  Applying {} loop specs...".format(len(all_loops)))
            lines = apply_loops(lines, all_loops)

        # Write output
        compress_als(lines, output_path)
        print("\n  Written to {} ({} bytes)".format(
            output_path.name, output_path.stat().st_size))
    else:
        print("\n--- DRY RUN: no ALS changes ---")

    return plan


# _match_track and _normalise are now imported from apply_loops above
# (single source of truth — see commit "Consolidate _match_track").


# -- Report generation --------------------------------------------------------

def generate_report(plan: ArrangementPlan, output_path: Path) -> Path:
    """Write a JSON arrangement report — the single audit surface for every
    pipeline decision. Includes key, BPM, energy, harmonic compatibility,
    loop source, and style selection so runs are debuggable from JSON alone.
    """
    track_lookup = {t.name: t for t in plan.tracks}

    report = {
        "track_count": len(plan.tracks),
        "transition_count": len(plan.overlaps),
        "tracks": [
            {
                "name": t.name,
                "camelot": t.camelot,
                "bpm": t.bpm,
                "energy": t.energy,
                "intro_skip_bars": t.intro_skip_bars,
                "arr_start": t.arr_start,
                "arr_end": t.arr_end,
                "sections": len(t.sections),
            }
            for t in plan.tracks
        ],
        "shifts": [
            {"track": name, "delta_beats": delta, "delta_bars": delta / 4}
            for name, delta in plan.shifts
        ],
        "loops": [
            {
                "track": ls.track_name,
                "type": "tail" if "tail" in ls.clip_name else "intro",
                "source_beats": "{:.0f}-{:.0f}".format(
                    ls.source_beat_start, ls.source_beat_end),
                "count": ls.count,
                "total_beats": (ls.source_beat_end - ls.source_beat_start) * ls.count,
                "insert_at_beat": ls.insert_at_beat,
            }
            for ls in plan.loops
        ],
        "transitions": [],
    }

    for ov in plan.overlaps:
        out_t = track_lookup.get(ov.out_track)
        in_t = track_lookup.get(ov.in_track)

        harmonic_score = None
        harmonic_type = None
        if _compat_score and out_t and in_t and out_t.camelot and in_t.camelot:
            harmonic_score, harmonic_type = _compat_score(out_t.camelot, in_t.camelot)

        bpm_delta = None
        if out_t and in_t and out_t.bpm and in_t.bpm:
            bpm_delta = round(in_t.bpm - out_t.bpm, 1)

        loop_source = "none"
        if ov.out_tail_loop:
            loop_source = "outro"
        elif ov.in_intro_loop:
            loop_source = "intro"

        t = {
            "pair_index": ov.pair_index,
            "out_track": ov.out_track,
            "in_track": ov.in_track,
            "overlap_beats": ov.overlap_beats,
            "overlap_bars": ov.overlap_bars,
            "status": ov.status,
            "harmonic_score": harmonic_score,
            "harmonic_type": harmonic_type,
            "bpm_delta": bpm_delta,
            "loop_source": loop_source,
            "selected_style": (
                "quick_swap" if ov.overlap_bars < 24
                else "long_blend" if ov.overlap_bars > 36
                else "standard"),
            "notes": ov.notes,
        }
        if ov.out_tail_loop:
            t["out_tail_loop"] = {
                "source": "{:.0f}-{:.0f}".format(
                    ov.out_tail_loop.source_beat_start,
                    ov.out_tail_loop.source_beat_end),
                "count": ov.out_tail_loop.count,
            }
        if ov.in_intro_loop:
            t["in_intro_loop"] = {
                "source": "{:.0f}-{:.0f}".format(
                    ov.in_intro_loop.source_beat_start,
                    ov.in_intro_loop.source_beat_end),
                "count": ov.in_intro_loop.count,
            }
        if ov.similar_pairs:
            t["similar_history"] = [
                {
                    "pair_index": p.get("pair_index"),
                    "out": p.get("out_track", "")[:30],
                    "in": p.get("in_track", "")[:30],
                    "verdict": p.get("verdict"),
                }
                for p in ov.similar_pairs[:3]
            ]
        report["transitions"].append(t)

    if output_path.suffix == ".json":
        report_path = output_path
    else:
        report_path = output_path.with_suffix(".json").parent / (
            output_path.stem + "_ARRANGEMENT_REPORT.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\nArrangement report: {}".format(report_path))
    return report_path


# -- CLI ----------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Propose arrangement for a Sections .als")
    parser.add_argument("als", type=Path, help="Input Sections .als")
    parser.add_argument("sections_json", type=Path, help="Sections JSON")
    parser.add_argument("output", type=Path, help="Output .als path")
    parser.add_argument("--history", type=Path, default=None,
                        help="Path to pair_history.jsonl")
    parser.add_argument("--hints", type=Path, default=None,
                        help="Path to track_hints.json (for intro_skip_bars, etc.)")
    parser.add_argument("--report", type=Path, default=None,
                        help="Path for arrangement report JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan without modifying ALS")

    args = parser.parse_args()

    plan = propose_arrangement(
        als_path=args.als,
        sections_path=args.sections_json,
        output_path=args.output,
        history_path=args.history,
        hints_path=args.hints,
        dry_run=args.dry_run,
    )

    # Generate report
    report_path = args.report or args.output
    generate_report(plan, report_path)

    # Summary
    print("\n=== SUMMARY ===")
    print("  Tracks: {}".format(len(plan.tracks)))
    print("  Shifts: {}".format(len(plan.shifts)))
    print("  Loops:  {}".format(len(plan.loops)))
    ok = sum(1 for o in plan.overlaps if o.status == "ok")
    short = sum(1 for o in plan.overlaps if o.status == "short")
    none = sum(1 for o in plan.overlaps if o.status == "none")
    print("  Overlaps: {} ok, {} short (loops added), {} none".format(ok, short, none))


if __name__ == "__main__":
    main()
