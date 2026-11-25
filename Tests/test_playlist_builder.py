"""Tests for Tools/playlist_builder.py with the Neon reader STUBBED everywhere.

No network, no database, no credentials file: an autouse fixture makes the real credentials reader raise and makes
`import psycopg2` fail unless a test installs a fake one. The builder prints its playlist to stdout (it writes no output
file), so the main path is tested through capsys, with the rows file (the offline `--rows` input) written to tmp_path.

Not tested on purpose: the exact numbers of the cool and cult presets (Sam's taste, still being tuned), only that they
return a finite score for every row. Resolver integration uses an isolated copy of the resolver and a synthetic index.
"""
import argparse
import datetime
import importlib.util
import json
import math
import os
import re
import socket
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_SOURCE = ROOT / "Tools" / "playlist_builder.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def _pb_module():
    return _load(REAL_SOURCE, "playlist_builder_under_test")


@pytest.fixture(autouse=True)
def pb(_pb_module, monkeypatch):
    """The module under test, with every path to the credentials file and to a real database closed."""
    def _no_credentials():
        raise AssertionError("a test reached the real credentials reader")
    monkeypatch.setattr(_pb_module, "ro_url_from_credentials_file", _no_credentials)
    monkeypatch.setitem(sys.modules, "psycopg2", None)            # `import psycopg2` raises ImportError
    monkeypatch.setitem(sys.modules, "psycopg2.extras", None)
    monkeypatch.delenv("MIX_PLANNER_RO_DATABASE_URL", raising=False)
    monkeypatch.delenv("DJMIX_MIX_ROOTS", raising=False)
    monkeypatch.setattr(_pb_module, "default_mix_roots", lambda: [])    # no test reads earlier mixes from this machine's disk
    return _pb_module


def args(**kw):
    base = dict(cmd="build", label=[], artist=[], year_from=None, year_to=None, min_streams=None, arc="rise",
                preset="any", n=12, max_per_artist=1, heat_min=0, heat_max=10, rows=None, credible=False, mixes_dir=[])
    base.update(kw)
    return argparse.Namespace(**base)


def row(title="Song", artist="Artist", genre="House", streams=1000, **kw):
    r = dict(credit_key=f"{artist}|{title}|{kw.get('remixer')}|{kw.get('mix_name')}", artist=artist, title=title,
             mix_name=None, remixer=None, roles="mix", label="Lab", genre=genre, release_date="2018-05-01", bpm=None,
             musical_key=None, streams_plays=streams, streams_confidence="high", enrichment_status="draft", display_tier="A")
    r.update(kw)
    return r


LADDER = [("House / Soulful", 1), ("Deep House", 2), ("House", 3), ("Afro House", 4), ("Jackin House", 5),
          ("Tech House", 6), ("Techno (Peak Time / Driving)", 8)]


def ladder_rows(streams=1000):
    return [row(title=f"Track {g}", artist=f"Artist {i}", genre=g, streams=streams) for i, (g, _) in enumerate(LADDER)]


# --- where_clause / SELECT ----------------------------------------------------------------------------------------

def test_where_clause_base_requires_streams(pb):
    assert pb.where_clause(args()) == "c.streams_plays > 0"


def test_where_clause_label_and_artist_are_or_groups(pb):
    w = pb.where_clause(args(label=["Defected", "DFTD"], artist=["Eli Brown"]))
    assert "(c.label ilike '%Defected%' or c.label ilike '%DFTD%')" in w
    assert "(c.artist ilike '%Eli Brown%')" in w


def test_where_clause_numeric_filters(pb):
    w = pb.where_clause(args(year_from=2015, year_to=2020, min_streams=5000))
    assert w == ("c.streams_plays > 0 and extract(year from c.release_date) >= 2015 "
                 "and extract(year from c.release_date) <= 2020 and c.streams_plays >= 5000")


def test_where_clause_doubles_single_quotes(pb):
    w = pb.where_clause(args(label=["O'Brien' or 1=1 --"]))
    assert "%O''Brien'' or 1=1 --%" in w
    assert w.count("'") % 2 == 0


def test_where_clause_numeric_filters_are_cast_to_int(pb):
    with pytest.raises(ValueError):
        pb.where_clause(args(year_from="2015; drop table credits"))


def test_select_is_one_read_only_select(pb):
    sql = pb.SELECT.format(where=pb.where_clause(args())).lower()
    assert sql.lstrip().startswith("select")
    for word in ("insert ", "update ", "delete ", "drop ", "alter ", "truncate ", "grant "):
        assert word not in sql
    assert ";" not in sql


# --- small pure helpers -------------------------------------------------------------------------------------------

def test_norm(pb):
    assert pb.norm("  A-B   c!! ") == "a b c"
    assert pb.norm(None) == ""


def test_year(pb):
    assert pb.year({"release_date": "2019-03-02"}) == 2019
    assert pb.year({"release_date": None}) is None
    assert pb.year({}) is None


def test_song_keys_one_key_per_version(pb):
    original = row("Better When")
    again = row("Better When", streams=99999, credit_key="other")
    remix = row("Better When", remixer="Bob")
    mixname = row("Better When", mix_name="Extended Mix")
    assert pb.song_keys(original) == pb.song_keys(again)                 # repeat rows of the SAME version collapse
    assert pb.song_keys(original) != pb.song_keys(remix)                 # a remix is a different track
    assert pb.song_keys(original) != pb.song_keys(mixname)
    assert pb.song_keys(remix) != pb.song_keys(row("Better When", remixer="Carol"))


@pytest.mark.parametrize("field,value", [
    ("title", "Song (Acapella)"), ("title", "Song Instrumental"), ("title", "Song Instrumenatal"), ("mix_name", "Dub Mix"),
    ("mix_name", "Radio Edit"), ("title", "Song (From Stems)"), ("title", "Song Apella"), ("title", "DJ Tool"),
    ("genre", "Dub"), ("mix_name", "ACAPELLA"),
])
def test_not_a_playlist_track_matches(pb, field, value):
    assert pb.NOT_A_PLAYLIST_TRACK.search(value)


@pytest.mark.parametrize("value", ["Double Dutch", "Dubai Nights", "Extended Mix", "Original Mix", "Radio Ga Ga", "Instrument"])
def test_not_a_playlist_track_leaves_normal_titles(pb, value):
    assert not pb.NOT_A_PLAYLIST_TRACK.search(value)


# --- prepare ------------------------------------------------------------------------------------------------------

def test_prepare_adds_heat_and_int_streams_without_mutating_input(pb):
    original = row("A", genre="Tech House", streams="5000")
    snapshot = dict(original)
    out = pb.prepare([original], args())
    assert out[0]["heat"] == 6 and out[0]["streams_plays"] == 5000
    assert original == snapshot and "heat" not in original               # the caller's row is untouched
    assert out[0] is not original


def test_prepare_drops_genres_off_the_ladder(pb):
    rows = [row("A", genre="Trance"), row("B", genre=None), row("C", genre="House")]
    assert [r["title"] for r in pb.prepare(rows, args())] == ["C"]


def test_prepare_heat_window(pb):
    rows = ladder_rows()
    assert [r["heat"] for r in pb.prepare(rows, args(heat_min=4, heat_max=6))] == [4, 5, 6]


@pytest.mark.parametrize("bpm,kept", [(100, False), (117, False), (118, True), (125, True), (132, True), (133, False),
                                      (None, True), (0, True)])
def test_prepare_tempo_band_only_when_bpm_is_present(pb, bpm, kept):
    assert bool(pb.prepare([row("A", bpm=bpm)], args())) is kept


def test_prepare_year_filters_need_a_release_date(pb):
    rows = [row("old", release_date="2016-01-01"), row("mid", release_date="2018-06-01"),
            row("new", release_date="2021-01-01"), row("none", release_date=None)]
    assert [r["title"] for r in pb.prepare(rows, args(year_from=2017))] == ["mid", "new"]
    assert [r["title"] for r in pb.prepare(rows, args(year_to=2018))] == ["old", "mid"]
    assert [r["title"] for r in pb.prepare(rows, args(year_from=2017, year_to=2019))] == ["mid"]
    assert len(pb.prepare(rows, args())) == 4                            # no filter: the undated row stays


def test_prepare_drops_acapellas_dubs_and_radio_edits(pb):
    rows = [row("Good"), row("Bad (Acapella)"), row("Fine", mix_name="Dub Mix"), row("Short", mix_name="Radio Edit")]
    assert [r["title"] for r in pb.prepare(rows, args())] == ["Good"]


# --- presets and arcs ---------------------------------------------------------------------------------------------

def test_preset_hits_follows_streams(pb):
    pool = pb.prepare([row(f"T{i}", artist=f"A{i}", streams=s) for i, s in enumerate((100, 200, 300, 400))], args())
    score = pb.preset_score(pool, "hits")
    assert [round(score(r), 4) for r in pool] == [0.25, 0.5, 0.75, 1.0]


def test_preset_any_is_between_half_and_one_and_follows_streams(pb):
    pool = pb.prepare([row(f"T{i}", artist=f"A{i}", streams=s) for i, s in enumerate((100, 200, 300, 400))], args())
    vals = [pb.preset_score(pool, "any")(r) for r in pool]
    assert vals == sorted(vals) and all(0.5 <= v <= 1.0 for v in vals)


@pytest.mark.parametrize("preset", ["cool", "cult"])
def test_cool_and_cult_return_a_finite_score_for_every_row(pb, preset):
    # Deliberately not pinning the shape of these two: they are Sam's taste and are still being tuned.
    rows = [row(f"T{i}", artist=f"A{i}", streams=1000 * (i + 1), release_date=f"{2010 + i}-01-01") for i in range(8)]
    rows.append(row("undated", artist="U", release_date=None))
    pool = pb.prepare(rows, args())
    score = pb.preset_score(pool, preset)
    assert all(math.isfinite(score(r)) for r in pool)


def test_arcs_have_the_documented_endpoints(pb):
    assert pb.ARCS["rise"](0) == 1 and pb.ARCS["rise"](1) == 7.5
    assert [pb.ARCS["flat"](p) for p in (0, 0.5, 1)] == [5.0, 5.0, 5.0]
    ps = [i / 20 for i in range(21)]
    rise = [pb.ARCS["rise"](p) for p in ps]
    assert rise == sorted(rise)
    assert all(0 <= pb.ARCS["wave"](p) <= 10 for p in ps)


# --- build --------------------------------------------------------------------------------------------------------

def test_build_rise_orders_by_heat_and_uses_every_track_when_n_matches(pb):
    rows = list(reversed(ladder_rows()))
    picked = pb.build(rows, args(n=7))
    assert [r["heat"] for r in picked] == [1, 2, 3, 4, 5, 6, 8]


