"""Write phrase-blend decisions from a project's groove analysis and cached envelopes."""
import argparse
import html
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from align_engine import _decision_bar
from apply_loops import staged_file
from measure_section_content import bar_content, read_envelope
from phrase_blend import ALT_MARGIN, _rank_key, candidates, plan_blend
from propose_arrangement import ordered_tracks, reject_aliased_output, reject_empty_layout_tracks
from section_format import groove_fills


FRACTIONAL_BAR = "is not a whole bar"      # the words align_engine._decision_bar refuses a bar with
# Only SUSTAINED vocals count towards the planner's vocal clash. Sam, 2026-11-24: "if the vocals are short
# stabs, that will probably be fine." The detector's vocal_regions already hold many 2 bar regions (its
# MIN_VOCAL_BARS), which are those stabs: Get Right [2, 2, 2, 2, 5], VLAD Interlude [2, 8, 2, 2, 10, 2, 2],
# VLAD POP [20, 2, 18, 2, 9, 2, 4, 3]. Sustained vocals are 4 bars and up. A region shorter than this sets
# no bar of has_vocal.
MIN_CLASH_VOCAL_BARS = 4
MAX_OPTIONS_TRIED = 200                    # plans tried per pair before the search gives up
SKIPPED_SHOWN = 5                          # skipped or refused plans listed per pair; the rest are counted


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def convert_track(result, envelope_path):
    """Use the detector's bar clock for both the phrase points and the envelopes."""
    fills = groove_fills(result, "plan_mix_decisions", str(envelope_path))
    bpm, n_bars = result["bpm"], result["n_bars"]
    if not (math.isfinite(bpm) and bpm > 0 and math.isfinite(n_bars) and n_bars > 0):
        raise ValueError(f"{envelope_path}: invalid bpm or n_bars")
    sections = result["sections"]
    if not sections:
        raise ValueError(f"{envelope_path}: empty detector sections")
    first = sections[0]
    lead = result.get("lead_in_bars", result["analysis"].get("lead_in_bars"))
    if lead is None:
        downbeat = result["analysis"].get("downbeat_sec")
        lead = (downbeat * bpm / 240 if downbeat is not None
                else first["start_sec"] * bpm / 240 - first["start_bar"])
    if not math.isfinite(lead):
        raise ValueError(f"{envelope_path}: invalid lead_in_bars")
    env, hop = read_envelope(envelope_path)
    if not math.isfinite(hop) or hop <= 0:
        raise ValueError(f"{envelope_path}: invalid envelope hop_t")
    if len({len(values) for values in env.values()}) != 1 or not len(env["drums"]):
        raise ValueError(f"{envelope_path}: empty or misaligned envelope channels")
    kick, bass, music = bar_content(env, hop, bpm, n_bars, lead)
    track = {
        "bpm": bpm, "n_bars": n_bars, "lead_in_bars": lead,
        "sections": [[s["start_bar"], s["label"].upper()] for s in sections],
        "fills": [[fill.start_bar, fill.end_bar] for fill in fills],
        "content": {"has_kick": kick, "has_bass": bass, "has_music": music},
    }
    vocals = vocal_bars(result, len(kick))
    if vocals is not None:
        track["content"]["has_vocal"] = vocals
    return track


def vocal_bars(result, n):
    """Per-bar 0/1 vocal flags from the detector's signals["vocal_regions"] ([start_sec, end_sec, start_bar,
    end_bar], bars in the track's own bar numbers, the clock of the section starts and of has_kick), for the
    regions of at least MIN_CLASH_VOCAL_BARS bars: short stabs set nothing. None when the detector recorded
    no such signal, or only unusable regions: the planner then ignores vocals for the track. An empty
    list from a detector that ran is a known zero."""
    regions = (result.get("signals") or {}).get("vocal_regions")
    if not isinstance(regions, (list, tuple)):
        return None
    flags, usable = [0] * n, not regions
    for region in regions:
        try:
            start, end = float(region[2]), float(region[3])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        usable = True
        if end - start < MIN_CLASH_VOCAL_BARS:
            continue
        for bar in range(max(math.ceil(start), 0), min(math.ceil(end), n)):
            flags[bar] = 1
    return flags if usable else None


