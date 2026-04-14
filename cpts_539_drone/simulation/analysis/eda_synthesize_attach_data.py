#cell1- define the spoofing function
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

    # assume df has 'timestamp_unix_s', 'lat_deg', 'lon_deg'
    t0 = out["timestamp_unix_s"].iloc[0]
    elapsed = out["timestamp_unix_s"] - t0

    out["true_lat_deg"] = out["lat_deg"]
    out["true_lon_deg"] = out["lon_deg"]
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

#cell2
input_dir = Path("data") / "normal_flights" / "raw"
output_dir = Path("data") / "attack_flights" / "raw"
output_dir.mkdir(parents=True, exist_ok=True)

files = sorted(input_dir.glob("*.csv"))
print("Found", len(files), "normal flights")

for f in files:
    df = pd.read_csv(f)
    print("Processing", f.name, "rows:", len(df))

    attack_df = inject_spoof(
        df,
        attack_start_s=10.0,   # change here if you want
        drift_rate_mps=1.5,
        drift_dir_deg=45.0,
    )

    out_name = f"attack_{f.name}"
    out_path = output_dir / out_name
    attack_df.to_csv(out_path, index=False)
    print("  -> wrote", out_path)

#cell3
import matplotlib.pyplot as plt

sample_file = sorted((Path("data") / "attack_flights" / "raw").glob("*.csv"))[0]
att = pd.read_csv(sample_file)

t = att["timestamp_unix_s"] - att["timestamp_unix_s"].min()

plt.figure(figsize=(8,4))
plt.plot(t, att["true_lat_deg"], label="true_lat")
plt.plot(t, att["lat_deg"], label="spoofed_lat")
plt.xlabel("time (s)")
plt.ylabel("latitude (deg)")
plt.legend()
plt.grid(True)
plt.title(sample_file.name)
plt.show()