def test_build_rise_with_fewer_slots_spans_housey_to_hard(pb):
    picked = pb.build(ladder_rows(), args(n=3))
    heats = [r["heat"] for r in picked]
    assert len(picked) == 3 and heats == sorted(heats) and heats[0] <= 2 and heats[-1] >= 6


def test_build_rise_is_ordered_by_heat_even_when_the_slots_chose_out_of_order(pb):
    # With the hits preset the loud heat-2 row wins slot 0 (target 1) over the quiet heat-1 row, so the slots pick
    # [heat 2, heat 1]; a rise must then be re-ordered by heat.
    rows = [row("Quiet", artist="Q", genre="House / Soulful", streams=100),
            row("Loud", artist="L", genre="Deep House", streams=900000)]
    picked = pb.build(rows, args(n=2, preset="hits"))
    assert [r["heat"] for r in picked] == [1, 2]
    flat = pb.build(rows, args(n=2, preset="hits", arc="flat"))
    assert [r["heat"] for r in flat] == [2, 1]                            # other arcs keep the slot order


def test_build_flat_arc_stays_in_the_middle(pb):
    picked = pb.build(ladder_rows(), args(n=3, arc="flat"))
    assert len(picked) == 3 and all(4 <= r["heat"] <= 6 for r in picked)


def test_build_wave_arc_runs_and_respects_n(pb):
    assert len(pb.build(ladder_rows(), args(n=5, arc="wave"))) == 5


def test_build_respects_n_and_never_exceeds_the_pool(pb):
    assert len(pb.build(ladder_rows(), args(n=2))) == 2
    assert len(pb.build(ladder_rows(), args(n=50))) == 7


def test_build_one_track_per_artist_by_default(pb):
    rows = [row("One", artist="Same", genre="House"), row("Two", artist="Same", genre="Tech House"),
            row("Three", artist="Other", genre="Deep House")]
    picked = pb.build(rows, args(n=3))
    assert sorted(r["artist"] for r in picked) == ["Other", "Same"]
    assert len(pb.build(rows, args(n=3, max_per_artist=2))) == 3


def test_build_collapses_repeat_rows_of_one_version_but_keeps_a_remix(pb):
    rows = [row("Better When", artist="A", genre="House", credit_key="1"),
            row("Better When", artist="A", genre="House", credit_key="2", streams=5),
            row("Better When", artist="A", genre="House", credit_key="3", remixer="Bob")]
    picked = pb.build(rows, args(n=3, max_per_artist=5))
    assert len(picked) == 2
    assert sorted(r.get("remixer") or "" for r in picked) == ["", "Bob"]


def test_build_never_picks_acapellas_or_dubs(pb):
    rows = ladder_rows() + [row("Loud (Acapella)", artist="X", genre="Tech House", streams=10 ** 9),
                            row("Loud", artist="Y", genre="Tech House", streams=10 ** 9, mix_name="Dub Mix")]
    assert not any("Acapella" in r["title"] or r.get("mix_name") == "Dub Mix" for r in pb.build(rows, args(n=9)))


def test_build_hits_preset_prefers_the_most_streamed_at_equal_heat(pb):
    rows = [row("Quiet", artist="A", genre="House", streams=100), row("Loud", artist="B", genre="House", streams=900000)]
    assert [r["title"] for r in pb.build(rows, args(n=1, arc="flat", preset="hits", heat_min=3, heat_max=3))] == ["Loud"]


def test_build_empty_input_returns_empty_and_warns(pb, capsys):
    assert pb.build([], args(n=5)) == []
    assert "[warn] only 0 usable tracks for n=5" in capsys.readouterr().err


def test_build_warns_when_the_pool_is_smaller_than_n(pb, capsys):
    picked = pb.build(ladder_rows()[:2], args(n=5))
    assert len(picked) == 2
    assert "[warn] only 2 usable tracks for n=5" in capsys.readouterr().err


def test_build_n_zero_returns_nothing(pb):
    assert pb.build(ladder_rows(), args(n=0)) == []


def test_build_is_deterministic_and_leaves_input_alone(pb):
    rows = ladder_rows()
    snapshot = json.dumps(rows, sort_keys=True)
    first = pb.build(rows, args(n=5))
    second = pb.build(rows, args(n=5))
    assert first == second
    assert json.dumps(rows, sort_keys=True) == snapshot


# --- report and main (the output is stdout) -----------------------------------------------------------------------

def test_report_line_format(pb, capsys):
    r = pb.prepare([row("Song", artist="Art", genre="House", streams=12345, mix_name="Original Mix",
                        musical_key="8A", bpm=124)], args())
    pb.report(r, args(arc="wave", preset="cool"))
    out = capsys.readouterr().out
    assert out.startswith("# Playlist draft: arc=wave preset=cool n=1  (source: Neon credits ledger)")
    assert " 1. [3] Art - Song  (Original Mix) | Lab 2018 | House | 12,345 streams | bpm 124 | key 8A | tier A" in out
    assert "Heat curve: 3" in out and out.rstrip().endswith("measured by the pipeline.")


def test_report_version_label_and_missing_values(pb, capsys):
    rows = pb.prepare([row("Remixed", artist="A", genre="House", remixer="Bob", release_date=None),
                       row("Plain", artist="B", genre="Deep House")], args())
    pb.report(pb.build(rows, args(n=2)), args())
    out = capsys.readouterr().out
    assert "(Bob remix)" in out and "(original/unspecified)" in out
    assert "Lab ? |" in out and "bpm - | key -" in out
    assert "Heat curve: 2 3" in out


def test_report_empty_playlist(pb, capsys):
    pb.report([], args(n=5))
    out = capsys.readouterr().out
    assert "n=0" in out and "Heat curve:" in out
    assert not [line for line in out.splitlines() if line[:3].strip().rstrip(".").isdigit()]


def _run_main(pb, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["playlist_builder.py", *argv])
    pb.main()


@pytest.fixture
def resolver_tools(pb, monkeypatch, tmp_path):
    tools = tmp_path / "Tools"
    tools.mkdir()
    (tools / "resolve_masters.py").write_bytes((REAL_SOURCE.parent / "resolve_masters.py").read_bytes())
    monkeypatch.setattr(pb, "__file__", str(tools / "playlist_builder.py"))
    return tools


def _index_folder(tools, name, files):
    renders = tools / name / "MASTER RENDERS"
    renders.mkdir(parents=True)
    for filename in files:
        (renders / filename).touch()  # The resolver only needs names and file metadata.
    (tools / "folder_index.json").write_text(json.dumps([[name, str(renders.parent)]]), encoding="utf-8")
    return renders


def _run_resolved(pb, monkeypatch, tools, *rows):
    rows_file = tools / "rows.json"
    rows_file.write_text(json.dumps(rows), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", str(len(rows)),
              "--max-per-artist", str(len(rows)), "--resolve")


def test_resolve_remix_uses_the_latest_matching_delivery(pb, monkeypatch, capsys, resolver_tools):
    renders = _index_folder(resolver_tools, "Artist - Better When [Label] Project",
                           ["Better When (Bob Remix) SW V3.wav", "Better When (Bob Remix) SW V2.wav",
                            "Better When (Original Mix) SW V5.wav"])
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When", remixer="Bob"))
    assert f"   MASTER: {renders / 'Better When (Bob Remix) SW V3.wav'}" in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("mix", ["Bob Remix", "Dub", "Acapella", "Instrumental", "Radio Edit", "Bob Mix"])
def test_resolve_original_rejects_alternate_only_folder(pb, monkeypatch, capsys, resolver_tools, mix):
    _index_folder(resolver_tools, "Artist - Better When [Label] Project", [f"Better When ({mix}) SW V3.wav"])
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When"))
    out = capsys.readouterr().out
    assert "NONE: " in out and "MASTER: " not in out


@pytest.mark.parametrize("remixer,mix_name,version", [
    (None, "Extended Mix", "Extended Mix"), ("Bob", "Extended Remix", "Bob Extended Remix"),
    ("Bob", "Bob Extended Remix", "Bob Extended Remix"),
])
def test_resolve_requires_mix_in_the_filename(pb, monkeypatch, capsys, resolver_tools, remixer, mix_name, version):
    renders = _index_folder(resolver_tools, f"Artist - Better When ({version}) [Label] Project",
                           ["Better When (Original Mix) SW V3.wav", f"Better When ({version}) SW V1.wav"])
    credit = row("Better When", remixer=remixer, mix_name=mix_name)
    _run_resolved(pb, monkeypatch, resolver_tools, credit)
    path = renders / f"Better When ({version}) SW V1.wav"
    assert f"   MASTER: {path}" in capsys.readouterr().out.splitlines()
    path.unlink()
    _run_resolved(pb, monkeypatch, resolver_tools, credit)
    out = capsys.readouterr().out
    assert "NONE: " in out and "MASTER: " not in out


def test_resolve_ep_picks_the_named_track(pb, monkeypatch, capsys, resolver_tools):
    name = "02 Better When.wav"
    renders = _index_folder(resolver_tools, "Artist - Better When [Label] Project",
                           ["01 Change The Situation (Extended Mix) SW V9.wav", name,
                            "03 Better When Again (Extended Mix) SW V9.wav"])
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When"))
    assert f"   MASTER: {renders / name}" in capsys.readouterr().out.splitlines()
    (renders / name).unlink()
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When"))
    out = capsys.readouterr().out
    assert "NONE: " in out and "MASTER: " not in out


@pytest.mark.parametrize("label", ["16 Bit MASTER", "24 Bit MASTER", "32 Bit MASTER"])
def test_resolve_accepts_every_bit_depth_delivery_label(pb, monkeypatch, capsys, resolver_tools, label):
    # Found live 2026-11-20: three real 16 Bit masters came back NONE because only "24 bit" was a delivery label.
    name = f"Artist - Better When {label}.wav"
    renders = _index_folder(resolver_tools, "Artist - Better When & Change The Situation [Label] Project",
                           [name, f"Artist - Change The Situation {label}.wav"])
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When"))
    assert f"   MASTER: {renders / name}" in capsys.readouterr().out.splitlines()


def test_resolve_title_only_file_needs_the_credited_artist(pb, monkeypatch, capsys, resolver_tools):
    _index_folder(resolver_tools, "Someone Else - Better When [Artist Records] Project", ["Better When.wav"])
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When"))
    out = capsys.readouterr().out
    assert "NONE: " in out and "MASTER: " not in out


@pytest.mark.parametrize("title_suffix,expected", [("", "MASTER: "), (" (Radio Edit)", "NONE: ")])
def test_resolve_untitled_delivery_needs_a_safe_named_anchor(pb, monkeypatch, capsys, resolver_tools, title_suffix, expected):
    renders = _index_folder(resolver_tools, "Artist - Better When [Label] Project",
                           [f"Better When{title_suffix} MASTER.wav", "MASTER AMENDED V2.wav"])
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When"))
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith(f"   {expected}") for line in lines)
    if not title_suffix: assert f"   MASTER: {renders / 'MASTER AMENDED V2.wav'}" in lines


