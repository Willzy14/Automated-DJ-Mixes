"""Tests for Tools/playlist_builder.py with the Neon reader STUBBED everywhere.

No network, no database, no credentials file: an autouse fixture makes the real credentials reader raise and makes
`import psycopg2` fail unless a test installs a fake one. The builder prints its playlist to stdout (it writes no output
file), so the main path is tested through capsys, with the rows file (the offline `--rows` input) written to tmp_path.

Not tested on purpose: the exact numbers of the cool and cult presets (Sam's taste, still being tuned), only that they
return a finite score for every row. The builder does not call Tools/resolve_masters.py (it only prints a hint line), so
there is no resolver-integration path to test.
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
    return _pb_module


def args(**kw):
    base = dict(cmd="build", label=[], artist=[], year_from=None, year_to=None, min_streams=None, arc="rise",
                preset="any", n=12, max_per_artist=1, heat_min=0, heat_max=10, rows=None)
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
