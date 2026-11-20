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
from phrase_blend import plan_blend
from propose_arrangement import ordered_tracks, reject_aliased_output, reject_empty_layout_tracks
from section_format import groove_fills


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
    return {
        "bpm": bpm, "n_bars": n_bars, "lead_in_bars": lead,
        "sections": [[s["start_bar"], s["label"].upper()] for s in sections],
        "fills": [[fill.start_bar, fill.end_bar] for fill in fills],
        "content": {"has_kick": kick, "has_bass": bass, "has_music": music},
    }


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


def print_plan(plan, label):
    print(f"  {label}: entry {plan.in_starts_at_out_bar:g}, swap {plan.bass_switch_out_bar:g}, "
          f"end {plan.out_end_bar:g}, cost {plan.cost:.2f}"
          + (" [ONE-SIDED / UNPAIRED]" if not plan.paired else ""))
    print(f"    Incoming source at swap {plan.swap_incoming_bar:g}, "
          f"trim {plan.in_intro_cut_bars:g}, repeat {plan.in_intro_looped_bars:g} "
          f"at {plan.in_loop_phrase_end:g}; outgoing extension {plan.out_looped_bars:g} "
          f"from {plan.out_loop_source_bars}, cut {plan.out_cut_bars + plan.out_trim_bars:g}")
    for reason in plan.why:
        print(f"    {reason}")


def write_decisions(path, names, tracks):
    if len(names) != len(tracks) or len(names) < 2 or len(set(names)) != len(names):
        raise ValueError("The mix order must contain at least two distinct, analysed tracks")
    decisions, errors = [], []
    for index, (outgoing, incoming) in enumerate(zip(tracks, tracks[1:]), 1):
        out_name, in_name = names[index - 1:index + 1]
        print(f"T{index}: {out_name} -> {in_name}")
        plan = plan_blend(outgoing, incoming)
        if plan is None:
            errors.append(f"T{index}: plan_blend returned None")
            print(f"  ERROR: {errors[-1]}")
            continue
        print_plan(plan, "Top")
        for rank, alternative in enumerate(plan.alternatives, 1):
            print_plan(alternative.plan, f"Alternative {rank}")
            print(f"    {alternative.reason}. {alternative.feel}")
        try:
            decision = decision_for_plan(index, out_name, in_name, outgoing, incoming, plan)
        except (ValueError, OverflowError) as exc:
            errors.append(f"T{index}: {exc}")
            print(f"  ERROR: {errors[-1]}")
        else:
            decisions.append(decision)
    if errors:
        raise ValueError("Decisions file was not written; no substitutions were made. "
                         + " | ".join(errors))
    write_json_atomically(path, {"transitions": decisions}, indent=2)
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
        write_decisions(args.out, names, tracks)
        if args.content is not None:
            write_json_atomically(args.content, {"tracks": dict(zip(names, tracks))})
    except (OSError, ValueError, KeyError, IndexError) as exc:
        parser.exit(1, f"plan_mix_decisions: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