def read_camelot(wav_path):
    """The track's key as a Camelot code from the sources the pipeline already reads (the file's own
    MIK/ID3 tags, then the Mixed In Key database, as propose_arrangement does), or None when neither has it."""
    try:
        from automated_dj_mixes.mik_reader import read_mik_db_track, read_mik_from_tags
        from automated_dj_mixes.sequencer import key_to_camelot
        key = None
        if wav_path.is_file():
            key = read_mik_from_tags(wav_path).key
            if not key:
                found = read_mik_db_track(wav_path)
                key = found.key if found else None
        return key_to_camelot(key) if key else None
    except Exception:
        return None


def clash_line(plan):
    """One report line per blend: vocal overlap and key clash, or why there is no number."""
    vocals = ("vocals unknown" if plan.vocal_clash_bars is None
              else f"vocals overlap {plan.vocal_clash_bars:g} bars")
    if plan.key_pair is None:
        key = "key unknown"
    elif plan.key_type != "clash":
        key = f"key {plan.key_pair[0]} to {plan.key_pair[1]} compatible ({plan.key_type})"
    else:
        key = (f"key {plan.key_pair[0]} to {plan.key_pair[1]} clash ({plan.key_steps} steps) "
               f"over {plan.key_tonal_bars:g} tonal bars")
    return f"{vocals}; {key}"


def order_input(project, sections_path):
    if sections_path is None:
        layouts = [p for p in (project / "Sections Review").glob("Sections_V*.json")
                   if p.stem.removeprefix("Sections_V").isdecimal()]
        if layouts:
            sections_path = max(layouts, key=lambda p: int(p.stem.removeprefix("Sections_V")))
    if sections_path is not None:
        return sections_path, True
    report = project / "Output" / "ARRANGEMENT_REPORT.json"
    if not report.is_file():
        raise ValueError(f"{project}: no layout sections or arrangement report; pass --sections")
    return report, False


def track_order(project, sections_path=None):
    return read_track_order(*order_input(project, sections_path))


def read_track_order(path, is_layout):
    data = read_json(path)
    if is_layout:
        reject_empty_layout_tracks(data, path)
        return [track.name for track in ordered_tracks(data)]
    transitions = data["transitions"]
    if not transitions:
        return []
    names = [transitions[0]["out_track"]]
    for transition in transitions:
        if transition["out_track"] != names[-1]:
            raise ValueError(f"{path}: transitions do not form an adjacent track order")
        names.append(transition["in_track"])
    return names