def test_resolve_prints_every_note_and_counts_picks(pb, monkeypatch, capsys, resolver_tools):
    renders = _index_folder(resolver_tools, "Artist - Better When [Label] Project",
                           ["02 Better When SW V1.wav", "01 Change The Situation.wav", "SW V4.wav", "SW V5.wav"])
    _run_resolved(pb, monkeypatch, resolver_tools, row("Better When"), row("Better When", remixer="Bob"), row("Missing"))
    lines = capsys.readouterr().out.splitlines()
    assert lines.count('   NONE: no file matched the hint "bob remix"') == 1
    assert lines.count("   NONE: no candidate matches the credit's title and version") == 1
    for name in ("SW V4.wav", "SW V5.wav"):
        assert lines.count(f"   NOTE: the folder may hold other tracks: {renders / name}") == 2
    assert lines[-1] == "Resolved 1 of 3; 2 with notes; 2 not found"


@pytest.mark.parametrize("index", [None, "{", "{}"])
def test_resolve_bad_index_exits_before_playlist(pb, monkeypatch, capsys, resolver_tools, index):
    if index is not None: (resolver_tools / "folder_index.json").write_text(index, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _run_main(pb, monkeypatch, "build", "--resolve")
    assert e.value.code == "Cannot read Tools/folder_index.json. Run python Tools/build_folder_index.py."
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""  # Python prints the single SystemExit message on stderr at the CLI.


def test_main_without_resolve_keeps_the_existing_report(pb, monkeypatch, capsys, resolver_tools):
    rows_file = resolver_tools / "rows.json"
    rows_file.write_text(json.dumps([row()]), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1")
    out = capsys.readouterr().out
    manifest_text, _, report_text = out.partition("\n# Playlist draft")     # the manifest now comes first; the report is unchanged
    assert manifest_text.splitlines()[0] == "built 1 of 1 requested"
    assert "# Playlist draft" + report_text == (
        "# Playlist draft: arc=rise preset=any n=1  (source: Neon credits ledger)\n\n"
        "Each row is one VERSION with its own streams (a remix and an original are different tracks). "
        "Genre is a DB DRAFT tag.\n\n"
        " 1. [3] Artist - Song  (original/unspecified) | Lab 2018 | House | 1,000 streams | bpm - | key - | tier A\n"
        "\nHeat curve: 3\n"
        "Next: Tools/resolve_masters.py to find audio + confirm Sam's version; BPM/key are measured by the pipeline.\n"
    )


def test_main_build_from_a_rows_file(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows() + [row("Dubby (Dub Mix)", artist="Z", mix_name="Dub Mix")]), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MIX_PLANNER_RO_DATABASE_URL", "postgresql://canary.invalid/never")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--arc", "rise", "--n", "4")
    out = capsys.readouterr().out
    numbered = [line for line in out.splitlines() if line[:3].strip().rstrip(".").isdigit()]
    assert len(numbered) == 4 and "Dubby" not in out
    heats = [int(line.split("[")[1].split("]")[0]) for line in numbered]
    assert heats == sorted(heats)
    assert "Heat curve: " + " ".join(str(h) for h in heats) in out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rows.json"]   # the builder wrote no file


def test_main_empty_rows_file_gives_an_empty_draft(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text("[]", encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "3")
    captured = capsys.readouterr()
    assert "n=0" in captured.out and "[warn] only 0 usable tracks for n=3" in captured.err


def test_main_filters_apply_to_a_rows_file(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "7", "--heat-min", "5", "--heat-max", "8")
    assert "Heat curve: 5 6 8" in capsys.readouterr().out


def test_main_sql_prints_the_select_and_never_fetches(pb, monkeypatch, capsys):
    monkeypatch.setattr(pb, "fetch", lambda a: (_ for _ in ()).throw(AssertionError("sql must not fetch")))
    _run_main(pb, monkeypatch, "sql", "--label", "Defected", "--min-streams", "5000")
    out = capsys.readouterr().out
    assert out.lstrip().lower().startswith("select") and "c.label ilike '%Defected%'" in out
    assert "c.streams_plays >= 5000" in out


def test_main_rejects_an_unknown_arc(pb, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run_main(pb, monkeypatch, "build", "--arc", "zigzag")
    assert e.value.code == 2


# --- fetch with a STUBBED Neon reader -----------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def execute(self, sql):
        self.log.append(("execute", sql))

    def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def set_session(self, **kw):
        self.log.append(("set_session", kw))

    def cursor(self, cursor_factory=None):
        self.log.append(("cursor", cursor_factory))
        return _FakeCursor(self.log, self.rows)


def _install_fake_psycopg2(monkeypatch, rows):
    log = []
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object()
    fake.extras = extras

    def connect(url, connect_timeout=None):
        log.append(("connect", url, connect_timeout))
        return _FakeConnection(log, rows)
    fake.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)
    return log, extras


def test_fetch_uses_a_read_only_session_and_only_the_select(pb, monkeypatch):
    db_rows = [row("A", release_date=datetime.date(2019, 3, 2)), row("B", release_date=None)]
    log, extras = _install_fake_psycopg2(monkeypatch, db_rows)
    monkeypatch.setenv("MIX_PLANNER_RO_DATABASE_URL", "postgresql://stub.invalid/none")
    a = args(label=["Defected"])
    got = pb.fetch(a)
    kinds = [entry[0] for entry in log]
    assert kinds == ["connect", "set_session", "cursor", "execute"]      # read-only is set before anything is sent
    assert log[0] == ("connect", "postgresql://stub.invalid/none", 8)
    assert log[1] == ("set_session", {"readonly": True})
    assert log[2] == ("cursor", extras.RealDictCursor)
    assert log[3][1] == pb.SELECT.format(where=pb.where_clause(a)) and log[3][1].lstrip().lower().startswith("select")
    assert [r["release_date"] for r in got] == ["2019-03-02", None]
    assert [r["title"] for r in got] == ["A", "B"]


def test_fetch_prefers_the_environment_url_to_the_credentials_file(pb, monkeypatch):
    log, _ = _install_fake_psycopg2(monkeypatch, [])
    monkeypatch.setenv("MIX_PLANNER_RO_DATABASE_URL", "postgresql://env.invalid/db")
    pb.fetch(args())                                                      # the autouse stub raises if the file were read
    assert log[0][1] == "postgresql://env.invalid/db"


def test_fetch_without_any_url_exits_with_instructions(pb, monkeypatch):
    log, _ = _install_fake_psycopg2(monkeypatch, [])
    monkeypatch.setattr(pb, "ro_url_from_credentials_file", lambda: None)
    with pytest.raises(SystemExit) as e:
        pb.fetch(args())
    assert "No read-only Neon URL" in str(e.value) and "--rows" in str(e.value)
    assert log == []                                                      # nothing was connected


def test_fetch_reads_the_rows_file_when_given_and_never_connects(pb, monkeypatch, tmp_path):
    log, _ = _install_fake_psycopg2(monkeypatch, [])
    f = tmp_path / "rows.json"
    f.write_text(json.dumps([row("A")]), encoding="utf-8")
    assert [r["title"] for r in pb.fetch(args(rows=str(f)))] == ["A"]
    assert log == []


# --- the credentials reader, on a SYNTHETIC copy (never the real file) --------------------------------------------

def _copy_with_synthetic_credentials(tmp_path, content):
    tools = tmp_path / "GH" / "Automated DJ Mixes" / "Tools"
    tools.mkdir(parents=True)
    copy = tools / "playlist_builder.py"
    copy.write_bytes(REAL_SOURCE.read_bytes())
    cred = tmp_path / "GH" / "Credentials"
    if content is not None:
        cred.mkdir()
        (cred / "Credentials.txt").write_bytes(content)
    mod = _load(copy, f"playlist_builder_copy_{abs(hash(str(tmp_path)))}")
    resolved = os.path.abspath(os.path.join(os.path.dirname(mod.__file__), "..", "..", "Credentials", "Credentials.txt"))
    assert resolved.startswith(str(tmp_path))                             # the copy can only ever see the synthetic file
    return mod


def test_credentials_reader_finds_the_key_in_a_synthetic_file(tmp_path):
    mod = _copy_with_synthetic_credentials(
        tmp_path, b"SOMETHING=1\r\nMIX_PLANNER_RO_DATABASE_URL=postgresql://fake.invalid/db\r\nOTHER=2\r\n")
    assert mod.ro_url_from_credentials_file() == "postgresql://fake.invalid/db"


def test_credentials_reader_returns_none_without_the_key_or_the_file(tmp_path):
    mod = _copy_with_synthetic_credentials(tmp_path / "a", b"SOMETHING=1\n# MIX_PLANNER_RO_DATABASE_URL=commented\n")
    assert mod.ro_url_from_credentials_file() is None
    mod2 = _copy_with_synthetic_credentials(tmp_path / "b", None)
    assert mod2.ro_url_from_credentials_file() is None


# --- manifest: requested vs built, pool counts, spreads, repeats, cached grid, --credible ----------------------------------
# Earlier mixes and gate logs are synthetic folders under tmp_path (names with spaces); nothing reads this machine's real disk.

EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)


def make_mix(root, name, wavs, logs=None, overrides=None, excluded=(), held=(), mtime=None):
    """A synthetic earlier mix. logs = {relative path: lines or bytes}; overrides = Hints/grid_overrides.json (object or raw text)."""
    mix = root / name
    (mix / "Audio").mkdir(parents=True)
    for sub, names in (("Audio", wavs), ("_Excluded Audio", excluded), ("Held Back", held)):
        (mix / sub).mkdir(exist_ok=True)
        for w in names: (mix / sub / w).touch()
    for rel, lines in (logs or {}).items():
        p = mix / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(lines if isinstance(lines, bytes) else ("\n".join(lines) + "\n").encode("utf-8"))
        if mtime: os.utime(p, (mtime, mtime))
    if overrides is not None:
        (mix / "Hints").mkdir()
        p = mix / "Hints" / "grid_overrides.json"
        p.write_text(overrides if isinstance(overrides, str) else json.dumps(overrides), encoding="utf-8")
        if mtime: os.utime(p, (mtime, mtime))
    return mix


def _real_gate_format():
    """The f-string validate_beatgrid.py really prints a verdict with, so these tests build their lines with it (format drift fails here)."""
    src = (ROOT / "Source" / "validate_beatgrid.py").read_text(encoding="utf-8")
    m = re.search(r'print\((f"  \[\{c\.verdict:4s\}\][^\n]*)\)\n', src)
    assert m, "the gate's print line moved: update _real_gate_format"
    return m.group(1)


def gate_line(verdict, name, detail, stats="R=0.50 ph=advisory"):
    c = types.SimpleNamespace(verdict=verdict, track=name, detail=detail)       # check_grid sets track = the file name WITH its extension
    return eval(_real_gate_format(), {}, {"c": c, "stats": stats})


PASS_DETAIL = "stem-kick grid (whole-mix R=0.50 is not the ruler; grid_vs_kick 4.1ms - sits on the transients)"
FAIL_DETAIL = "stem grid is 127ms OFF its own kicks (>15ms) - the detector can't grid this track"


def prow(d):
    """row() from a dict that carries the title."""
    return row(d["title"], **{k: v for k, v in d.items() if k != "title"})


def load_mixes(pb, *roots):
    dirs = pb.mix_dirs([str(r) for r in roots])
    return dirs, pb.read_earlier_mixes(dirs)


def run_manifest(pb, rows, a, roots=(), **kw):
    drops = {}
    pool = pb.prepare(rows, a, drops)
    picked = pb.build(rows, a)
    dirs, (mixes, bad, unparsed) = load_mixes(pb, *roots)
    return pb.manifest(rows, pool, drops, picked, a, mixes, bad, pb.read_grid_cache(dirs), unparsed, **kw), picked


def grid_of(pb, r, *roots):
    return pb.grid_for(r, pb.read_grid_cache(pb.mix_dirs([str(x) for x in roots])))


def repeats_of(pb, r, *roots):
    return pb.repeats_in(r, load_mixes(pb, *roots)[1][0])


def test_manifest_first_line_built_against_requested(pb):
    lines, picked = run_manifest(pb, ladder_rows(), args(n=7))
    assert lines[0] == "built 7 of 7 requested" and len(picked) == 7


def test_manifest_short_build_says_pool_too_small(pb):
    lines, _ = run_manifest(pb, ladder_rows()[:2], args(n=5))
    assert lines[0] == "built 2 of 5 requested: pool too small (2 usable tracks after the filters)"


def test_short_build_blames_max_per_artist_only_when_that_is_the_cause(pb):
    rows = [row(f"T{i}", artist="Same", genre="House", streams=1000 + i) for i in range(6)]
    assert run_manifest(pb, rows, args(n=4))[0][0] == "built 1 of 4 requested: max-per-artist 1 leaves 1 pickable"
    assert "repeat rows" not in run_manifest(pb, rows, args(n=4))[0][0]


def test_short_build_blames_repeat_rows_when_that_is_the_cause(pb):
    rows = [row("Same Song", artist=f"A{i}", genre="House", streams=1000 + i) for i in range(6)]       # six rows, one version
    first = run_manifest(pb, rows, args(n=4))[0][0]
    assert first == "built 1 of 4 requested: repeat rows of one version leave 1 distinct versions"
    assert "max-per-artist" not in first


def test_short_build_names_both_causes_when_both_apply(pb):
    rows = [row("Same Song", artist="A", streams=1000 + i) for i in range(3)] + [row(f"T{i}", artist="B", streams=900 + i) for i in range(3)]
    first = run_manifest(pb, rows, args(n=5))[0][0]
    assert "leave 4 distinct versions" in first and "max-per-artist 1 leaves 2 pickable" in first


def test_manifest_empty_pool_and_n_of_one(pb):
    lines, picked = run_manifest(pb, [], args(n=3))
    assert picked == [] and lines[0].startswith("built 0 of 3 requested: pool too small (0 usable")
    assert "dropped: nothing" in " ".join(lines) and "heat curve, picked order: -" in lines
    lines, picked = run_manifest(pb, ladder_rows(), args(n=1))
    assert lines[0] == "built 1 of 1 requested" and len(picked) == 1


def test_manifest_n_zero_is_built_zero_of_zero(pb):
    assert run_manifest(pb, ladder_rows(), args(n=0))[0][0] == "built 0 of 0 requested"


def test_manifest_lists_the_filters_as_given(pb):
    lines, _ = run_manifest(pb, ladder_rows(), args(label=["Defected", "DFTD"], artist=["Eli"], year_from=2015, year_to=2020,
                                                    min_streams=5000, heat_min=2, heat_max=8, preset="cool", arc="wave", n=3,
                                                    max_per_artist=2, credible=True))
    f = lines[1]
    for part in ("label=Defected,DFTD", "artist=Eli", "year=2015-2020", "min_streams=5000", "heat=2-8", "preset=cool", "arc=wave",
                 "max_per_artist=2", "n=3", "credible=True"):
        assert part in f


def test_prepare_drops_count_each_reason_and_leave_the_pool_unchanged(pb):
    rows = [row("Song (Acapella)"), row("Odd", genre="Polka"), row("Hard", genre="Techno (Peak Time / Driving)"),
            row("Fast", bpm=170), row("Old", release_date="2001-01-01"), row("Undated", release_date=None), row("Fine")]
    a = args(heat_max=7, year_from=2010)
    drops = {}
    assert [r["title"] for r in pb.prepare(rows, a, drops)] == ["Fine"]
    assert drops == {"not a playlist track": 1, "genre not on the ladder": 1, "heat outside range": 1, "tempo outside band": 1,
                     "year filter": 2}
    assert pb.prepare(rows, a) == pb.prepare(rows, a, {})            # drops is optional and changes nothing


def test_manifest_pool_line_and_coverage_counts(pb):
    rows = [row("A", artist="A1", bpm=124, musical_key="8A"), row("B", artist="B1", genre=None, release_date=None), row("C", genre="Polka")]
    lines, _ = run_manifest(pb, rows, args(n=3))
    assert "pool: 3 rows fetched; 1 after prepare; dropped: genre not on the ladder 2" in lines
    assert "coverage of the 3 rows: genre 2, bpm 1, musical_key 1, release_date 2" in lines


def test_manifest_rows_missing_release_date_and_genre_do_not_crash(pb):
    rows = [row("A", artist="A1", release_date=None), row("B", artist="B1", genre=None), {"artist": "X", "title": "Y", "streams_plays": 5}]
    lines, picked = run_manifest(pb, rows, args(n=3))
    assert len(picked) == 1 and "era spread, picked: undated 1" in lines


@pytest.mark.parametrize("bad_date", ["n.d.", 2018, 20180501, "", None, "abcd-01-01", "0000-01-01", ["2018"]])
def test_era_treats_an_unparseable_release_date_as_undated(pb, bad_date):
    assert pb.era(row("A", release_date=bad_date)) == "undated"


def test_manifest_survives_unparseable_dates_on_unpicked_pool_rows(pb):
    # the manifest reads every pool row, so a date that cannot be read must count as undated, not crash it
    rows = [row("Good", artist="G", release_date="2018-05-01", streams=100), row("Dateless", artist="D", release_date="n.d.", streams=5),
            row("Numeric", artist="N", release_date=2018, streams=4)]
    a = args(n=1)
    pool = pb.prepare(rows, a)                              # prepare() does not read dates without a year filter; build() itself would
    lines = pb.manifest(rows, pool, {}, pool[:1], a)
    assert "era spread, pool: undated 2, 2015-2019 1" in lines and "era spread, picked: 2015-2019 1" in lines


def test_manifest_spreads_show_a_flat_pick_against_a_narrow_pool(pb):
    rows = [row(f"T{i}", artist=f"A{i}", genre="Tech House", streams=1000 + i, release_date=f"20{10 + i}-01-01") for i in range(6)]
    lines, _ = run_manifest(pb, rows, args(n=4))
    assert "genre spread, picked: Tech House 4 (1 distinct of 1 in the pool)" in lines
    assert any(l.startswith("era spread, picked: ") for l in lines) and any(l.startswith("era spread, pool: ") for l in lines)
    lines, _ = run_manifest(pb, ladder_rows(), args(n=7))
    assert any(l.startswith("genre spread, picked") and l.endswith("(7 distinct of 7 in the pool)") for l in lines)


def test_manifest_heat_curve_matches_the_report(pb):
    lines, picked = run_manifest(pb, ladder_rows(), args(n=7))
    assert "heat curve, picked order: " + " ".join(str(r["heat"]) for r in picked) in lines


def test_manifest_preset_any_note_unless_credible(pb):
    note = "preset any ranks by stream percentile across all genres; use --credible --preset cool for a coherent set"
    noted = lambda a: [l for l in run_manifest(pb, ladder_rows(), a)[0] if "ranks by stream percentile" in l]
    assert noted(args(n=3)) == [note]                                  # exactly that one line, not embedded in a longer one
    assert noted(args(n=3, preset="cool", credible=True)) == [] and noted(args(n=3, preset="cool")) == []


def test_manifest_pick_lines_and_summary_show_the_version(pb, tmp_path):
    rows = [row("Mr. Bongo", artist="Mr. V", streams=2000), row("Mr. Bongo", artist="Mr. V", remixer="Butch", streams=1000)]
    make_mix(tmp_path, "Mix A", ["Mr. V - Mr. Bongo (Butch Remix).wav"],
             logs={"Output/relay.log": [gate_line("FAIL", "Mr. V - Mr. Bongo (Butch Remix).wav", FAIL_DETAIL)]})
    lines, picked = run_manifest(pb, rows, args(n=2, max_per_artist=2), roots=[tmp_path])
    pick_lines = [l for l in lines if l.startswith("  #")]
    assert len(pick_lines) == 2
    assert [l for l in pick_lines if "(original/unspecified)" in l][0].endswith("grid: unknown (no cached verdict)")
    assert [l for l in pick_lines if "(Butch remix)" in l][0].endswith("grid: FAIL 127.0 ms (cached) [Mix A]; repeat of: Mix A")
    assert lines[-1].endswith("CACHED GRID FAIL: Mr. V - Mr. Bongo (Butch remix) (127.0 ms)")


# --- repeats --------------------------------------------------------------------------------------------------------------

def test_repeats_not_checked_when_no_earlier_mix_is_readable(pb, tmp_path):
    lines, _ = run_manifest(pb, ladder_rows(), args(n=3), roots=[tmp_path / "nowhere"])
    assert [l for l in lines if l.startswith("repeats")] == ["repeats: not checked (no earlier mix list found)"]
    assert "(not checked)" in lines[-1]


def test_repeats_flag_a_pick_named_with_the_mix_and_print_the_coverage_count(pb, tmp_path):
    root = tmp_path / "Earlier Mixes"
    make_mix(root, "24.11.26 Fresh Mix", ["Artist 5 - Track Tech House.wav", "Other - Elsewhere SW V2.wav"])
    make_mix(root, "21.11.26 Second Mix", ["Nobody - Nothing 24 Bit MASTER.wav"])
    lines, picked = run_manifest(pb, ladder_rows(), args(n=7), roots=[root])
    assert "repeats: 1 of 7 picks were in an earlier mix (checked against 2 earlier mixes)" in lines
    assert [l for l in lines if "Track Tech House" in l and l.startswith("  #")][0].endswith("repeat of: 24.11.26 Fresh Mix")
    assert len([l for l in lines if "repeat of:" in l]) == 1


def test_repeats_zero_when_mixes_are_readable_but_nothing_repeats(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Nobody - Nothing.wav"])
    lines, _ = run_manifest(pb, ladder_rows(), args(n=3), roots=[tmp_path])
    assert "repeats: 0 of 3 picks were in an earlier mix (checked against 1 earlier mixes)" in lines


def test_unreadable_count_prints_even_when_no_mix_was_readable(pb, tmp_path):
    (tmp_path / "Mix Empty" / "Audio").mkdir(parents=True)                  # the Audio folder exists but holds no wav
    (tmp_path / "Mix Empty Too" / "Audio").mkdir(parents=True)
    lines, _ = run_manifest(pb, ladder_rows(), args(n=3), roots=[tmp_path])
    assert "repeats: not checked (no earlier mix list found; 2 unreadable)" in lines


def test_unreadable_count_prints_beside_readable_mixes(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Nobody - Nothing.wav"])
    (tmp_path / "Mix Empty" / "Audio").mkdir(parents=True)
    lines, _ = run_manifest(pb, ladder_rows(), args(n=3), roots=[tmp_path])
    assert "repeats: 0 of 3 picks were in an earlier mix (checked against 1 earlier mixes; 1 unreadable)" in lines


def test_unparseable_file_names_are_counted_and_printed(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["no separator here.wav", "notes.txt", " - .wav", "Artist - Song.wav", "Another one.wav"])
    mixes, bad, unparsed = pb.read_earlier_mixes(pb.mix_dirs([str(tmp_path)]))
    assert len(mixes[0][1]) == 1 and bad == 0 and unparsed == 3             # notes.txt is not a wav, so it is not counted
    lines, _ = run_manifest(pb, ladder_rows(), args(n=3), roots=[tmp_path])
    assert "repeats: 0 of 3 picks were in an earlier mix (checked against 1 earlier mixes; 3 file names could not be read)" in lines


def test_unlistable_audio_folder_is_counted_as_unreadable(pb, tmp_path, monkeypatch):
    make_mix(tmp_path, "Mix A", ["Artist - Song.wav"])
    make_mix(tmp_path, "_Corpus", ["Artist - Song.wav"])                  # names starting with _ are corpora, not mixes
    mixes, bad, _ = pb.read_earlier_mixes(pb.mix_dirs([str(tmp_path)]))
    assert [m[0] for m in mixes] == ["Mix A"] and bad == 0
    monkeypatch.setattr(pb, "wavs_in", lambda d: None)                    # the Audio folder cannot be listed
    mixes, bad, _ = pb.read_earlier_mixes(pb.mix_dirs([str(tmp_path)]))
    assert mixes == [] and bad == 1


def test_missing_explicit_path_is_reported_in_the_repeats_line(pb, tmp_path):
    drops = {}
    a = args(n=3)
    rows = ladder_rows()
    pool, picked = pb.prepare(rows, a, drops), pb.build(rows, a)
    lines = pb.manifest(rows, pool, drops, picked, a, None, 0, None, 0, pb.root_problems([str(tmp_path / "typo")]))
    assert f"repeats: not checked (no earlier mix list found; path not found: {tmp_path / 'typo'})" in lines


def test_a_remix_and_an_original_are_different_versions_not_repeats(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Mr. V - Mr. Bongo (Butch Remix).wav", "Artist - Plain Song (Extended Mix) SW V3.wav"])
    orig, remix = row("Mr. Bongo", artist="Mr. V"), row("Mr. Bongo", artist="Mr. V", remixer="Butch")
    plain, plain_other_remix = row("Plain Song", artist="Artist"), row("Plain Song", artist="Artist", remixer="Bob")
    assert repeats_of(pb, orig, tmp_path) == []
    assert repeats_of(pb, remix, tmp_path) == ["Mix A"]
    assert repeats_of(pb, plain, tmp_path) == ["Mix A"]                       # extended mix is the original version
    assert repeats_of(pb, plain_other_remix, tmp_path) == []


def test_repeat_match_needs_a_word_boundary_and_a_title_made_of_letters(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Artist - Taste Of Honey.wav", "Artist - !!!.wav"])
    assert repeats_of(pb, row("Taste", artist="Artist"), tmp_path) == []      # "Taste" is not "Taste Of Honey"
    assert repeats_of(pb, row("!!!", artist="Artist"), tmp_path) == []        # punctuation-only title never matches anything
    assert repeats_of(pb, row("", artist="Artist"), tmp_path) == []


def test_repeat_match_folds_accents_and_ignores_the_delivery_suffix(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Micha" + chr(0xEB) + "l Canitrot - Renaissance (Extended Mix) 24 Bit MASTER AMENDED V3.wav", "10A - Duo - Song SW V1.wav"])
    assert repeats_of(pb, row("Renaissance", artist="Michael Canitrot"), tmp_path) == ["Mix A"]
    assert repeats_of(pb, row("Renaissance", artist="Micha" + chr(0xEB) + "l Canitrot"), tmp_path) == ["Mix A"]
    assert repeats_of(pb, row("Song", artist="Duo"), tmp_path) == ["Mix A"]    # a leading key prefix is ignored


def test_non_latin_artist_names_do_not_crash_or_match(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Кино - Группа крови.wav"])
    assert repeats_of(pb, row("Группа крови", artist="Кино"), tmp_path) == []


# file name shapes that used to be missed (each must now repeat), and the version pairs that must NEVER repeat
@pytest.mark.parametrize("fname,pick", [
    ("Aa " + EN_DASH + " Bb.wav", dict(artist="Aa", title="Bb")),
    ("Aa -Bb.wav", dict(artist="Aa", title="Bb")),
    ("01 - Aa - Bb.wav", dict(artist="Aa", title="Bb")),
    ("7. Aa - Bb.wav", dict(artist="Aa", title="Bb")),
    ("Aa - Bb (Bob Extended Remix).wav", dict(artist="Aa", title="Bb", mix_name="Bob Remix", remixer="Bob")),
    ("Aa - Bb (Bob Remix).wav", dict(artist="Aa", title="Bb", mix_name="Bob Extended Remix", remixer="Bob")),
    ("Aa - Bb (Original Mix) UPDATE 12.02.26.wav", dict(artist="Aa", title="Bb")),
    ("Aa - Bb UPDATE 12.02.26.wav", dict(artist="Aa", title="Bb")),
    ("Miguel Migs - Sail On (feat. Jason Brock).wav", dict(artist="Miguel Migs", title="Sail On")),
    ("Miguel Migs - Sail On.wav", dict(artist="Miguel Migs", title="Sail On (feat. Jason Brock)")),
    ("Ridney & Richard Earnshaw - Roadblock VIP.wav", dict(artist="Ridney & Richard Earnshaw", title="Roadblock", mix_name="VIP Mix")),
    ("Ridney & Richard Earnshaw - Roadblock (VIP Mix).wav", dict(artist="Ridney & Richard Earnshaw", title="Roadblock", mix_name="VIP")),
    ("Aa - Bb (Extended Mix).wav", dict(artist="Aa", title="Bb", mix_name="Original Mix")),
])
def test_repeat_matches_the_name_shapes_that_used_to_be_missed(pb, tmp_path, fname, pick):
    make_mix(tmp_path, "Mix A", [fname])
    assert repeats_of(pb, prow(pick), tmp_path) == ["Mix A"]


@pytest.mark.parametrize("fname,pick", [
    ("Aa - Bb (Bob Remix).wav", dict(artist="Aa", title="Bb")),                                    # remix file, original pick
    ("Aa - Bb (Original Mix).wav", dict(artist="Aa", title="Bb", mix_name="Bob Remix", remixer="Bob")),   # original file, remix pick
    ("Aa - Bb (Bob Extended Remix).wav", dict(artist="Aa", title="Bb")),
    ("Aa - Bb (Bob Extended Remix).wav", dict(artist="Aa", title="Bb", mix_name="Cat Remix", remixer="Cat")),   # another remixer
    ("Aa - Bb (Extended Mix).wav", dict(artist="Aa", title="Bb", mix_name="Bob Remix", remixer="Bob")),
    ("Aa - Bb (Extended Remix).wav", dict(artist="Aa", title="Bb")),                               # a remix with no name is still a remix
    ("Aa - Bb VIP.wav", dict(artist="Aa", title="Bb")),
    ("Aa - Bb (Dub Mix).wav", dict(artist="Aa", title="Bb")),
])
def test_repeat_never_matches_a_different_version(pb, tmp_path, fname, pick):
    make_mix(tmp_path, "Mix A", [fname])
    assert repeats_of(pb, prow(pick), tmp_path) == []


# --- artist rule: equality of the SET of whole names, never a token subset -----------------------------------------------

@pytest.mark.parametrize("fname,artist", [
    ("DJ Koze - Song.wav", "DJ"), ("The Martinez Brothers - Song.wav", "The"), ("Jimpster & Crackazat - Song.wav", "Crackazat"),
    ("Crackazat - Song.wav", "Jimpster & Crackazat"), ("Aa x Bb x Cc - Song.wav", "Aa x Bb"), ("Aa - Song.wav", "Aa feat. Bb"),
])
def test_artist_must_be_the_same_set_of_names_not_a_subset(pb, tmp_path, fname, artist):
    make_mix(tmp_path, "Mix A", [fname])
    assert repeats_of(pb, row("Song", artist=artist), tmp_path) == []


@pytest.mark.parametrize("fname,artist", [
    ("Ridney, Richard Earnshaw - Song.wav", "Ridney & Richard Earnshaw"), ("Ridney & Richard Earnshaw - Song.wav", "Ridney, Richard Earnshaw"),
    ("Ridney and Richard Earnshaw - Song.wav", "Ridney & Richard Earnshaw"), ("Bb x Aa - Song.wav", "Aa x Bb"),
    ("Aa feat. Bb - Song.wav", "Aa ft Bb"), ("DJ Koze - Song.wav", "dj koze"), ("Aa vs. Bb - Song.wav", "Aa & Bb"),
])
def test_artist_credit_spellings_with_the_same_names_still_match(pb, tmp_path, fname, artist):
    make_mix(tmp_path, "Mix A", [fname])
    assert repeats_of(pb, row("Song", artist=artist), tmp_path) == ["Mix A"]


# --- mix roots: explicit roots replace the defaults; a root may be one mix or a folder of mixes --------------------------

def test_explicit_roots_replace_the_default_roots_in_main(pb, monkeypatch, capsys, tmp_path):
    make_mix(tmp_path / "Defaults", "Default Mix", ["Artist - Song.wav"])
    make_mix(tmp_path / "Mine", "My Mix", ["Other - Elsewhere.wav"])
    monkeypatch.setattr(pb, "default_mix_roots", lambda: [str(tmp_path / "Defaults")])
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([row("Song", artist="Artist")]), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1")
    assert "repeats: 1 of 1 picks were in an earlier mix (checked against 1 earlier mixes)" in capsys.readouterr().out
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1", "--mixes-dir", str(tmp_path / "Mine"))
    out = capsys.readouterr().out
    assert "repeats: 0 of 1 picks were in an earlier mix (checked against 1 earlier mixes)" in out and "repeat of:" not in out
    monkeypatch.setenv("DJMIX_MIX_ROOTS", str(tmp_path / "Mine"))
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1")
    assert "repeats: 0 of 1 picks were in an earlier mix (checked against 1 earlier mixes)" in capsys.readouterr().out


def test_mixes_dir_accepts_one_mix_project_as_well_as_a_folder_of_mixes(pb, tmp_path):
    mix = make_mix(tmp_path / "Folder Of Mixes", "Mix A", ["Artist - Song.wav"])
    make_mix(tmp_path / "Folder Of Mixes", "Mix B", ["Artist - Song.wav"])
    assert [os.path.basename(d) for d in pb.mix_dirs([str(mix)])] == ["Mix A"]
    assert [os.path.basename(d) for d in pb.mix_dirs([str(tmp_path / "Folder Of Mixes")])] == ["Mix A", "Mix B"]
    assert pb.mix_dirs([str(mix), str(tmp_path / "Folder Of Mixes")]) == pb.mix_dirs([str(tmp_path / "Folder Of Mixes")])   # no double count


def test_explicit_mix_roots_come_from_the_flag_and_the_environment(pb, monkeypatch):
    monkeypatch.setenv("DJMIX_MIX_ROOTS", os.pathsep.join(["/env/one", "/env/two"]))
    assert pb.explicit_mix_roots(args(mixes_dir=["/flag"])) == ["/flag", "/env/one", "/env/two"]
    monkeypatch.delenv("DJMIX_MIX_ROOTS")
    assert pb.explicit_mix_roots(args()) == []


def test_main_reports_a_missing_mixes_dir_path(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([row()]), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1", "--mixes-dir", str(tmp_path / "typo"))
    assert f"repeats: not checked (no earlier mix list found; path not found: {tmp_path / 'typo'})" in capsys.readouterr().out


# --- worktree detection ---------------------------------------------------------------------------------------------------

def _worktree(tmp_path, gitdir_line, encoding="utf-8", prefix=b""):
    here = tmp_path / "wt" / "Automated DJ Mixes"
    here.mkdir(parents=True)
    (here / ".git").write_bytes(prefix + gitdir_line.encode(encoding))
    return str(here)


def test_main_checkout_from_an_absolute_gitdir(pb, tmp_path):
    main = tmp_path / "main repo"
    here = _worktree(tmp_path, f"gitdir: {main / '.git' / 'worktrees' / 'dj-pick'}\n")
    assert os.path.normcase(pb.main_checkout(here)) == os.path.normcase(str(main))


def test_main_checkout_from_a_relative_gitdir(pb, tmp_path):
    here = _worktree(tmp_path, "gitdir: ../../main repo/.git/worktrees/dj-pick\n")
    assert os.path.normcase(pb.main_checkout(here)) == os.path.normcase(str(tmp_path / "main repo"))


def test_main_checkout_survives_a_bom(pb, tmp_path):
    main = tmp_path / "main repo"
    here = _worktree(tmp_path, f"gitdir: {main / '.git' / 'worktrees' / 'x'}\n", prefix=b"\xef\xbb\xbf")
    assert os.path.normcase(pb.main_checkout(here)) == os.path.normcase(str(main))


@pytest.mark.parametrize("content", [b"gitdir: /nowhere/not-a-worktree\n", b"gitdir: /a/.git\n", b"not a gitdir line\n", b"", b"\xff\xfe\x00 bad bytes"])
def test_main_checkout_gives_none_for_anything_else_without_raising(pb, tmp_path, content):
    here = tmp_path / "wt"
    here.mkdir()
    (here / ".git").write_bytes(content)
    assert pb.main_checkout(str(here)) is None


def test_main_checkout_is_none_in_a_normal_checkout_where_dot_git_is_a_folder(pb, tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert pb.main_checkout(str(tmp_path / "repo")) is None
    assert pb.main_checkout(str(tmp_path / "no such folder")) is None


def test_default_roots_include_the_main_checkout_test_project_in_a_worktree(pb, monkeypatch, tmp_path):
    fresh = _load(REAL_SOURCE, "playlist_builder_fresh")                       # an unpatched copy: the fixture's guards stay in place
    main = tmp_path / "main repo"
    here = tmp_path / "wt" / "Automated DJ Mixes"
    (here / "Tools").mkdir(parents=True)
    (here / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'x'}\n", encoding="utf-8")
    monkeypatch.setattr(fresh, "__file__", str(here / "Tools" / "playlist_builder.py"))
    roots = [os.path.normcase(r) for r in fresh.default_mix_roots()]
    assert roots[0] == os.path.normcase(str(main / "Test Project")) and os.path.normcase(str(here / "Test Project")) in roots


# --- cached beat-grid verdicts --------------------------------------------------------------------------------------------

GRID_ROWS = [row("Pass Track", artist="Ann"), row("Fail Track", artist="Bob", genre="Tech House"), row("New Track", artist="Cat", genre="Deep House")]


def test_grid_pass_fail_and_unknown_from_earlier_gate_logs(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Ann - Pass Track (Extended Mix) SW V2.wav"], excluded=["Bob - Fail Track SW V1.wav"], logs={"Output/relay.log": [
        "Beatgrid verification (whole-track onset-vs-grid sweep test)...",
        gate_line("PASS", "Ann - Pass Track (Extended Mix) SW V2.wav", PASS_DETAIL),
        gate_line("FAIL", "Bob - Fail Track SW V1.wav", FAIL_DETAIL)]})
    lines, _ = run_manifest(pb, GRID_ROWS, args(n=3, max_per_artist=3), roots=[tmp_path])
    picks = {l.strip().split(": ")[0].split(" ", 1)[1]: l for l in lines if l.startswith("  #")}
    assert "grid: PASS 4.1 ms (cached) [Mix A]" in picks["Ann - Pass Track (original/unspecified)"]
    assert "grid: FAIL 127.0 ms (cached) [Mix A]" in picks["Bob - Fail Track (original/unspecified)"]
    assert "grid: unknown (no cached verdict)" in picks["Cat - New Track (original/unspecified)"]
    assert "grid known for 2 of 3 picks" in lines
    assert lines[-1].endswith("CACHED GRID FAIL: Bob - Fail Track (original/unspecified) (127.0 ms)")


def test_grid_unknown_is_never_printed_as_a_pass_when_other_tracks_have_verdicts(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Someone - Else.wav", "Other - Thing.wav"], logs={"Output/relay.log": [
        gate_line("PASS", "Someone - Else.wav", PASS_DETAIL), gate_line("FAIL", "Other - Thing.wav", FAIL_DETAIL)]})
    lines, _ = run_manifest(pb, GRID_ROWS, args(n=3), roots=[tmp_path])
    pick_lines = [l for l in lines if l.startswith("  #")]
    assert len(pick_lines) == 3 and all("grid: unknown (no cached verdict)" in l for l in pick_lines)
    assert not any("grid: PASS" in l or "grid: FAIL" in l or "CACHED GRID FAIL" in l for l in lines)
    assert "grid known for 0 of 3 picks (cache has 2 verdicts, none for these picks)" in lines


def test_grid_unknown_is_never_printed_as_a_pass(pb, tmp_path):
    lines, _ = run_manifest(pb, GRID_ROWS, args(n=3), roots=[tmp_path])
    assert "grid known for 0 of 3 picks (no earlier gate output found)" in lines
    pick_lines = [l for l in lines if l.startswith("  #")]
    assert len(pick_lines) == 3 and all("grid: unknown (no cached verdict)" in l for l in pick_lines)
    assert not any("grid: PASS" in l or "CACHED GRID FAIL" in l for l in lines)


def test_grid_line_says_when_the_cache_exists_but_covers_none_of_the_picks(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Someone - Else.wav"], logs={"Output/relay.log": [gate_line("PASS", "Someone - Else.wav", PASS_DETAIL)]})
    lines, _ = run_manifest(pb, GRID_ROWS, args(n=3), roots=[tmp_path])
    assert "grid known for 0 of 3 picks (cache has 1 verdicts, none for these picks)" in lines


# the three places the gate lines really live on this machine: Output/*.log, <mix>/*.txt, Output/*.txt
@pytest.mark.parametrize("rel", ["Output/relay.log", "phase1a_log.txt", "Output/phase1a_log.txt", "run.log"])
def test_grid_reads_gate_lines_wherever_earlier_runs_left_them(pb, tmp_path, rel):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={rel: [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)]})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) == ("PASS", 4.1, "Mix A", "log")


def test_grid_ignores_a_log_that_is_a_folder_or_unreadable(pb, tmp_path):
    mix = make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/ok.log": [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)]})
    (mix / "Output" / "dir.log").mkdir()
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[0] == "PASS"


LONG_PREFIX = "Zqlongartist - Zqa very long title that keeps going on"          # exactly 54 chars: the gate cuts here
LONG_ORIG, LONG_RMX = LONG_PREFIX + " (Original Mix).wav", LONG_PREFIX + " (Zqbob Remix).wav"
LONG_ORIG_ROW = dict(artist="Zqlongartist", title="Zqa very long title that keeps going on", mix_name="Original Mix")
LONG_RMX_ROW = dict(artist="Zqlongartist", title="Zqa very long title that keeps going on", mix_name="Zqbob Remix", remixer="Zqbob")


def test_the_long_name_fixture_really_is_cut_by_the_gate():
    # built with the gate's own format string: if validate_beatgrid.py changes how it prints the name, this fails
    line = gate_line("PASS", LONG_ORIG, "x")
    assert len(LONG_PREFIX) == 54 and line[9:63] == LONG_PREFIX and line[63:65] == "  "
    assert re.search(r"name = Path\(audio_path\)\.name", (ROOT / "Source" / "validate_beatgrid.py").read_text(encoding="utf-8"))   # name keeps .wav


def test_cut_name_shared_by_an_original_and_a_remix_is_unknown_for_both(pb, tmp_path):
    # both files are in Audio and start with the 54 chars the gate printed: it is not known whose verdict this is
    make_mix(tmp_path, "Mix A", [LONG_ORIG, LONG_RMX], logs={"Output/a.log": [gate_line("FAIL", LONG_RMX, FAIL_DETAIL)]})
    assert grid_of(pb, prow(LONG_ORIG_ROW), tmp_path) is None
    assert grid_of(pb, prow(LONG_RMX_ROW), tmp_path) is None


def test_cut_name_not_resolvable_to_a_file_is_unknown_never_an_original(pb, tmp_path):
    make_mix(tmp_path, "Mix A", [], logs={"Output/a.log": [gate_line("PASS", LONG_RMX, PASS_DETAIL)]})   # the remix's PASS, no Audio to resolve it
    assert grid_of(pb, row("Zqa very long title that keeps going on", artist="Zqlongartist", mix_name="Original Mix"), tmp_path) is None
    assert grid_of(pb, row("Zqa very long title that keeps going on", artist="Zqlongartist", mix_name="Zqbob Remix", remixer="Zqbob"), tmp_path) is None


def test_cut_name_with_one_matching_file_is_resolved_even_via_two_folders(pb, tmp_path):
    name = "Harry Romero, Jose Nunez, Alex Alicea & Shawnee Taylor - I Wanna Thank You 24 Bit MASTER.wav"
    make_mix(tmp_path, "Mix A", [name], excluded=[name], logs={"Output/a.log": [gate_line("PASS", name, PASS_DETAIL)]})   # same file seen twice
    r = row("I Wanna Thank You", artist="Harry Romero, Jose Nunez, Alex Alicea & Shawnee Taylor")
    assert grid_of(pb, r, tmp_path) == ("PASS", 4.1, "Mix A", "log")


def test_a_short_name_is_taken_as_it_is_and_a_remix_is_not_the_originals_result(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Ann - Song (Bob Remix).wav"], logs={"Output/a.log": [gate_line("FAIL", "Ann - Song (Bob Remix).wav", FAIL_DETAIL)]})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) is None
    assert grid_of(pb, row("Song", artist="Ann", remixer="Bob"), tmp_path)[0] == "FAIL"


def test_grid_newest_cached_result_wins(pb, tmp_path):
    old = make_mix(tmp_path, "Old Mix", ["Ann - Song.wav"], logs={"Output/relay.log": [gate_line("FAIL", "Ann - Song.wav", FAIL_DETAIL)]}, mtime=1_000_000)
    new = make_mix(tmp_path, "New Mix", ["Ann - Song.wav"], logs={"Output/relay.log": [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)]}, mtime=2_000_000)
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[0] == "PASS"


@pytest.mark.parametrize("order", [("PASS", "FAIL"), ("FAIL", "PASS")])
def test_a_tie_on_mtime_goes_to_the_worse_verdict_whatever_the_line_order(pb, tmp_path, order):
    details = {"PASS": PASS_DETAIL, "FAIL": FAIL_DETAIL}
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line(v, "Ann - Song.wav", details[v]) for v in order]}, mtime=1_500_000)
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[0] == "FAIL"


def test_a_tie_on_mtime_across_two_mixes_goes_to_the_worse_verdict(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)]}, mtime=1_500_000)
    make_mix(tmp_path, "Mix B", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line("FAIL", "Ann - Song.wav", FAIL_DETAIL)]}, mtime=1_500_000)
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[0] == "FAIL"


