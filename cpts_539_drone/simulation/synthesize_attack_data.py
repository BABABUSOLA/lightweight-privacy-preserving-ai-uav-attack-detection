"""
Synthesize GPS Spoofing from Normal Flight CSVs
=================================================
Reads normal-flight CSVs, injects GPS drift after a configurable time,
writes new CSVs labelled as attack data.

Usage
-----
::

    # Convert all normal CSVs into attack versions:
    python3 simulation/synthesize_attack_data.py \\
        --input-dir data/normal_flights/raw \\
        --output-dir data/attack_flights/raw \\
        --attack-start 10 \\
        --drift-rate 1.5 \\
        --drift-dir 45

Options
-------
  --input-dir PATH       Directory containing normal-flight CSVs (default: data/normal_flights/raw)
  --output-dir PATH      Directory for synthesised attack CSVs (default: data/attack_flights/raw)
  --attack-start FLOAT   Seconds of clean data before spoof begins (default: 10.0)
  --drift-rate FLOAT     Drift speed in m/s (default: 1.5)
  --drift-dir FLOAT      Drift heading — 0 = north, 90 = east (default: 45.0)
"""

import argparse
import logging
import math
import sys
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("synthesize_attack_data")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_INPUT_COLUMNS = ["timestamp_unix_s", "lat_deg", "lon_deg"]

EXPECTED_OUTPUT_COLUMNS = [
    "timestamp_unix_s", "lat_deg", "lon_deg",
    "true_lat_deg", "true_lon_deg",
    "label", "attack_elapsed_s",
]


# ---------------------------------------------------------------------------
# Geodetic helpers
# ---------------------------------------------------------------------------

def meters_to_lat(m: float) -> float:
    """Convert a north/south displacement in metres to a latitude offset."""
    return m / 111_111.0


def meters_to_lon(m: float, lat_deg: float) -> float:
    """Convert an east/west displacement in metres to a longitude offset."""
    return m / (111_111.0 * math.cos(math.radians(lat_deg)))


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_args(args: argparse.Namespace) -> None:
    """Raise ValueError if any CLI argument is out of range."""
    if args.attack_start < 0:
        raise ValueError("--attack-start must be >= 0")
    if args.drift_rate < 0:
        raise ValueError("--drift-rate must be >= 0")


