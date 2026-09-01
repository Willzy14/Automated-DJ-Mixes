"""Size the OUTGOING track's residual bass across a swap, feed-forward.

WHY THIS EXISTS. When the bass switches from the outgoing track to the incoming
one, the mix can lose energy - usually because the incoming's kick and bass are
simply less powerful than the outgoing's. Sam fixes that one of two ways: turn
the incoming up through the transition and automate it back down, or leave a
little of the OUTGOING's bass in so the mix does not feel hollow. He chose the
second, scaled to the shortfall. This module works out how much to leave.

FEED-FORWARD, AND WHY THAT IS THE ONLY SAFE FORM. The obvious design - bounce
the mix, measure the hole, fix it, re-bounce - was reviewed independently by
both peer brains and both returned fatal. The killer is that the render gate
measures the SUMMED bounce and cannot infer cause: at one V16 swap, boosting
the incoming recovered 1.84 dB of a 15 dB hole while still clearing the gate,
because K-weighting de-emphasises the very band that was missing. A loop built
on that optimises its own measurement rather than the music. Measuring the TWO
ISOLATED SOURCE TRACKS instead removes the problem by construction: there is no
bounce in the loop, so there is nothing to game and no identity chain to get
wrong.

WHAT IS ACTUALLY MEASURED. Not "outgoing minus incoming" - that is a property
of two records, not of the transition. What the listener hears across a swap is

    before:  outgoing at full bass  +  incoming bass-killed
    after:   outgoing bass-killed   +  incoming at full bass

so the hole is the drop from the first to the second, per band, and that is
what gets sized. Both are predicted by `mix_predict` from source audio, at the
same arrangement instant, through each clip's own warp markers.

THE CALIBRATION OFFSET CANCELS. `mix_predict`'s ~2.76 dB sub offset has no known
mechanism, but it is a FIXED per-band constant subtracted from every prediction
in that band, and the shortfall is a DIFFERENCE of two predictions in the same
band - so the constant cancels exactly and never reaches the residual.

THE GATE, HOWEVER, IS NOT YET CERTIFIED FOR THIS QUANTITY, and that is the
honest blocker on the whole feature (Codex FATAL 2, 2026-09-01). `BAND_P95_DB`
was measured on predictions of the SUMMED bounce. What is gated here is a
difference of two per-track SHARES, which is a different estimator: two
component errors can cancel in a sum while ADDING in a difference, so a band
whose summed prediction is good to 1.09 dB may have a share-difference error
appreciably worse - conceivably wrong-sign. `can_size_correction` therefore
does not currently certify this decision; it is being used as the best
available proxy, with the margin widened to acknowledge the gap.

Clearing it needs held-out calibration of share-differences specifically -
solo renders of individual tracks from a second mix, compared against their
predicted shares. Until that exists this feature stays default OFF, and the
numbers it prints are a ranking aid rather than a certified size.

WHAT THIS DOES NOT KNOW. It measures energy, not music. Two basslines in
different keys sounding together, or comb filtering between them, is a defect
this cannot see - the model sums powers and discards the cross-terms entirely,
so kick-transient cancellation or reinforcement is invisible to it. That is why
the residual is capped at a level Sam has already shipped and listened to
rather than at whatever the arithmetic wants, and why the last gate is his ears.

SAM'S TWO RULINGS, 2026-09-01 - both settled, do not re-litigate:

  1. HOW MUCH BASS. "Meet it halfway." Size to the shortfall but cap at
     EQ_BASS_PARTIAL, which recovers roughly half a typical hole. The
     alternative on the table was to match the level across the swap, which on
     his material means barely cutting the outgoing at all (about -1 to -2 dB
     rather than -6) - MiniMax argued that is the truer reading of "don't let
     it feel hollow". He chose the conservative one: every value this writes is
     a value he has already heard in a shipped mix.

  2. THE CROSS-BAND TRADE. "Refuse - don't trade one problem for another."
     When filling a sub hole would push 60-150 Hz above where it sat before the
     swap, refuse the pair outright and keep today's full kill. This is Codex's
     position over MiniMax's, and it is why the guard below is absolute rather
     than marginal. He accepted the cost knowingly: on the 14.08.26 V16 mix it
     means the feature fires on NONE of the 15 transitions, because every
     candidate there is a sign-disagreement pair. A no-op on that mix is the
     correct answer, not a bug to be tuned away - the 100 Hz shelf is simply
     the wrong actuator when the two bands disagree.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mix_predict as mp

# Measurement window, matching mix_predict's own default so the numbers here
# are comparable with everything else the model reports.
WINDOW_SEC = 3.0

# Bands the low shelf can actually move. The shelf sits at 100 Hz, so it owns
# sub outright and most of bass; above lowmid it does effectively nothing and a
# residual sized from those bands would be sized from noise.
DRIVING_BANDS = ("sub", "bass")

# The residual ladder, in ChannelEq LowShelfGain (a LINEAR AMPLITUDE RATIO).
# The floor is the full kill the pipeline writes today; the ceiling is
# EQ_BASS_PARTIAL, the -5.68 dB partial cut that is already in production and
# that Sam has heard. Deliberately NOT higher: the arithmetic would happily ask
# for two full basslines at once, which is the exact mud this technique exists
# to avoid, and no one has ever listened to that.
RESIDUAL_FLOOR = 0.18      # EQ_BASS_KILL
RESIDUAL_CEILING = 0.52    # EQ_BASS_PARTIAL
RESIDUAL_STEPS = 24

# A residual may not push a band ABOVE where it sat before the swap by more
# than this many times the band's own p95 error. Without it, closing a sub hole
# is free to build a bass bulge that nothing else in the pipeline would catch.
OVERSHOOT_MARGIN = 1.0

# Extra margin on the FIRING gate, over and above mix_predict's own 2x. The p95
# it uses was measured on summed-bounce predictions, and what is gated here is a
# difference of two per-track shares - an estimator whose error can be larger,
# because two component errors that cancel in a sum can add in a difference. sqrt(2)
# is the right factor if the two share errors are independent; they are almost
# certainly correlated (same model, same mix), so this is conservative rather
# than derived, and it is a stand-in for the held-out calibration that would
# actually settle it. See the module docstring.
SHARE_DIFFERENCE_MARGIN = 1.414

# How many beat probes to spread across the residual's active window. A
# residual safe at the swap can still bulge once the incoming's own bass
# arrives, so the guard is evaluated across the whole taper, not at its start.
LATER_PROBES = 5




@dataclass(frozen=True)
class ResidualDecision:
    """What to do at one swap, and why.

    A decision is returned for EVERY pair, including the refusals. Silence was
    the wrong answer here: on a real mix most pairs will not qualify, and a
    build log that simply omits them cannot be told apart from one where the
    sizing never ran. `fired` is the only thing a caller should branch on.
    """
    fired: bool
    reason: str
    gain: float | None = None           # ChannelEq LowShelfGain to hold at the swap
    band: str | None = None             # the band whose shortfall drove the sizing
    shortfall_db: float | None = None   # the measured pre -> post hole in that band
    recovered_db: float | None = None   # how much of it this residual recovers

    @property
    def gain_db(self) -> float | None:
        if self.gain is None:
            return None
        return 20.0 * math.log10(max(self.gain, 1e-6))


def _worth_sizing(band: str, db: float) -> bool:
    """mix_predict's own sizing gate, widened for the share-difference gap."""
    return mp.can_size_correction(band, db / SHARE_DIFFERENCE_MARGIN)


