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

def make_mix(root, name, wavs, log_lines=None, overrides=None, excluded=()):
    mix = root / name
    (mix / "Audio").mkdir(parents=True)
    for w in wavs: (mix / "Audio" / w).touch()
    if excluded:
        (mix / "_Excluded Audio").mkdir()
        for w in excluded: (mix / "_Excluded Audio" / w).touch()
    if log_lines is not None:
        (mix / "Output").mkdir()
        (mix / "Output" / "relay.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    if overrides is not None:
        (mix / "Hints").mkdir()
        (mix / "Hints" / "grid_overrides.json").write_text(overrides if isinstance(overrides, str) else json.dumps(overrides), encoding="utf-8")
    return mix


def gate_line(verdict, name, detail):
    return f"  [{verdict:4s}] {name[:54]:56s} R=0.50 ph=advisory     {detail}"


PASS_DETAIL = "stem-kick grid (whole-mix R=0.50 is not the ruler; grid_vs_kick 4.1ms - sits on the transients)"
FAIL_DETAIL = "stem grid is 127ms OFF its own kicks (>15ms) - the detector can't grid this track"


def run_manifest(pb, rows, a, roots=(), **kw):
    drops = {}
    pool = pb.prepare(rows, a, drops)
    picked = pb.build(rows, a)
    dirs = pb.mix_dirs(list(roots))
    mixes, bad = pb.read_earlier_mixes(dirs)
    return pb.manifest(rows, pool, drops, picked, a, mixes, bad, pb.read_grid_cache(dirs), **kw), picked


def test_manifest_first_line_built_against_requested(pb):
    lines, picked = run_manifest(pb, ladder_rows(), args(n=7))
    assert lines[0] == "built 7 of 7 requested" and len(picked) == 7


def test_manifest_short_build_says_pool_too_small(pb):
    lines, _ = run_manifest(pb, ladder_rows()[:2], args(n=5))
    assert lines[0] == "built 2 of 5 requested: pool too small (2 usable tracks after the filters)"


def test_manifest_short_build_says_max_per_artist(pb):
    rows = [row(f"T{i}", artist="Same", genre="House", streams=1000 + i) for i in range(6)]
    lines, _ = run_manifest(pb, rows, args(n=4))
    assert lines[0].startswith("built 1 of 4 requested: ") and "max-per-artist 1" in lines[0] and "leave 1 pickable" in lines[0]


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
    assert note in run_manifest(pb, ladder_rows(), args(n=3))[0]
    assert note not in run_manifest(pb, ladder_rows(), args(n=3, preset="cool", credible=True))[0]
    assert note not in run_manifest(pb, ladder_rows(), args(n=3, preset="cool"))[0]


# --- repeats --------------------------------------------------------------------------------------------------------------

def test_repeats_not_checked_when_no_earlier_mix_is_readable(pb, tmp_path):
    lines, _ = run_manifest(pb, ladder_rows(), args(n=3), roots=[tmp_path / "nowhere"])
    assert "repeats: not checked (no earlier mix list found)" in lines
    assert not any("none" in l.lower() for l in lines if l.startswith("repeats"))
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


def test_a_remix_and_an_original_are_different_versions_not_repeats(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Mr. V - Mr. Bongo (Butch Remix).wav", "Artist - Plain Song (Extended Mix) SW V3.wav"])
    orig = row("Mr. Bongo", artist="Mr. V")
    remix = row("Mr. Bongo", artist="Mr. V", remixer="Butch")
    plain = row("Plain Song", artist="Artist")
    plain_other_remix = row("Plain Song", artist="Artist", remixer="Bob")
    mixes, _ = pb.read_earlier_mixes(pb.mix_dirs([tmp_path]))
    assert pb.repeats_in(orig, mixes) == []
    assert pb.repeats_in(remix, mixes) == ["Mix A"]
    assert pb.repeats_in(plain, mixes) == ["Mix A"]                       # extended mix is the original version
    assert pb.repeats_in(plain_other_remix, mixes) == []


def test_repeat_match_needs_a_word_boundary_and_a_title_made_of_letters(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Artist - Taste Of Honey.wav", "Artist - !!!.wav"])
    mixes, _ = pb.read_earlier_mixes(pb.mix_dirs([tmp_path]))
    assert pb.repeats_in(row("Taste", artist="Artist"), mixes) == []      # "Taste" is not "Taste Of Honey"
    assert pb.repeats_in(row("!!!", artist="Artist"), mixes) == []        # punctuation-only title never matches anything
    assert pb.repeats_in(row("", artist="Artist"), mixes) == []


def test_repeat_match_folds_accents_and_ignores_the_delivery_suffix(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Michaël Canitrot - Renaissance (Extended Mix) 24 Bit MASTER AMENDED V3.wav", "10A - Duo - Song SW V1.wav"])
    mixes, _ = pb.read_earlier_mixes(pb.mix_dirs([tmp_path]))
    assert pb.repeats_in(row("Renaissance", artist="Michael Canitrot"), mixes) == ["Mix A"]
    assert pb.repeats_in(row("Renaissance", artist="Michaël Canitrot"), mixes) == ["Mix A"]
    assert pb.repeats_in(row("Song", artist="Duo"), mixes) == ["Mix A"]    # a leading key prefix is ignored


def test_non_latin_artist_names_do_not_crash_or_match(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Кино - Группа крови.wav"])
    mixes, _ = pb.read_earlier_mixes(pb.mix_dirs([tmp_path]))
    assert pb.repeats_in(row("Группа крови", artist="Кино"), mixes) == []   # unknown, never a false repeat


def test_unreadable_mix_names_are_skipped_and_unlistable_mixes_are_counted(pb, tmp_path, monkeypatch):
    make_mix(tmp_path, "Mix A", ["no separator here.wav", "notes.txt", " - .wav", "Artist - Song.wav"])
    make_mix(tmp_path, "_Corpus", ["Artist - Song.wav"])                  # names starting with _ are corpora, not mixes
    mixes, bad = pb.read_earlier_mixes(pb.mix_dirs([tmp_path]))
    assert [m[0] for m in mixes] == ["Mix A"] and len(mixes[0][1]) == 1 and bad == 0
    monkeypatch.setattr(pb, "wavs_in", lambda d: None)                    # the Audio folder cannot be listed
    mixes, bad = pb.read_earlier_mixes(pb.mix_dirs([tmp_path]))
    assert mixes == [] and bad == 1


def test_mix_roots_come_from_the_environment_and_the_flag(pb, monkeypatch, tmp_path):
    make_mix(tmp_path / "Env Root", "Mix A", ["Artist - Song.wav"])
    make_mix(tmp_path / "Flag Root", "Mix B", ["Artist - Song.wav"])
    monkeypatch.undo()                                                     # the real default_mix_roots, no autouse stubs
    monkeypatch.setenv("DJMIX_MIX_ROOTS", str(tmp_path / "Env Root"))
    assert str(tmp_path / "Env Root") in pb.default_mix_roots()
    assert [os.path.basename(d) for d in pb.mix_dirs([str(tmp_path / "Env Root"), str(tmp_path / "Flag Root")])] == ["Mix A", "Mix B"]
    assert pb.mix_dirs([str(tmp_path / "Env Root"), str(tmp_path / "Env Root")]) == pb.mix_dirs([str(tmp_path / "Env Root")])


# --- cached beat-grid verdicts --------------------------------------------------------------------------------------------

GRID_ROWS = [row("Pass Track", artist="Ann"), row("Fail Track", artist="Bob", genre="Tech House"), row("New Track", artist="Cat", genre="Deep House")]


def test_grid_pass_fail_and_unknown_from_earlier_gate_logs(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Ann - Pass Track (Extended Mix) SW V2.wav"], excluded=["Bob - Fail Track SW V1.wav"], log_lines=[
        "Beatgrid verification (whole-track onset-vs-grid sweep test)...",
        gate_line("PASS", "Ann - Pass Track (Extended Mix) SW V2.wav", PASS_DETAIL),
        gate_line("FAIL", "Bob - Fail Track SW V1.wav", FAIL_DETAIL)])
    lines, _ = run_manifest(pb, GRID_ROWS, args(n=3, max_per_artist=3), roots=[tmp_path])
    picks = {l.strip().split(": ")[0].split(" ", 1)[1]: l for l in lines if l.startswith("  #")}
    assert "grid: PASS 4.1 ms (cached) [Mix A]" in picks["Ann - Pass Track"]
    assert "grid: FAIL 127.0 ms (cached) [Mix A]" in picks["Bob - Fail Track"]
    assert "grid: unknown (never analysed)" in picks["Cat - New Track"]
    assert "grid known for 2 of 3 picks" in lines
    assert lines[-1].endswith("CACHED GRID FAIL: Bob - Fail Track (127.0 ms)")


def test_grid_unknown_is_never_printed_as_a_pass(pb, tmp_path):
    lines, _ = run_manifest(pb, GRID_ROWS, args(n=3), roots=[tmp_path])
    assert "grid known for 0 of 3 picks (no earlier gate output found)" in lines
    assert not any("grid: PASS" in l for l in lines) and not any("CACHED GRID FAIL" in l for l in lines)
    assert all("grid: unknown (never analysed)" in l for l in lines if l.startswith("  #"))


def test_grid_long_names_cut_at_54_chars_are_resolved_through_the_audio_folder(pb, tmp_path):
    long_name = "Harry Romero, Jose Nunez, Alex Alicea & Shawnee Taylor - I Wanna Thank You 24 Bit MASTER.wav"
    make_mix(tmp_path, "Mix A", [long_name], log_lines=[gate_line("PASS", long_name, PASS_DETAIL)])
    r = row("I Wanna Thank You", artist="Harry Romero, Jose Nunez, Alex Alicea & Shawnee Taylor")
    cache = pb.read_grid_cache(pb.mix_dirs([tmp_path]))
    assert pb.grid_for(r, cache) == ("PASS", 4.1, "Mix A")


def test_grid_a_remix_is_not_the_originals_result(pb, tmp_path):
    make_mix(tmp_path, "Mix A", ["Ann - Song (Bob Remix).wav"], log_lines=[gate_line("FAIL", "Ann - Song (Bob Remix).wav", FAIL_DETAIL)])
    cache = pb.read_grid_cache(pb.mix_dirs([tmp_path]))
    assert pb.grid_for(row("Song", artist="Ann"), cache) is None
    assert pb.grid_for(row("Song", artist="Ann", remixer="Bob"), cache)[0] == "FAIL"


def test_grid_newest_cached_result_wins(pb, tmp_path):
    old = make_mix(tmp_path, "Old Mix", ["Ann - Song.wav"], log_lines=[gate_line("FAIL", "Ann - Song.wav", FAIL_DETAIL)])
    new = make_mix(tmp_path, "New Mix", ["Ann - Song.wav"], log_lines=[gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)])
    os.utime(old / "Output" / "relay.log", (1_000_000, 1_000_000))
    os.utime(new / "Output" / "relay.log", (2_000_000, 2_000_000))
    assert pb.grid_for(row("Song", artist="Ann"), pb.read_grid_cache(pb.mix_dirs([tmp_path])))[0] == "PASS"


def test_grid_overrides_json_is_read_and_applies_the_15_ms_rule(pb, tmp_path):
    make_mix(tmp_path, "Mix A", [], overrides={"Ann - Near.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 3.2},
                                                "Ann - Far.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": 88.0},
                                                "Ann - Other.wav": {"phase_source": "ableton-ticks", "grid_vs_kick_ms": 1.0},
                                                "Ann - Bad.wav": {"phase_source": "drum-stem-kicks", "grid_vs_kick_ms": "x"}})
    cache = pb.read_grid_cache(pb.mix_dirs([tmp_path]))
    assert pb.grid_for(row("Near", artist="Ann"), cache)[:2] == ("PASS", 3.2)
    assert pb.grid_for(row("Far", artist="Ann"), cache)[:2] == ("FAIL", 88.0)
    assert pb.grid_for(row("Other", artist="Ann"), cache) is None          # only stem-kick grids carry the kick figure
    assert pb.grid_for(row("Bad", artist="Ann"), cache) is None


@pytest.mark.parametrize("content", ["{", "", "[1, 2]", "null", '{"Ann - Song.wav": 5}', '{"Ann - Song.wav": {"grid_vs_kick_ms": 2}}'])
def test_grid_corrupt_or_half_written_cache_files_give_unknown_not_a_crash(pb, tmp_path, content):
    make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], overrides=content,
             log_lines=["  [PASS", gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)[:30], "garbage \x00 line"])
    cache = pb.read_grid_cache(pb.mix_dirs([tmp_path]))
    assert pb.grid_for(row("Song", artist="Ann"), cache) is None