def test_grid_overrides_json_is_a_labelled_fallback_with_the_15_ms_rule(pb, tmp_path):
    make_mix(tmp_path, "Mix A", [], overrides={"Ann - Near.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 3.2},
                                                "Ann - Far.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 88.0},
                                                "Ann - Edge.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 15.0},
                                                "Ann - Other.wav": {"phase_source": "ableton-ticks", "grid_vs_kick_ms": 1.0}})
    assert grid_of(pb, row("Near", artist="Ann"), tmp_path) == ("PASS", 3.2, "Mix A", "override")
    assert grid_of(pb, row("Far", artist="Ann"), tmp_path)[:2] == ("FAIL", 88.0)
    assert grid_of(pb, row("Edge", artist="Ann"), tmp_path)[0] == "PASS"
    assert grid_of(pb, row("Other", artist="Ann"), tmp_path) is None          # only stem-kick grids carry the kick figure
    lines, _ = run_manifest(pb, [row("Near", artist="Ann")], args(n=1), roots=[tmp_path])
    assert [l for l in lines if l.startswith("  #")][0].endswith("grid: PASS 3.2 ms (override) [Mix A]")


def test_a_gate_log_verdict_beats_an_override_whatever_the_mtimes(pb, tmp_path):
    # the gate said FAIL at 15.4 ms; the override file (newer, re-saved) still holds 15.0, which the 15 ms rule would call a PASS
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/relay.log": [gate_line("FAIL", "Ann - Song.wav", "stem grid is 15ms OFF its own kicks (>15ms)")]},
             overrides={"Ann - Song.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 15.0}}, mtime=1_000_000)
    os.utime(tmp_path / "Mix A" / "Hints" / "grid_overrides.json", (2_000_000, 2_000_000))
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) == ("FAIL", 15.0, "Mix A", "log")
    lines, _ = run_manifest(pb, [row("Song", artist="Ann")], args(n=1), roots=[tmp_path])
    assert lines[-1].endswith("CACHED GRID FAIL: Ann - Song (original/unspecified) (15.0 ms)")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -90.0, -0.1, "4.1", True, None, [3]])