def _db(power: float) -> float:
    return 10.0 * math.log10(max(power, 1e-24))


def _track_band_power(model, track, beat: float, shelf: float,
                      cache: dict) -> dict | None:
    """Per-band power this one track contributes at `beat`, with `shelf` on its
    low shelf. None when the track is not sounding there, or its source audio
    cannot be read - both of which must fail closed, not guess."""
    clip = track.clip_at(beat)
    if clip is None:
        return None
    gain = track.gain_at(beat)
    if gain <= 1e-6:
        return None
    src = clip.source_sec(beat)
    if src is None:
        return None
    bp = mp._source_band_power(clip, src, WINDOW_SEC, shelf, cache,
                               tuple(track.filters))
    if bp is None:
        return None
    scale = (model.master * track.mixer_trim * gain) ** 2
    return {name: value * scale for name, value in bp.items()}


def size_residual(model, out_track, in_track, swap_beat: float,
                  taper_end_beat: float, cache: dict | None = None,
                  ceiling: float = RESIDUAL_CEILING,
                  out_trim_db: float = 0.0,
                  in_trim_db: float = 0.0) -> ResidualDecision:
    """Size the outgoing's residual bass across one swap.

    Returns a decision, always. `fired=False` means "leave today's full kill
    exactly as it is" and carries the reason why.

    `out_trim_db` / `in_trim_db` are the LUFS levelling offsets the mix will
    actually ship with. They are not optional in practice: the arranged set this
    model is loaded from still has every fader at 0 dB, and levelling later
    moves them by up to ~3 dB, which is more than some of the holes being sized.
    """
    cache = {} if cache is None else cache
    out_scale = 10.0 ** (out_trim_db / 10.0)     # dB -> POWER ratio
    in_scale = 10.0 ** (in_trim_db / 10.0)

    def out_at(shelf, beat=None):
        p = _track_band_power(model, out_track, swap_beat if beat is None else beat,
                              shelf, cache)
        return None if p is None else {b: v * out_scale for b, v in p.items()}

    def in_at(shelf, beat=None):
        p = _track_band_power(model, in_track, swap_beat if beat is None else beat,
                              shelf, cache)
        return None if p is None else {b: v * in_scale for b, v in p.items()}

    unity = 1.0   # EQ_BASS_UNITY
    out_unity, out_kill = out_at(unity), out_at(RESIDUAL_FLOOR)
    in_unity, in_kill = in_at(unity), in_at(RESIDUAL_FLOOR)
    if None in (out_unity, out_kill, in_unity, in_kill):
        return ResidualDecision(False, "source audio unreadable, or a track is "
                                       "not sounding at the swap")

    # What the swap does today, per band. The honest quantity is not "outgoing
    # minus incoming" - that is a property of two records - but the change the
    # listener hears across the swap.
    pre = {b: out_unity[b] + in_kill[b] for b in out_unity}
    post = {b: out_kill[b] + in_unity[b] for b in out_unity}
    shortfall = {b: _db(pre[b]) - _db(post[b]) for b in DRIVING_BANDS}

    candidates = [b for b in DRIVING_BANDS
                  if shortfall[b] > 0 and _worth_sizing(b, shortfall[b])]
    if not candidates:
        detail = ", ".join(f"{b} {shortfall[b]:+.1f} dB" for b in DRIVING_BANDS)
        return ResidualDecision(False, f"no hole worth sizing ({detail})")
    band = max(candidates, key=lambda b: shortfall[b])
    target = shortfall[band]

    # Probe beats across the residual's whole active life, not just its start.
    # A residual that is safe at the swap can still bulge two bars later when
    # the incoming's own bass arrives (Codex FATAL 4). The outgoing's Utility
    # Gain fade and the residual's own taper are deliberately NOT modelled here:
    # both only ever REDUCE the outgoing's contribution, so holding it at its
    # swap value over-states it, and over-stating the thing being guarded
    # against is the safe direction to be wrong in.
    span = max(0.0, taper_end_beat - swap_beat)
    probes = [swap_beat + span * i / (LATER_PROBES - 1) for i in range(LATER_PROBES)] \
        if span > 0 else [swap_beat]

    step = (ceiling - RESIDUAL_FLOOR) / RESIDUAL_STEPS
    best: ResidualDecision | None = None
    blocked: str | None = None
    for i in range(1, RESIDUAL_STEPS + 1):
        gain = RESIDUAL_FLOOR + step * i

        # Refuse when the RESULT sits clear of the pre-swap reference, at ANY
        # probe: filling a sub hole must not be allowed to build a bass bulge.
        #
        # The bound is deliberately ABSOLUTE - candidate result against the
        # pre-swap level - not "the residual adds less than p95 to wherever the
        # band already is". The marginal form was tried and reverted: it permits
        # another dB on top of an existing 4 dB excess, and on V16 it took the
        # feature from firing on one pair to three purely by being more
        # permissive. A guard relaxed until the feature fires is not a guard
        # (Codex, 2026-09-01).
        tripped: list[tuple[str, float]] = []
        for beat in probes:
            o_r, i_u = out_at(gain, beat), in_at(unity, beat)
            if o_r is None or i_u is None:
                continue          # the pair has stopped sounding; nothing to guard
            for b in DRIVING_BANDS:
                excess = _db(o_r[b] + i_u[b]) - _db(pre[b])
                if excess > OVERSHOOT_MARGIN * mp.band_uncertainty_db(b):
                    tripped.append((b, excess))
        if tripped:
            b, d = max(tripped, key=lambda x: x[1])
            blocked = (f"{b} would reach {d:+.1f} dB above its pre-swap level "
                       f"somewhere in the transition")
            break

        out_r = out_at(gain)
        if out_r is None:
            return ResidualDecision(False, "outgoing source unreadable at the "
                                           "residual shelf")
        recovered = _db(out_r[band] + in_unity[band]) - _db(post[band])
        made = ResidualDecision(
            True,
            f"{band} drops {target:.1f} dB across the swap; holding the "
            f"outgoing shelf at {20 * math.log10(gain):.1f} dB recovers "
            f"{recovered:.1f} dB",
            round(gain, 4), band, round(target, 2), round(recovered, 2))
        if recovered >= target:
            return made
        best = made

    # The ladder ran out, or the guard stopped it, before the hole closed.
    # Partial help is still help - but only when the recovery is itself bigger
    # than the model's error, otherwise this writes an envelope for a move it
    # cannot stand behind.
    if best is not None and _worth_sizing(band, best.recovered_db):
        return best
    got = f"{best.recovered_db:.1f}" if best is not None else "0.0"
    why = blocked or "the residual ceiling was reached"
    return ResidualDecision(
        False,
        f"{band} drops {target:.1f} dB but only {got} dB is recoverable ({why})")
