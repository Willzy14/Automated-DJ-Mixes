"""Main pipeline controller — wires analysis, sequencing, warping, automation, and ALS generation."""

import argparse
from pathlib import Path


def run_pipeline(input_dir: Path, output_dir: Path) -> Path:
    """Execute the full mix pipeline on tracks in input_dir, write ALS to output_dir."""
    raise NotImplementedError("Pipeline not yet implemented")


def main():
    parser = argparse.ArgumentParser(description="Generate an Ableton Live session from tagged dance tracks")
    parser.add_argument("--input", required=True, help="Folder containing audio tracks")
    parser.add_argument("--output", required=True, help="Folder for generated ALS output")
    args = parser.parse_args()

    als_path = run_pipeline(Path(args.input), Path(args.output))
    print(f"Generated: {als_path}")


if __name__ == "__main__":
    main()