def test_grid_overrides_with_nan_inf_negative_or_odd_values_are_unknown(pb, tmp_path, value):
    make_mix(tmp_path, "Mix A", [], overrides={"Ann - Song.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": value}})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) is None


@pytest.mark.parametrize("content", ["{", "", "[1, 2]", "null", '{"Ann - Song.wav": 5}', '{"Ann - Song.wav": {"grid_vs_kick_ms": 2}}'])
def test_grid_corrupt_or_half_written_cache_files_give_unknown_not_a_crash(pb, tmp_path, content):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], overrides=content,
             logs={"Output/relay.log": ["  [PASS", gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)[:30], "garbage \x00 line"]})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) is None


def test_grid_log_with_invalid_bytes_is_still_read(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/relay.log": gate_line("PASS", "Ann - Song.wav", PASS_DETAIL).encode() + b"\n\xff\xfe bad bytes \x80\n"})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[0] == "PASS"


# --- main: the manifest comes first, stderr warns when short, --credible refuses preset any -------------------------------

def test_main_prints_the_manifest_before_the_track_rows_and_warns_on_stderr_when_short(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()[:3]), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "5")
    cap = capsys.readouterr()
    assert cap.out.startswith("built 3 of 5 requested: pool too small (3 usable tracks after the filters)\n")
    assert cap.out.index("summary:") < cap.out.index("# Playlist draft")
    assert "[warn] built 3 of 5 requested: pool too small" in cap.err
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "3")
    assert "[warn] built" not in capsys.readouterr().err


