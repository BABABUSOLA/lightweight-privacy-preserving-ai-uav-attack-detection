"""
Synthesize GPS Spoofing from Normal Flight CSVs
=================================================
Reads normal-flight CSVs, injects GPS drift after a configurable time,
writes new CSVs labelled as attack data.
"""

import argparse
import math
from pathlib import Path

import pandas as pd


def meters_to_lat(m: float) -> float:
    return m / 111_111.0


def meters_to_lon(m: float, lat_deg: float) -> float:
    return m / (111_111.0 * math.cos(math.radians(lat_deg)))


def inject_spoof(
    df: pd.DataFrame,
    attack_start_s: float = 10.0,
    drift_rate_mps: float = 1.5,
    drift_dir_deg: float = 45.0,
) -> pd.DataFrame:
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
        for e, lat in zip(drift_east[attack_mask], out.loc[attack_mask, "true_lat_deg"])
    ]
    out.loc[attack_mask, "label"] = "attack"
    out.loc[attack_mask, "attack_elapsed_s"] = attack_t[attack_mask].round(3)

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="data/normal_flights/raw",
                        help="Directory with normal CSVs")
    parser.add_argument("--output-dir", default="data/attack_flights/raw",
                        help="Directory for attack CSVs")
    parser.add_argument("--attack-start", type=float, default=10.0)
    parser.add_argument("--drift-rate", type=float, default=1.5)
    parser.add_argument("--drift-dir", type=float, default=45.0)
    args = parser.parse_args()

    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*.csv"))
    if not files:
        print(f"No CSVs found in {in_dir}")
        return

    for f in files:
        df = pd.read_csv(f)
        print(f"Processing {f.name} ({len(df)} rows)...")

        attack_df = inject_spoof(
            df,
            attack_start_s=args.attack_start,
            drift_rate_mps=args.drift_rate,
            drift_dir_deg=args.drift_dir
        )

        # Rename: normal_hover_run01 -> attack_hover_run01
        out_name = f.name.replace("hover", "spoof_hover", 1)
        if out_name == f.name:
            out_name = "attack_" + f.name

        out_path = out_dir / out_name
        attack_df.to_csv(out_path, index=False)
        print(f"  -> {out_path}  ({attack_df['label'].value_counts().to_dict()})")

    print("Done.")


if __name__ == "__main__":
    main()

'''
# Convert all normal CSVs into attack versions


python3 simulation/synthesize_attack_data.py \
  --input-dir data/normal_flights/raw \
  --output-dir data/attack_flights/raw \
  --attack-start 10 \
  --drift-rate 1.5 \
  --drift-dir 45
'''