def test_grid_log_with_invalid_bytes_is_still_read(pb, tmp_path):
    mix = make_mix(tmp_path, "Mix A", ["Ann - Song.wav"], log_lines=[gate_line("PASS", "Ann - Song.wav", PASS_DETAIL)])
    with open(mix / "Output" / "relay.log", "ab") as f: f.write(b"\xff\xfe bad bytes \x80\n")
    assert pb.grid_for(row("Song", artist="Ann"), pb.read_grid_cache(pb.mix_dirs([tmp_path])))[0] == "PASS"


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
    assert "built 3 of 3" not in capsys.readouterr().err


def test_main_mixes_dir_flag_feeds_the_repeat_and_grid_checks(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([row("Song", artist="Ann")]), encoding="utf-8")
    make_mix(tmp_path / "My Earlier Mixes", "Mix A", ["Ann - Song.wav"], log_lines=[gate_line("FAIL", "Ann - Song.wav", FAIL_DETAIL)])
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "1", "--mixes-dir", str(tmp_path / "My Earlier Mixes"))
    out = capsys.readouterr().out
    assert "repeats: 1 of 1 picks were in an earlier mix (checked against 1 earlier mixes)" in out
    assert "  #1 Ann - Song: grid: FAIL 127.0 ms (cached) [Mix A]; repeat of: Mix A" in out
    assert "CACHED GRID FAIL: Ann - Song (127.0 ms)" in out


def test_main_manifest_never_writes_a_file_or_reaches_the_network(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
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


@pytest.mark.parametrize("argv", [["--credible", "--preset", "any"], ["--credible"]])
def test_credible_refuses_preset_any_with_exit_2_and_a_one_line_reason(pb, monkeypatch, capsys, tmp_path, argv):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), *argv)
    assert e.value.code == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "--credible refuses --preset any" in cap.err and "use --preset cool, cult or hits" in cap.err


def test_without_credible_preset_any_still_works_and_says_why_it_is_not_coherent(pb, monkeypatch, capsys, tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(ladder_rows()), encoding="utf-8")
    _run_main(pb, monkeypatch, "build", "--rows", str(rows_file), "--n", "3")
    assert "preset any ranks by stream percentile across all genres; use --credible --preset cool for a coherent set" in capsys.readouterr().out


def test_manifest_has_no_em_dash_and_no_credentials(pb, tmp_path):
    lines, _ = run_manifest(pb, ladder_rows(), args(n=7))
    text = "\n".join(lines)
    assert chr(0x2014) not in text and "postgres" not in text.lower()