def decision_for_plan(index, out_name, in_name, outgoing, incoming, plan):
    """Encode the chosen plan exactly, including which source bars repeat."""
    def bar(value, name):
        return _decision_bar(value, name, index)

    entry = bar(plan.in_starts_at_out_bar, "entry_out_bar")
    switch = bar(plan.bass_switch_out_bar, "bass_switch_out_bar")
    trim = bar(plan.in_intro_cut_bars, "intro_trim_bars")
    swap_in = bar(plan.swap_incoming_bar, "swap_in_bar")
    end = bar(plan.out_end_bar, "out_end_bar")
    intro_ext = bar(plan.in_intro_looped_bars, "intro_loop extension")
    tail_ext = bar(plan.out_looped_bars, "tail_loop extension")
    if switch != entry - trim + intro_ext + swap_in:
        raise ValueError(f"decision for pair {index}: swap_in_bar cannot reproduce the planner swap")
    if end - outgoing["n_bars"] != tail_ext - plan.out_cut_bars - plan.out_trim_bars:
        raise ValueError(f"decision for pair {index}: out_end_bar disagrees with the planner edits")
    decision = {
        "pair_index": index, "out_track": out_name, "in_track": in_name,
        "entry_out_bar": entry, "intro_trim_bars": trim, "swap_in_bar": swap_in,
        "swap_cue": plan.in_point_kind or "unmarked",
        "out_cue": plan.out_point_kind or "unmarked",
        "out_end_bar": end, "reason": " | ".join(plan.why),
    }
    if intro_ext:
        upper = bar(plan.in_loop_phrase_end, "intro_loop.source_end_bar")
        if not 0 <= upper - intro_ext < upper <= incoming["n_bars"]:
            raise ValueError(f"decision for pair {index}: intro_loop has no real source window")
        decision["intro_loop"] = {"source_start_bar": upper - intro_ext, "source_end_bar": upper}
    if tail_ext:
        if not plan.out_loop_source_bars:
            raise ValueError(f"decision for pair {index}: tail_loop is missing its source bars")
        lower, upper = (bar(value, "tail_loop source") for value in plan.out_loop_source_bars)
        if not 0 <= lower < upper <= outgoing["n_bars"]:
            raise ValueError(f"decision for pair {index}: tail_loop has no real source window")
        reps, partial = divmod(tail_ext, upper - lower)
        decision["tail_loop"] = {
            "source_start_bar": lower, "source_end_bar": upper,
            "reps": int(reps), "partial_bars": partial,
        }
    return decision


def ranked_options(top, outgoing, incoming):
    """Every other plan the planner offers for this pair, in the planner's own order (its rank
    key: sane pairings first, then cheapest): the listed alternatives and the ranked options."""
    pool, notes = [], []
    for rank, alternative in enumerate(top.alternatives, 1):
        pool.append((f"alternative {rank}", alternative.plan))
    seen = {top.full_key(), *(plan.full_key() for _, plan in pool)}
    try:
        ranked = candidates(outgoing, incoming)
    except Exception as exc:
        ranked = []
        notes.append(f"  the ranked options could not be listed: {exc}")
    for rank, plan in enumerate(ranked, 1):
        if plan.full_key() not in seen:
            pool.append((f"ranked option {rank}", plan))
    keyed = []
    for label, plan in pool:
        try:
            key = _rank_key(plan)
            if not all(isinstance(value, (int, float)) for value in key):
                raise TypeError("the plan's rank fields are not all numbers")
            keyed.append((key, label, plan))
        except Exception as exc:
            notes.append(f"  {label} skipped: {exc}")
    keyed.sort(key=lambda item: item[0])
    return [(label, plan) for _, label, plan in keyed], notes


def refusal(top, plan):
    """Why a plan that is otherwise valid may not replace the top plan (empty: it may)."""
    why = []
    if plan.out_cut_bars:
        why.append("cuts the outgoing's outro")
    if plan.terms.get("flat", 0.0):
        why.append("has a flat stretch")
    if plan.cost - top.cost > ALT_MARGIN:
        why.append("over the limit")
    if top.sane and not plan.sane:
        why.append("one-sided" if not plan.paired else "not a sane pairing")
    return why


