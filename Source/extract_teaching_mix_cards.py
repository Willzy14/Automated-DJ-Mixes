"""Distil real transitions from finished mixes in Teaching Mixes/ into cards
Claude reads and reasons over directly - NOT a formula, NOT wired into
propose_arrangement.py or any automated decision.

Sam, 2026-09-16, drawing the line explicitly: "this is not for the bots...
this is for you as an AI looking for several different answers for the same
transition and distilling which one might work best." Burn list C7 Step 1
(same session) built exactly the formula version of "learn from past mixes"
and it lost to a trivial baseline - this is a deliberately different thing:
a case-study library for judgement, the same way Claude read Sam's own
hand-tweak write-ups before building D13's Claude-arranged decisions.

THE ROUTING DISCOVERY (Sam, 2026-09-16, confirmed against real files before
writing a line of extraction code): these are DJ-mixer-style sets. Individual
AudioTracks carry ONLY the arrangement (where each song's clips sit); they do
NOT carry the mix automation. Every track's AudioOutputRouting is "Sends
Only" - each track sends near-exclusively to ONE of a small number of Return
tracks (named "A-Zone ...", "B-Zone ..." etc. in the files seen so far),
alternating in ROUGHLY A/B/A/B order (real deviations exist - e.g. two edit
layers of the same song briefly sharing one zone). The actual mix move for a
transition - the fader ride, the filter/EQ sweep - lives as automation on
the RETURN track's own devices, not on the individual song tracks. Reading a
song track's own automation for "where was the bass cut" will always come up
empty; reading the CORRECT return track's automation in the transition's
time window is the only place that move actually exists in the file.

CONFIRMED, NOT ASSUMED, against a real file (Defected In The House - Ibiza
2026 CD2 Of 2): 18 tracks, sends alternate A/A gaps aside (tracks 7+8 both
briefly on Zone A, 9+10 both on Zone B - a real layered-edit exception, not
a bug), each Return track ("A-Zone 62 DJ EQ", "B-Zone 62 DJ EQ") carries a
real Volume automation envelope PLUS a MIDI-CC-mapped macro/filter
automation (Channel 0 vs 1, NoteOrController "20", Manual values on a
0-127 MIDI range - a physical hardware knob was played live and Ableton
captured the result; the underlying device/parameter it drives is not
positively identified yet, so it is reported as "possible filter/EQ move,
exact device unconfirmed" - never asserted as a specific bass-shelf value
this module cannot actually verify).

SCHEMA VARIES PER FILE, confirmed by direct inspection before writing this
module: Ableton versions from Live 9.1 through 12.3 across the 20 files in
Teaching Mixes/. Only 5 of 20 carry the zone-bus `<AutomationEnvelope>`
data described above (Mechanism 1). **CORRECTED (Claude subagent review,
2026-09-16, the same day this was first written): the other 15 do NOT lack
automation - an earlier version of this docstring claimed Gbox Side 1 and
Tapesh Mix "were performed with no automation written at all", which was
false.** 14 of those 15 carry real automation directly on individual song
tracks' own devices (Mixer Volume, FilterEQ3's `GainLo` bass gain,
AutoFilter's `Cutoff` frequency), via the OLDER per-parameter
`<ArrangerAutomation><Events><FloatEvent>` mechanism (Mechanism 2, built
same day, Sam's direction: "keep going - build the extraction now") -
`_track_direct_automation` reads it directly off each song track, no bus
involved. `Volume` and `GainLo` convert to real dB via the same curve as
Mechanism 1's channel fader; `Cutoff` is reported as its raw, unconverted
value (lower = more filtered - direction confirmed, exact Hz curve is
not). Only ONE file (Gbox Side 3) has real automation on none of these
three classified parameters (its curves are all Send/DryWet/Tempo - not
currently treated as a mix move). **19 of 20 files now have real, extracted
automation.** See `Documentation/Mix Patterns Library/Teaching Mixes
Cards/INDEX.md`'s Coverage section and Revision history for the full,
corrected-twice numbers.

Locators exist in some files (15 in the Defected file, evenly spread) but
their meaning is UNCONFIRMED - they may or may not be Sam's own transition
markers. A card reports any locator inside its transition window as a
"possible marker", never as a confirmed one.
"""

