"""Print VLAD's full Rekordbox phrase + fill data."""

from automated_dj_mixes.rekordbox_reader import read_rekordbox_library


def main():
    rb_lib = read_rekordbox_library()
    for fname, ra in rb_lib.items():
        if "vlad" in fname.lower():
            print(f"\n=== {ra.title} ===")
            print(f"BPM: {ra.bpm}, end_beat: {ra.end_beat}, mood: {ra.mood}")
            print(f"first_downbeat_offset: {ra.first_downbeat_offset}")
            print(f"Total beats in grid: {len(ra.beat_times_ms)}")
            print(f"\n{'idx':>3}  {'label':8s} {'start':>5} {'end':>5} {'len':>4}  fill@  bars")
            print("-" * 60)
            for i, p in enumerate(ra.phrases):
                end_beat = ra.phrase_end_beat(i)
                length = end_beat - p.start_beat
                fill_info = f"{p.fill_beat}" if p.fill else "-"
                bars = length / 4.0
                print(
                    f"  {i:2d}  {p.label:8s} {p.start_beat:>5d} {end_beat:>5d} "
                    f"{length:>4d}  {fill_info:>5s}  {bars:>4.1f}"
                )
            return


if __name__ == "__main__":
    main()