def test_main_mixes_dir_flag_feeds_the_repeat_and_grid_checks(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([row("Song", artist="Ann")]), encoding="utf-8")
    make_mix(tmp_path / "My Earlier Mixes", "Mix A", ["Ann - Song.wav"], logs={"Output/relay.log": [gate_line("FAIL", "Ann - Song.wav", FAIL_DETAIL)]})
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1", "--mixes-dir", str(tmp_path / "My Earlier Mixes"))
    out = capsys.readouterr().out
    assert "repeats: 1 of 1 picks were in an earlier mix (checked against 1 earlier mixes)" in out
    assert "  #1 Ann - Song (original/unspecified): grid: FAIL 127.0 ms (cached) [Mix A]; repeat of: Mix A" in out
    assert "CACHED GRID FAIL: Ann - Song (original/unspecified) (127.0 ms)" in out


def test_main_manifest_never_writes_a_file_or_reaches_the_network(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    def no_network(*a, **k): raise AssertionError("the manifest opened a socket")
    monkeypatch.setattr(socket, "socket", no_network)
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "4")
    capsys.readouterr()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rows.json"]


@pytest.mark.parametrize("preset", ["cool", "cult", "hits"])
def test_credible_accepts_the_coherent_presets(pb, monkeypatch, capsys, tmp_path, preset):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "3", "--credible", "--preset", preset)
    out = capsys.readouterr().out
    assert f"preset={preset}" in out and "credible=True" in out and "preset any ranks by stream percentile" not in out