def validate_input_csv(df: pd.DataFrame, filepath: Path) -> None:
    """Raise ValueError if required columns are missing or data is empty."""
    if df.empty:
        raise ValueError(f"Input CSV is empty: {filepath}")

    missing = [c for c in REQUIRED_INPUT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Input CSV {filepath.name} is missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    # Sanity-check that coordinate columns contain numeric data
    for col in ["timestamp_unix_s", "lat_deg", "lon_deg"]:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(
                f"Column '{col}' in {filepath.name} is not numeric "
                f"(dtype: {df[col].dtype})"
            )


def validate_output_csv(df: pd.DataFrame, filepath: Path) -> None:
    """Warn if the output dataframe has unexpected shape or content."""
    issues = []

    # Check expected columns exist
    missing = [c for c in EXPECTED_OUTPUT_COLUMNS if c not in df.columns]
    if missing:
        issues.append(f"missing expected columns: {missing}")

    # Check that labels are only 'normal' or 'attack'
    if "label" in df.columns:
        unexpected = set(df["label"].unique()) - {"normal", "attack"}
        if unexpected:
            issues.append(f"unexpected label values: {unexpected}")

    # Check row count preserved
    if df.empty:
        issues.append("output dataframe is empty")

    # Check no NaN in critical columns
    for col in ["lat_deg", "lon_deg", "label"]:
        if col in df.columns and df[col].isna().any():
            nan_count = df[col].isna().sum()
            issues.append(f"column '{col}' has {nan_count} NaN values")

    if issues:
        for issue in issues:
            logger.warning(f"Output validation ({filepath.name}): {issue}")
    else:
        logger.info(f"Output validation passed: {filepath.name}")


# ---------------------------------------------------------------------------
# Core transform
# ---------------------------------------------------------------------------

def inject_spoof(
    df: pd.DataFrame,
    attack_start_s: float = 10.0,
    drift_rate_mps: float = 1.5,
    drift_dir_deg: float = 45.0,
) -> pd.DataFrame:
    """Inject synthetic GPS drift into a normal-flight dataframe.

    Args:
        df: Input dataframe with at least timestamp_unix_s, lat_deg, lon_deg.
        attack_start_s: Seconds of clean pass-through before drift begins.
        drift_rate_mps: Drift speed in m/s.
        drift_dir_deg: Drift heading (0 = north, 90 = east).

    Returns:
        New dataframe with spoofed lat/lon, ground-truth columns, and labels.
    """
    out = df.copy()

    # Normalize time to start at 0
    t0 = out["timestamp_unix_s"].iloc[0]
    elapsed = out["timestamp_unix_s"] - t0

    # Keep ground truth
    out["true_lat_deg"] = out["lat_deg"]
    out["true_lon_deg"] = out["lon_deg"]

    # Label
    out["label"] = "normal"
    out["attack_elapsed_s"] = 0.0

    attack_mask = elapsed >= attack_start_s
    attack_t = (elapsed - attack_start_s).clip(lower=0)

    dir_rad = math.radians(drift_dir_deg)
    drift_north = drift_rate_mps * math.cos(dir_rad) * attack_t
    drift_east = drift_rate_mps * math.sin(dir_rad) * attack_t

    out.loc[attack_mask, "lat_deg"] += drift_north[attack_mask].apply(meters_to_lat)
    out.loc[attack_mask, "lon_deg"] += [
        meters_to_lon(e, lat)
        for e, lat in zip(
            drift_east[attack_mask], out.loc[attack_mask, "true_lat_deg"]
        )
    ]
    out.loc[attack_mask, "label"] = "attack"
    out.loc[attack_mask, "attack_elapsed_s"] = attack_t[attack_mask].round(3)

    return out


# ---------------------------------------------------------------------------
# Output naming
# ---------------------------------------------------------------------------

def make_output_name(input_name: str) -> str:
    """Deterministic output filename: ``attack_<original>``.

    Avoids the fragile heuristic of replacing substrings like 'hover'.
    """
    return f"attack_{input_name}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the synthesis pipeline.  Returns 0 on success, 1 on error."""
    parser = argparse.ArgumentParser(
        description="Synthesise GPS-spoofed attack CSVs from normal flight data.",
        epilog="""
Examples:
  python3 simulation/synthesize_attack_data.py
  python3 simulation/synthesize_attack_data.py \\
      --input-dir data/normal_flights/raw \\
      --output-dir data/attack_flights/raw \\
      --attack-start 10 --drift-rate 1.5 --drift-dir 45
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir", default="data/normal_flights/raw",
        help="Directory with normal CSVs",
    )
    parser.add_argument(
        "--output-dir", default="data/attack_flights/raw",
        help="Directory for attack CSVs",
    )
    parser.add_argument("--attack-start", type=float, default=10.0,
                        help="Seconds before spoof begins")
    parser.add_argument("--drift-rate", type=float, default=1.5,
                        help="Drift speed (m/s)")
    parser.add_argument("--drift-dir", type=float, default=45.0,
                        help="Drift heading (0=north, 90=east)")
    args = parser.parse_args()

    # --- Validate arguments ---------------------------------------------------
    try:
        validate_args(args)
    except ValueError as e:
        logger.error(f"Invalid arguments: {e}")
        parser.print_help()
        return 1

    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)

    if not in_dir.exists():
        logger.error(f"Input directory does not exist: {in_dir}")
        return 1
    if not in_dir.is_dir():
        logger.error(f"Input path is not a directory: {in_dir}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*.csv"))
    if not files:
        logger.error(f"No CSV files found in {in_dir}")
        return 1

    logger.info(f"Found {len(files)} CSV(s) in {in_dir}")
    logger.info(
        f"Spoof config: attack_start={args.attack_start}s, "
        f"drift_rate={args.drift_rate} m/s, drift_dir={args.drift_dir}°"
    )

    processed = 0
    skipped = 0

    for f in files:
        # --- Read and validate input ------------------------------------------
        try:
            df = pd.read_csv(f)
        except Exception as e:
            logger.error(f"Failed to read {f.name}: {e}")
            skipped += 1
            continue

        try:
            validate_input_csv(df, f)
        except ValueError as e:
            logger.error(str(e))
            skipped += 1
            continue

        logger.info(f"Processing {f.name} ({len(df)} rows)")

        # --- Inject spoof -----------------------------------------------------
        attack_df = inject_spoof(
            df,
            attack_start_s=args.attack_start,
            drift_rate_mps=args.drift_rate,
            drift_dir_deg=args.drift_dir,
        )

        # --- Validate and write output ----------------------------------------
        out_name = make_output_name(f.name)
        out_path = out_dir / out_name

        validate_output_csv(attack_df, out_path)

        try:
            attack_df.to_csv(out_path, index=False)
        except Exception as e:
            logger.error(f"Failed to write {out_path}: {e}")
            skipped += 1
            continue

        label_counts = attack_df["label"].value_counts().to_dict()
        logger.info(f"  -> {out_path}  ({label_counts})")
        processed += 1

    # --- Summary --------------------------------------------------------------
    logger.info(f"Done: {processed} processed, {skipped} skipped out of {len(files)}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())