def whole_bar_decision(index, out_name, in_name, outgoing, incoming, top, error):
    """The first plan in the planner's own order that its decision checks accept and that is no
    worse than the best in kind (no outro cut, no flat stretch, within ALT_MARGIN of the best
    cost, sane if the best is sane); None if there is none. A fractional bar is never rounded:
    the planner scored that alignment, so a different plan replaces it. The replacement is
    validated on its own, so a second defect in the discarded top plan does not matter."""
    options, notes = ranked_options(top, outgoing, incoming)
    for note in notes:
        print(note)
    shown = {"skipped": 0, "refused": 0}
    hidden = {"skipped": 0, "refused": 0}

    def report(kind, line):
        if shown[kind] < SKIPPED_SHOWN:
            shown[kind] += 1
            print(line)
        else:
            hidden[kind] += 1

    refused, tried, found = [], 0, None
    for label, plan in options:
        if tried == MAX_OPTIONS_TRIED:
            print(f"  stopped after {MAX_OPTIONS_TRIED} ranked options for T{index}; "
                  f"{len(options) - tried} not tried")
            break
        tried += 1
        try:
            decision = decision_for_plan(index, out_name, in_name, outgoing, incoming, plan)
            why = refusal(top, plan)
            extra = plan.cost - top.cost
        except Exception as exc:
            report("skipped", f"  {label} skipped: {exc}")
            continue
        if why:
            refused.append((extra, ", ".join(why)))
            report("refused", f"  {label} refused: {extra:+.1f} over the best, {', '.join(why)}")
            continue
        found = label, plan, decision, extra
        break
    if hidden["skipped"]:
        print(f"  and {hidden['skipped']} more not whole-bar")
    if hidden["refused"]:
        print(f"  and {hidden['refused']} more whole-bar plans refused")
    if found is None:
        if refused:
            nearest, why = min(refused, key=lambda item: abs(item[0]))
            print(f"T{index}: no whole-bar plan qualified (limit +{ALT_MARGIN:.1f} over the best "
                  f"{top.cost:.2f}, sane if the best is sane, no outro cut, no flat stretch); "
                  f"nearest was {nearest:+.1f}, {why}")
        else:
            print(f"T{index}: no whole-bar plan among {tried} ranked options")
        return None
    label, plan, decision, extra = found
    gap = (f"{extra:+.2f}" if extra >= 0
           else f"{extra:+.2f}, ranked lower by the planner's own order")
    said = (f"T{index}: top plan (entry {top.in_starts_at_out_bar:g}, cost {top.cost:.2f}) has a "
            f"fractional bar ({error}); used {label} (entry {plan.in_starts_at_out_bar:g}, "
            f"swap {plan.bass_switch_out_bar:g}, end {plan.out_end_bar:g}, cost {plan.cost:.2f} "
            f"vs the planner's best {top.cost:.2f} ({gap}))")
    print(said)
    decision["reason"] = said + " | " + decision["reason"]
    return decision, {
        "pair_index": index, "out_track": out_name, "in_track": in_name, "used": label,
        "top_entry_out_bar": top.in_starts_at_out_bar, "top_cost": round(top.cost, 4),
        "top_error": error, "entry_out_bar": decision["entry_out_bar"],
        "swap_out_bar": plan.bass_switch_out_bar, "out_end_bar": decision["out_end_bar"],
        "cost": round(plan.cost, 4), "cost_difference": round(extra, 4),
    }


def print_plan(plan, label):
    print(f"  {label}: entry {plan.in_starts_at_out_bar:g}, swap {plan.bass_switch_out_bar:g}, "
          f"end {plan.out_end_bar:g}, cost {plan.cost:.2f}"
          + (" [ONE-SIDED / UNPAIRED]" if not plan.paired else ""))
    print(f"    Incoming source at swap {plan.swap_incoming_bar:g}, "
          f"trim {plan.in_intro_cut_bars:g}, repeat {plan.in_intro_looped_bars:g} "
          f"at {plan.in_loop_phrase_end:g}; outgoing extension {plan.out_looped_bars:g} "
          f"from {plan.out_loop_source_bars}, cut {plan.out_cut_bars + plan.out_trim_bars:g}")
    if plan.vocal_clash_bars is not None or plan.key_pair is not None:
        print(f"    {clash_line(plan)}")
    for reason in plan.why:
        print(f"    {reason}")