@pytest.mark.parametrize("argv", [["build", "--credible", "--preset", "any"], ["build", "--credible"],
                                  ["sql", "--credible", "--preset", "any"], ["sql", "--credible"]])
def test_credible_refuses_preset_any_with_exit_2_and_a_one_line_reason(pb, monkeypatch, capsys, tmp_path, argv):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    cmd, *rest = argv
    with pytest.raises(SystemExit) as e:
        _run_main(pb, monkeypatch, cmd, *(["--rows", str(rows_file)] if cmd == "build" else []), *rest)
    assert e.value.code == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "--credible refuses --preset any" in cap.err and "use --preset cool, cult or hits" in cap.err


def test_sql_with_credible_and_a_real_preset_still_prints_the_select(pb, monkeypatch, capsys):
    _run_main(pb, monkeypatch, "sql", "--credible", "--preset", "cool")
    assert capsys.readouterr().out.lstrip().lower().startswith("select")


def test_without_credible_preset_any_still_works_and_says_why_it_is_not_coherent(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "3")
    assert "preset any ranks by stream percentile across all genres; use --credible --preset cool for a coherent set" in capsys.readouterr().out


def test_main_output_has_no_dash_characters_and_never_prints_the_database_url(pb, monkeypatch, capsys, tmp_path):
    canary = "postgresql://user:canary-secret-xyz@canary.invalid/db"
    monkeypatch.setenv("MIX_PLANNER_RO_DATABASE_URL", canary)
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "7")
    cap = capsys.readouterr()
    assert len(cap.out.splitlines()) > 20 and "built 7 of 7 requested" in cap.out
    for ch in (EN_DASH, EM_DASH):
        assert ch not in cap.out and ch not in cap.err
    assert "canary-secret-xyz" not in cap.out + cap.err and "canary.invalid" not in cap.out + cap.err


# --- round 2: double-space cut names, swapped files, override lowering, nesting, separators, stats shape -----------------------

A52 = "Zqlongartist - Zqa very long title that keeps goingx"          # 52 chars: with two spaces after it the gate's 54 chars rstrip to 52
T52 = A52.split(" - ", 1)[1]
DOUBLE_SPACE_RMX = A52 + "  (Zqbob Remix).wav"