from __future__ import annotations

import gzip
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_sections_als import parse_sections_als  # noqa: E402 - proven clip reader


# ----------------------------------------------------------------------- #
# Low-level XML helpers                                                    #
# ----------------------------------------------------------------------- #

def _load(als_path: Path) -> str:
    with gzip.open(als_path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def _track_blocks(text: str, tag: str) -> list[tuple[str, str]]:
    """Split on an opening tag NAME only (e.g. "<AudioTrack ") - never on a
    fixed attribute list. Real files in this folder have extra attributes
    after Id (`SelectedToolPanel=...`) that break a naive `Id="\\d+">` match
    silently - confirmed directly, not assumed, by two failed attempts
    during discovery. Returns [(id_or_empty, body)]."""
    parts = re.split(f"<{tag} ", text)
    out = []
    for part in parts[1:]:
        end = part.find(f"</{tag}>")
        body = part[:end] if end >= 0 else part
        id_m = re.match(r'Id="(\d+)"', part)
        out.append((id_m.group(1) if id_m else "", body))
    return out


NOISE_TAGS = {
    "LomId", "Manual", "MidiControllerRange", "Min", "Max", "KeyMidi",
    "LowerRangeNote", "UpperRangeNote", "ControllerMapMode", "Channel",
    "NoteOrController", "PersistentKeyString", "IsNote", "AutomationTarget",
    "ModulationTarget", "LockEnvelope", "Mapping", "MpeSettings",
    "MpePitchBendUsesTuning", "TargetEnum",
}


def _nearest_real_tag(text: str, pos: int, window: int = 2000) -> str | None:
    """The nearest OPEN tag before `pos` that isn't structural noise -
    usually the actual parameter name (Volume, Pan, DryWet, ...)."""
    ctx = text[max(0, pos - window):pos]
    tags = [t for t in re.findall(r"<([A-Za-z][A-Za-z0-9_.]*)[ >]", ctx)
            if t not in NOISE_TAGS]
    return tags[-1] if tags else None


# ----------------------------------------------------------------------- #
# Data model                                                               #
# ----------------------------------------------------------------------- #

@dataclass
class AutomationPoint:
    time_beats: float
    value: float


@dataclass
class ZoneAutomation:
    zone_name: str
    volume_points: list[AutomationPoint] = field(default_factory=list)
    filter_points: list[AutomationPoint] = field(default_factory=list)
    filter_label: str = ""          # the resolved parameter name, e.g. "MacroControls.0"
    filter_confirmed: bool = False  # True only if the underlying device parameter (not
                                    # just a macro number) is positively identified


@dataclass
class TrackDirectAutomation:
    """Automation on a SONG TRACK's own devices, via the older
    `<ArrangerAutomation><Events>` per-parameter mechanism (round 2 finding,
    2026-09-16, Claude subagent review: an earlier version of this module
    claimed 15 of 20 real files "were mixed live, nothing written" - false.
    Those files' real automation lives directly on individual song tracks'
    own Mixer Volume and FilterEQ3/AutoFilter devices, not on any bus -
    confirmed directly, 492 real curves / 26,822 points across those 15
    files. This is MORE precise than the zone-bus macro case: `GainLo` is
    FilterEQ3's actual bass/low-shelf gain parameter and `Cutoff` is
    AutoFilter's actual cutoff frequency - both positively identified
    device parameters, not an unresolved macro.

    GainLo's dB curve, CONFIRMED not just inferred (round 3, 2026-09-16):
    MiniMax's review flagged the `_value_to_db` conversion for `GainLo` as
    plausible-but-unverified against Ableton's own documentation - fair, at
    the time. A Claude subagent then queried Ableton's Live manual directly:
    EQ Three's (FilterEQ3's) documented gain range per band is **-infinite
    dB to +6dB** - NOT the +-15dB figure a broader, less targeted web search
    had first turned up (that figure belongs to the separate Channel EQ
    device). The real file's own `MidiControllerRange Max="1.99526238"`
    converts via `20*log10(v)` to **+6.02dB**, matching Ableton's documented
    +6dB max almost exactly. That is real, independent confirmation the
    curve is correct, not a coincidence of plausible-looking numbers. The
    Min side (a practical -70dB floor in the file, `0.0003162277571`) is
    consistent with the documented "-infinite dB" as a bounded parameter's
    practical floor. `GainLo`'s dB values can be read with the same
    confidence as the zone-bus Volume fader's. `Cutoff` still has no
    equivalent documented-range confirmation and remains reported as raw,
    unconverted units for that reason."""
    volume_points: list[AutomationPoint] = field(default_factory=list)
    bass_gain_points: list[AutomationPoint] = field(default_factory=list)   # FilterEQ3 GainLo
    cutoff_points: list[AutomationPoint] = field(default_factory=list)      # AutoFilter Cutoff


@dataclass
class TrackInfo:
    order: int
    name: str
    zone: str | None            # None = no dominant send found (e.g. a stray reference track)
    clips: list[dict]           # from parse_sections_als, per-track clip list
    arr_start: float | None
    arr_end: float | None
    direct_automation: TrackDirectAutomation | None = None


@dataclass
class Locator:
    time_beats: float
    name: str


@dataclass
class MixExtraction:
    als_path: Path
    ableton_version: str
    tracks: list[TrackInfo]
    zones: dict[str, ZoneAutomation]
    locators: list[Locator]
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------- #
# Extraction                                                               #
# ----------------------------------------------------------------------- #

def _ableton_creator(text: str) -> str:
    """The document's Creator string (e.g. "Ableton Live 12.3.2") - NOT a
    parsed MajorVersion/MinorVersion pair (MiniMax review, 2026-09-16: the
    original name `_ableton_version` claimed more than it returned)."""
    m = re.search(r'<Ableton MajorVersion="(\d+)" MinorVersion="([^"]*)"[^>]*Creator="([^"]*)"', text)
    return m.group(3) if m else "unknown"


def _zone_names(text: str) -> dict[str, str]:
    """Return track pointer order -> zone display name, keyed by the
    ReturnTrack's own send-slot INDEX (0-based, matching each AudioTrack's
    own `<TrackSendHolder Id="N">` numbering) - NOT the ReturnTrack's own
    `Id="43"`-style attribute, which is an unrelated internal id."""
    zones: dict[str, str] = {}
    for idx, (_rid, body) in enumerate(_track_blocks(text, "ReturnTrack")):
        name_m = re.search(r'<EffectiveName Value="([^"]*)"', body)
        zones[str(idx)] = name_m.group(1) if name_m else f"Return {idx}"
    return zones


def _track_zone(body: str, zone_names: dict[str, str]) -> str | None:
    """Whichever send this track has closest to full (>0.5) is its zone.
    A track with no send above that (e.g. sends-only-to-reverb, or the
    stray whole-mix reference track seen in one real file) returns None -
    reported honestly, never guessed.

    Zone is resolved by POSITION (the Nth <TrackSendHolder> encountered in
    this track, matched to the Nth ReturnTrack in file order) - NOT by the
    TrackSendHolder's own `Id="N"` attribute value. Found in independent
    review (Claude subagent, 2026-09-16), confirmed directly against a
    real file: `Id` is an internal object id, not a stable 0-based slot -
    in the real Defected CD2/CD3 files, tracks 1-2 carry Ids "2,3,4" while
    every later track carries "0,1,2" for the exact same three sends. Using
    the raw Id as a zone-name lookup key mis-resolved both files' first two
    transitions (track 1 read as the reverb return, track 2 as unresolved)."""
    sends = re.findall(r'<TrackSendHolder Id="\d+">.*?<Manual Value="([\d.]+)"',
                       body, re.S)
    best_idx, best_val = None, 0.0
    for position, val in enumerate(sends):
        v = float(val)
        if v > best_val:
            best_val, best_idx = v, str(position)
    if best_idx is None or best_val < 0.5:
        return None
    return zone_names.get(best_idx)


def _track_direct_automation(body: str) -> TrackDirectAutomation:
    """Real automation drawn directly on THIS track's own devices, via the
    older `<ArrangerAutomation><Events>` mechanism (round 2 finding,
    2026-09-16) - the actual mix-move data source for the 15 real files
    that carry no zone-bus automation. Only blocks with MORE THAN ONE
    FloatEvent are real curves; a single-point block is a static "set
    once" value (e.g. a device On/Off toggle), not automation - matches
    the same discipline `_zone_automation` uses (an envelope with no
    points is skipped, never treated as "automation exists but is empty").
    `<FloatEvent>` here carries no `Id=` attribute (unlike the
    AutomationEnvelope mechanism's FloatEvents) - a genuinely different XML
    shape for the same underlying concept, confirmed by direct inspection."""
    ta = TrackDirectAutomation()
    for m in re.finditer(r'<ArrangerAutomation>\s*<Events>(.*?)</Events>\s*</ArrangerAutomation>',
                         body, re.S):
        events_xml = m.group(1)
        if events_xml.count("<FloatEvent") <= 1:
            continue
        points = [
            AutomationPoint(time_beats=float(t), value=float(v))
            for t, v in re.findall(r'<FloatEvent Time="(-?[\d.]+)" Value="(-?[\d.]+)"', events_xml)
        ]
        if len(points) <= 1:
            continue
        label = _nearest_real_tag(body, m.start())
        if label == "Volume":
            ta.volume_points.extend(points)
        elif label == "GainLo":
            ta.bass_gain_points.extend(points)
        elif label == "Cutoff":
            ta.cutoff_points.extend(points)
        # Everything else (DryWet, Send, Tempo, MixDirect, On, Pan, ...) is
        # real automation too but not a fader/bass/filter mix move - out of
        # scope for a transition card, same as _zone_automation's ignore list.
    return ta


def _zone_automation(text: str, zone_names: dict[str, str]) -> dict[str, ZoneAutomation]:
    result: dict[str, ZoneAutomation] = {}
    for idx_str, (_rid, body) in zip(zone_names, _track_blocks(text, "ReturnTrack")):
        zname = zone_names[idx_str]
        za = ZoneAutomation(zone_name=zname)
        for env_m in re.finditer(
            r'<AutomationEnvelope Id="\d+">\s*<EnvelopeTarget>\s*'
            r'<PointeeId Value="(-?\d+)" />.*?<Events>(.*?)</Events>',
            body, re.S,
        ):
            pointee, events_xml = env_m.groups()
            points = [
                AutomationPoint(time_beats=float(t), value=float(v))
                for t, v in re.findall(
                    r'<FloatEvent Id="\d+" Time="(-?[\d.]+)" Value="(-?[\d.]+)"',
                    events_xml)
            ]
            if not points:
                continue
            tgt_m = re.search(rf'<AutomationTarget Id="{pointee}">', body)
            label = _nearest_real_tag(body, tgt_m.start()) if tgt_m else None
            if label == "Volume":
                za.volume_points.extend(points)
            elif label and label.startswith("MacroControls."):
                # Confirmed against a real file (discovery, 2026-09-16): this
                # is a macro knob on the zone's own rack ("62 DJ EQ" = two
                # EQ Eight devices + a Limiter, chained), MIDI-CC-mapped to a
                # hardware controller (real DJ performance, not a programmed
                # curve). WHICH underlying EQ band(s)/parameter(s) that macro
                # maps to is NOT resolved by this pass - the macro number
                # itself is real and precise, its downstream target is not.
                za.filter_points.extend(points)
                za.filter_label = label
                za.filter_confirmed = False
            elif label in ("Send", "On", "Speaker", "Pan", "SplitStereoPanL",
                          "SplitStereoPanR", "CrossFadeState", "Panorama",
                          "ChainSelector", None):
                pass  # routing/panning/on-off noise, not a mix move
            else:
                za.filter_points.extend(points)
                za.filter_label = label
                za.filter_confirmed = True
        za.volume_points.sort(key=lambda p: p.time_beats)
        za.filter_points.sort(key=lambda p: p.time_beats)
        result[zname] = za
    return result


def _locators(text: str) -> list[Locator]:
    out = []
    for m in re.finditer(
        r'<Locator Id="\d+">\s*<LomId Value="0" />\s*<Time Value="([\d.]+)" />\s*'
        r'<Name Value="([^"]*)"', text):
        out.append(Locator(time_beats=float(m.group(1)), name=m.group(2)))
    return out


def extract(als_path: Path) -> MixExtraction:
    text = _load(als_path)
    warnings: list[str] = []

    zone_names = _zone_names(text)
    if not zone_names:
        warnings.append("no Return tracks found - this file may not use the "
                        "zone-bus pattern; per-track automation not checked "
                        "by this module (out of scope, see module docstring)")

    clip_data = parse_sections_als(als_path)  # {track_name: [clip, ...]}

    # Track order + zone, in file order (matches mix running order for a
    # DJ set built track-by-track in the arrangement).
    tracks: list[TrackInfo] = []
    for order, (_tid, body) in enumerate(_track_blocks(text, "AudioTrack"), start=1):
        name_m = re.search(r'<EffectiveName Value="([^"]*)"', body)
        name = name_m.group(1) if name_m else f"(unnamed track {order})"
        zone = _track_zone(body, zone_names) if zone_names else None
        clips = clip_data.get(name, [])
        arr_start = min((c["arr_bars"] for c in clips), default=None)
        arr_end = max((c["arr_end_bars"] for c in clips), default=None)
        direct = _track_direct_automation(body)
        tracks.append(TrackInfo(order=order, name=name, zone=zone,
                                clips=clips, arr_start=arr_start, arr_end=arr_end,
                                direct_automation=direct))

    zones = _zone_automation(text, zone_names) if zone_names else {}
    locators = _locators(text)

    return MixExtraction(als_path=als_path, ableton_version=_ableton_creator(text),
                         tracks=tracks, zones=zones, locators=locators,
                         warnings=warnings)


# ----------------------------------------------------------------------- #
# Transition cards                                                         #
# ----------------------------------------------------------------------- #

def find_transitions(mix: MixExtraction) -> list[tuple[TrackInfo, TrackInfo]]:
    """Adjacent REAL tracks (arr_start/arr_end both known) whose arrangement
    windows genuinely overlap - a stray reference track (no clips resolved,
    or a window spanning the whole mix while every neighbour's does not)
    produces no transitions, never a fabricated one."""
    real = [t for t in mix.tracks if t.arr_start is not None and t.arr_end is not None]
    # Drop a track whose own span already contains every other real track's
    # span entirely - the whole-mix reference-import pattern found in
    # discovery (18-Defected...(CD 1 Of 2), one clip spanning bar 0-2000).
    if len(real) > 2:
        spans = [(t.arr_end - t.arr_start) for t in real]
        median_span = sorted(spans)[len(spans) // 2]
        real = [t for t, s in zip(real, spans) if s < median_span * 8]
    pairs = []
    for a, b in zip(real, real[1:]):
        if b.arr_start < a.arr_end:  # genuine overlap
            pairs.append((a, b))
    return pairs


def _points_in_window(points: list[AutomationPoint], start: float, end: float,
                      pad_beats: float = 32.0) -> list[AutomationPoint]:
    return [p for p in points if start - pad_beats <= p.time_beats <= end + pad_beats]


def _value_to_db(v: float) -> float:
    """Ableton's own linear-to-dB mixer curve (inverse of
    automated_dj_mixes.als_generator._db_to_ableton_volume: value=10**(db/20)).
    Floored at -70dB rather than -inf for a value of exactly 0 - matches the
    fader-minimum convention already seen throughout these files
    (0.0003162277571 = 10**(-70/20), the recurring "off" send value)."""
    if v <= 0.0003162277571:
        return -70.0
    return 20.0 * math.log10(v)


def _summarize_curve(points: list[AutomationPoint], to_display, unit: str,
                     snap_threshold: float, snap_window_bars: float = 1.0,
                     excursion_threshold: float = 8.0) -> str:
    """Collapse a raw automation curve into a short plain-English shape
    description: holds, gradual ramps, hard snaps (a value change bigger
    than `snap_threshold` inside `snap_window_bars`), AND any excursion
    (a min or max well past both the start and end value - a dip-and-
    recover or a spike-and-return, which a naive start-vs-end comparison
    misses entirely: found in review against real data, 2026-09-16 - a
    filter that swept 64->32->64 read as "flat" before this fix, because
    its start and end values happened to match). Never claims more
    precision than the data has - reports exactly the points it saw."""
    if not points:
        return "no automation in this window"
    pts = sorted(points, key=lambda p: p.time_beats)
    disp = [(p.time_beats / 4, to_display(p.value)) for p in pts]

    events: list[str] = [f"starts {disp[0][1]}{unit} @ bar {disp[0][0]:.1f}"]
    for (t0, v0), (t1, v1) in zip(disp, disp[1:]):
        if (t1 - t0) <= snap_window_bars and abs(v1 - v0) >= snap_threshold:
            events.append(f"SNAP to {v1}{unit} @ bar {t1:.1f}")

    start_val, end_val = disp[0][1], disp[-1][1]
    min_bar, min_val = min(disp, key=lambda p: p[1])
    max_bar, max_val = max(disp, key=lambda p: p[1])
    bracket = max(start_val, end_val)
    floor = min(start_val, end_val)
    if min_val < floor - excursion_threshold:
        events.append(f"dips to {min_val}{unit} @ bar {min_bar:.1f} before recovering")
    if max_val > bracket + excursion_threshold:
        events.append(f"spikes to {max_val}{unit} @ bar {max_bar:.1f} before returning")

    if len(disp) > 1 and not any(w in events[-1] for w in ("SNAP", "dips", "spikes")):
        if end_val == start_val:
            # MiniMax review, 2026-09-16: "gradual flat" is a contradiction
            # (something "gradual" implies change) - holds is the honest word.
            events.append(f"holds at {end_val}{unit} @ bar {disp[-1][0]:.1f}")
        else:
            direction = "up" if end_val > start_val else "down"
            events.append(f"gradual {direction} to {end_val}{unit} @ bar {disp[-1][0]:.1f}")
    elif f"@ bar {disp[-1][0]:.1f}" not in events[-1]:
        events.append(f"ends {end_val}{unit} @ bar {disp[-1][0]:.1f}")
    return "; then ".join(events)


def build_card(mix: MixExtraction, out_t: TrackInfo, in_t: TrackInfo) -> str:
    overlap_start, overlap_end = in_t.arr_start, out_t.arr_end
    overlap_bars = (overlap_end - overlap_start)

    lines = [
        f"## {out_t.name} -> {in_t.name}",
        f"- Source: `{mix.als_path.name}` ({mix.ableton_version})",
        f"- Outgoing zone: {out_t.zone or 'UNRESOLVED'}  |  Incoming zone: {in_t.zone or 'UNRESOLVED'}",
        f"- Overlap: bar {overlap_start:.1f} to {overlap_end:.1f} ({overlap_bars:.1f} bars)",
    ]

    if out_t.zone is not None and out_t.zone == in_t.zone:
        lines.append("- **Same zone on both sides** - no zone crossfade expected here; "
                     "likely a direct edit/layer within one channel, not a fader move.")

    for label, t, zone_name in (("Outgoing", out_t, out_t.zone), ("Incoming", in_t, in_t.zone)):
        za = mix.zones.get(zone_name) if zone_name else None
        if za is None:
            lines.append(f"- {label} zone automation: not resolved "
                         f"({'no zone assigned' if not zone_name else 'zone has no automation'})")
            continue
        vol_pts = _points_in_window(za.volume_points, overlap_start * 4, overlap_end * 4)
        if vol_pts:
            shape = _summarize_curve(vol_pts, lambda v: round(_value_to_db(v), 1), "dB",
                                     snap_threshold=6.0)
            lines.append(f"- {label} zone ({zone_name}) channel fader: {shape}")
        else:
            lines.append(f"- {label} zone ({zone_name}) channel fader: no automation point in this window")
        filt_pts = _points_in_window(za.filter_points, overlap_start * 4, overlap_end * 4)
        if filt_pts:
            if za.filter_confirmed:
                note = f"device parameter '{za.filter_label}'"
            else:
                note = (f"macro '{za.filter_label}' on this zone's rack - a live-played "
                        f"knob (MIDI-mapped), its underlying EQ/filter target not resolved")
            shape = _summarize_curve(filt_pts, lambda v: round(v, 1), "/64", snap_threshold=10.0)
            lines.append(f"- {label} zone ({zone_name}) filter/EQ knob ({note}): {shape}")

    # Burn list C10 round 2 (2026-09-16): the OTHER real automation source -
    # a track's own devices, via the older per-clip ArrangerAutomation
    # mechanism (see TrackDirectAutomation's docstring). Checked for BOTH
    # sides regardless of zone-automation availability - a track could in
    # principle carry both, and this is genuinely more precise data where
    # it exists (GainLo/Cutoff are positively identified device parameters,
    # not an unresolved macro).
    for label, t in (("Outgoing", out_t), ("Incoming", in_t)):
        da = t.direct_automation
        if da is None:
            continue
        vol_pts = _points_in_window(da.volume_points, overlap_start * 4, overlap_end * 4)
        if vol_pts:
            shape = _summarize_curve(vol_pts, lambda v: round(_value_to_db(v), 1), "dB",
                                     snap_threshold=6.0)
            lines.append(f"- {label} track's own channel fader (direct automation): {shape}")
        bass_pts = _points_in_window(da.bass_gain_points, overlap_start * 4, overlap_end * 4)
        if bass_pts:
            shape = _summarize_curve(bass_pts, lambda v: round(_value_to_db(v), 1), "dB",
                                     snap_threshold=6.0)
            lines.append(f"- {label} track's own bass EQ (FilterEQ3 GainLo, direct automation): {shape}")
        cutoff_pts = _points_in_window(da.cutoff_points, overlap_start * 4, overlap_end * 4)
        if cutoff_pts:
            shape = _summarize_curve(cutoff_pts, lambda v: round(v, 1), " (raw, not Hz-converted)",
                                     snap_threshold=15.0)
            lines.append(f"- {label} track's own filter cutoff (AutoFilter, direct automation, "
                         f"lower=more filtered): {shape}")

    near_locators = [l for l in mix.locators
                     if overlap_start - 8 <= l.time_beats / 4 <= overlap_end + 8]
    if near_locators:
        lines.append("- Possible marker(s) nearby (meaning unconfirmed): " +
                     ", ".join(f"{l.name}@{l.time_beats/4:.1f}b" for l in near_locators))

    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("als_path", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    mix = extract(args.als_path)
    for w in mix.warnings:
        print(f"WARNING: {w}")
    print(f"{args.als_path.name}: {mix.ableton_version}, {len(mix.tracks)} tracks, "
         f"{len(mix.zones)} zone(s), {len(mix.locators)} locator(s)")

    transitions = find_transitions(mix)
    print(f"{len(transitions)} transition(s) found\n")

    cards = [build_card(mix, a, b) for a, b in transitions]
    output = "\n\n".join(cards)
    print(output)

    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