def write_decisions(path, names, tracks, *, allow_partial=False):
    if len(names) != len(tracks) or len(names) < 2 or len(set(names)) != len(names):
        raise ValueError("The mix order must contain at least two distinct, analysed tracks")
    decisions, errors, fallback_pairs, whole_bar_used = [], [], [], []
    for index, (outgoing, incoming) in enumerate(zip(tracks, tracks[1:]), 1):
        out_name, in_name = names[index - 1:index + 1]
        print(f"T{index}: {out_name} -> {in_name}")
        notes = []
        plan = (plan_blend(outgoing, incoming, why=notes) if allow_partial
                else plan_blend(outgoing, incoming))
        if plan is None:
            reason = " | ".join(notes) or "plan_blend returned None"
        else:
            print_plan(plan, "Top")
            for rank, alternative in enumerate(plan.alternatives, 1):
                print_plan(alternative.plan, f"Alternative {rank}")
                print(f"    {alternative.reason}. {alternative.feel}")
            try:
                decision = decision_for_plan(index, out_name, in_name, outgoing, incoming, plan)
            except (ValueError, OverflowError) as exc:
                reason = str(exc)
                used = (whole_bar_decision(index, out_name, in_name, outgoing, incoming, plan, reason)
                        if FRACTIONAL_BAR in reason else None)
                if used is not None:
                    decisions.append(used[0])
                    whole_bar_used.append(used[1])
                    continue
            else:
                decisions.append(decision)
                continue
        if allow_partial:
            print(f"FALLBACK T{index}: {reason}")
            fallback_pairs.append({"pair_index": index, "out_track": out_name,
                                   "in_track": in_name, "reason": reason})
        else:
            errors.append(f"T{index}: {reason}")
            print(f"  ERROR: {errors[-1]}")
    if whole_bar_used:
        print(f"{len(whole_bar_used)} {'pair' if len(whole_bar_used) == 1 else 'pairs'} "
              "used a whole-bar alternative: "
              + ", ".join(f"T{row['pair_index']} ({row['cost_difference']:+.2f})"
                          for row in whole_bar_used))
    if errors:
        raise ValueError("Decisions file was not written; no substitutions were made. "
                         + " | ".join(errors))
    if not decisions:
        raise ValueError("Decisions file was not written; the planner found no usable pairs.")
    payload = {"transitions": decisions}
    if allow_partial:
        payload["fallback_pairs"] = fallback_pairs
    if whole_bar_used:
        payload["whole_bar_alternative_count"] = len(whole_bar_used)
        payload["whole_bar_alternatives"] = whole_bar_used
    write_json_atomically(path, payload, indent=2)
    return decisions


def write_json_atomically(path, data, **options):
    payload = (json.dumps(data, **options) + "\n").encode("ascii")
    with staged_file(path, payload) as partial:
        partial.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--sections", type=Path, help="Exact layout sections JSON used by Phase 2")
    parser.add_argument("--out", required=True, type=Path, help="Decisions JSON to write")
    parser.add_argument("--content", type=Path, help="Save planner tracks and measured bar content")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Leave unusable pairs to the rule-based arranger if at least one pair plans")
    args = parser.parse_args(argv)
    try:
        order_path, is_layout = order_input(args.project, args.sections)
        names = [html.unescape(name) for name in read_track_order(order_path, is_layout)]
        stem_dir = args.project / "_Stem Analysis"
        stem_inputs = [(stem_dir / f"SECTIONS_STEM_{name}.json",
                        stem_dir / f"{name}__stemenv.npz") for name in names]
        inputs = [order_path, *(path for pair in stem_inputs for path in pair)]
        reject_aliased_output(args.out, inputs)
        if args.content is not None:
            reject_aliased_output(args.content, [*inputs, args.out])
        tracks = [convert_track(read_json(sections), envelope)
                  for sections, envelope in stem_inputs]
        for name, track in zip(names, tracks):
            track["key"] = read_camelot(args.project / "Audio" / f"{name}.wav")
        write_decisions(args.out, names, tracks, allow_partial=args.allow_partial)
        if args.content is not None:
            write_json_atomically(args.content, {"tracks": dict(zip(names, tracks))})
    except (OSError, ValueError, KeyError, IndexError) as exc:
        parser.exit(1, f"plan_mix_decisions: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