def test_a_cut_name_that_rstrips_short_is_still_cut_not_whole(pb, tmp_path):
    assert len(A52) == 52 and gate_line("PASS", DOUBLE_SPACE_RMX, "x")[9:63].rstrip() == A52          # the gate shows 52 chars and no .wav
    make_mix(tmp_path, "Mix A", [], logs={"Output/a.log": [gate_line("PASS", DOUBLE_SPACE_RMX, PASS_DETAIL)]})       # remix file not in Audio
    assert grid_of(pb, row(T52, artist="Zqlongartist", mix_name="Original Mix"), tmp_path) is None
    assert grid_of(pb, row(T52, artist="Zqlongartist", mix_name="Zqbob Remix", remixer="Zqbob"), tmp_path) is None


def test_a_double_space_cut_name_resolves_to_the_remix_file_not_the_original(pb, tmp_path):
    make_mix(tmp_path, "Mix A", [DOUBLE_SPACE_RMX], logs={"Output/a.log": [gate_line("PASS", DOUBLE_SPACE_RMX, PASS_DETAIL)]})
    assert grid_of(pb, row(T52, artist="Zqlongartist", mix_name="Original Mix"), tmp_path) is None
    assert grid_of(pb, row(T52, artist="Zqlongartist", mix_name="Zqbob Remix", remixer="Zqbob"), tmp_path) == ("PASS", 4.1, "Mix A", "log")


def test_a_whole_name_ends_in_wav_whatever_its_length_or_case(pb, tmp_path):
    make_mix(tmp_path, "Mix A", [], logs={"Output/a.log": [gate_line("PASS", "Ann - Song.WAV", PASS_DETAIL)]})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[0] == "PASS"                    # no Audio file needed for a whole name


def test_a_cut_name_is_unknown_when_the_file_was_swapped_after_the_log_was_written(pb, tmp_path):
    # the remix's PASS is in an OLD log; the remix file is gone and the original (newer than the log) now has the same 54 chars
    make_mix(tmp_path, "Mix A", [LONG_ORIG], logs={"Output/a.log": [gate_line("PASS", LONG_RMX, PASS_DETAIL)]}, mtime=1_000_000)
    assert grid_of(pb, prow(LONG_ORIG_ROW), tmp_path) is None
    assert grid_of(pb, prow(LONG_RMX_ROW), tmp_path) is None


def test_a_cut_name_resolves_when_the_file_is_not_newer_than_the_log(pb, tmp_path):
    make_mix(tmp_path, "Mix A", [LONG_ORIG], logs={"Output/a.log": [gate_line("PASS", LONG_ORIG, PASS_DETAIL)]}, mtime=4_000_000_000)
    assert grid_of(pb, prow(LONG_ORIG_ROW), tmp_path) == ("PASS", 4.1, "Mix A", "log")


def test_an_older_log_pass_is_lowered_by_a_newer_override_fail_and_labelled(pb, tmp_path):
    ov = {"Ann - Song.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 88.0}}
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)]}, overrides=ov, mtime=1_000_000)
    os.utime(tmp_path / "Mix A" / "Hints" / "grid_overrides.json", (2_000_000, 2_000_000))
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) == ("FAIL", 88.0, "Mix A", "override")
    lines, _ = run_manifest(pb, [row("Song", artist="Ann")], args(n=1), roots=[tmp_path])
    assert [l for l in lines if l.startswith("  #")][0].endswith("grid: FAIL 88.0 ms (override) [Mix A]; repeat of: Mix A")
    assert lines[-1].endswith("CACHED GRID FAIL: Ann - Song (original/unspecified) (88.0 ms)")


def test_an_override_fail_older_than_the_log_pass_does_not_lower_it(pb, tmp_path):
    ov = {"Ann - Song.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 88.0}}
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)]}, overrides=ov, mtime=2_000_000)
    os.utime(tmp_path / "Mix A" / "Hints" / "grid_overrides.json", (1_000_000, 1_000_000))
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) == ("PASS", 4.1, "Mix A", "log")


@pytest.mark.parametrize("override_ms", [2.0, 15.0, 88.0])
def test_an_override_never_raises_a_log_fail(pb, tmp_path, override_ms):
    ov = {"Ann - Song.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": override_ms}}
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line("FAIL", "Ann - Song.wav", FAIL_DETAIL)]}, overrides=ov, mtime=1_000_000)
    os.utime(tmp_path / "Mix A" / "Hints" / "grid_overrides.json", (2_000_000, 2_000_000))        # newer, and it says PASS (or FAIL again)
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) == ("FAIL", 127.0, "Mix A", "log")


def test_a_newer_override_pass_leaves_a_log_pass_alone(pb, tmp_path):
    ov = {"Ann - Song.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 2.0}}
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)]}, overrides=ov, mtime=1_000_000)
    os.utime(tmp_path / "Mix A" / "Hints" / "grid_overrides.json", (2_000_000, 2_000_000))
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) == ("PASS", 4.1, "Mix A", "log")


def test_a_deeply_nested_override_file_gives_unknown_not_a_crash(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], overrides="[" * 200000 + "]" * 200000)
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) is None


@pytest.mark.parametrize("a,b,equal", [("DJ X", "DJ", False), ("DJ X", "DJ X", True), ("X", "X", True), ("X", "DJ", False), ("Vs", "Vs", True),
                                       ("X Ambassadors", "X", False), ("X Ambassadors", "X Ambassadors", True), ("Aa x Bb", "Aa & Bb", True),
                                       ("Aa x", "Aa", False), ("Aa and", "Aa", False)])
def test_a_separator_word_with_no_name_beside_it_belongs_to_the_name(pb, a, b, equal):
    assert pb.artist_set(a) and (pb.artist_set(a) == pb.artist_set(b)) is equal


def test_a_solo_artist_called_x_is_matched_by_its_own_name(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["X - Song.wav"])
    assert repeats_of(pb, row("Song", artist="X"), tmp_path) == ["Mix A"] and repeats_of(pb, row("Song", artist="DJ"), tmp_path) == []


def test_blank_mixes_dir_entries_are_ignored_never_the_current_folder(pb, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("DJMIX_MIX_ROOTS", os.pathsep + "  " + os.pathsep)
    assert pb.explicit_mix_roots(args(mixes_dir=["", "   ", " /x "])) == ["/x"]
    monkeypatch.delenv("DJMIX_MIX_ROOTS")
    make_mix(tmp_path / "Defaults", "Default Mix", ["Artist - Song.wav"])
    monkeypatch.setattr(pb, "default_mix_roots", lambda: [str(tmp_path / "Defaults")])
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([row("Song", artist="Artist")]), encoding="utf-8")
    monkeypatch.chdir(tmp_path / "Defaults" / "Default Mix")                      # a blank entry read as "." would find this mix's Audio
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1", "--mixes-dir", "")
    out = capsys.readouterr().out
    assert "checked against 1 earlier mixes" in out and "path not found" not in out and "not a folder" not in out


def test_a_mixes_dir_that_is_a_file_says_not_a_folder(pb, monkeypatch, capsys, tmp_path):
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert pb.root_problems([str(tmp_path / "notes.txt"), str(tmp_path / "gone"), str(tmp_path)]) == \
        [f"not a folder: {tmp_path / 'notes.txt'}", f"path not found: {tmp_path / 'gone'}"]
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([row()]), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1", "--mixes-dir", str(tmp_path / "notes.txt"))
    assert f"not a folder: {tmp_path / 'notes.txt'}" in capsys.readouterr().out


# gate lines are only read in the shape validate_beatgrid.py prints: the stats field right after the 56-wide name
@pytest.mark.parametrize("stats", ["R=0.50 ph=advisory", "R=0.07 ph=+3ms[ticks]", "R=0.91 ph=-12ms[ticks]", "-"])
def test_every_stats_shape_the_gate_prints_is_read(pb, tmp_path, stats):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"Output/a.log": [gate_line("PASS", "Ann - Song.wav", PASS_DETAIL, stats=stats)]})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[0] == "PASS"


def test_a_fit_grids_from_ticks_line_is_not_a_gate_verdict(pb, tmp_path):
    # Source/fit_grids_from_ticks.py prints  [PASS] name[:52] padded to 54, then the BPM: no stats field, so not a gate line
    fit = "[PASS] " + "Zqa - Zqt (Zqbob Remix).wav"[:52].ljust(54) + " " + f"{99.5:>9.4f}" + " BPM first=0.100s off=+0.5ms drift=+1.0ms hits=300 dboff=0"
    make_mix(tmp_path, "Mix A", [], logs={"fit.txt": [fit]})
    assert grid_of(pb, row("Zqt", artist="Zqa", mix_name="Zqbob Remix", remixer="Zqbob"), tmp_path) is None


@pytest.mark.parametrize("line", [
    "Notes: PASS on the first listen. [PASS] felt good",
    "[PASS] Ann - Song.wav was fine by ear, Sam said PASS, no grid check done at all",
    "  [PASS] Ann - Song.wav                                   listened, fine",
    "  [SKIP] Ann - Song.wav                                   R=0.50 ph=advisory     too short",
])
def test_hand_typed_notes_and_skips_are_not_gate_verdicts(pb, tmp_path, line):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"notes.txt": [line]})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path) is None


def test_a_utf16_gate_log_is_read(pb, tmp_path):
    text = chr(0xFEFF) + gate_line("FAIL", "Ann - Song.wav", FAIL_DETAIL) + chr(13) + chr(10)
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], logs={"run.log": text.encode("utf-16-le")})
    assert grid_of(pb, row("Song", artist="Ann"), tmp_path)[:2] == ("FAIL", 127.0)


# a remixer called Master / Bit / V2 is a name, not delivery junk; delivery junk is only ever the TAIL
@pytest.mark.parametrize("remixer", ["Master", "Bit", "V2", "Amended"])
def test_a_remixer_named_like_a_delivery_word_is_not_the_bare_remix(pb, tmp_path, remixer):
    make_mix(tmp_path, "Mix A", [f"Aa - Bb ({remixer} Remix).wav"])
    assert repeats_of(pb, row("Bb", artist="Aa", mix_name="Remix"), tmp_path) == []            # bare "Remix" is a different version
    assert repeats_of(pb, row("Bb", artist="Aa", mix_name=f"{remixer} Remix", remixer=remixer), tmp_path) == ["Mix A"]


@pytest.mark.parametrize("fname", ["Aa - Bb (Extended Mix) SW V3.wav", "Aa - Bb (Original Mix) 24 Bit MASTER AMENDED V3.wav", "Aa - Bb UPDATE 12.02.26.wav",
                                   "Aa - Bb 16 Bit MASTER.wav"])
def test_delivery_words_at_the_end_are_still_ignored(pb, tmp_path, fname):
    make_mix(tmp_path, "Mix A", [fname])
    assert repeats_of(pb, row("Bb", artist="Aa"), tmp_path) == ["Mix A"]
