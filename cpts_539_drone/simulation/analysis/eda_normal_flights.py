from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# Point to your raw normal-flight logs
data_dir = Path("data") / "normal_flights" / "raw"
files = sorted(data_dir.glob("*.csv"))
len(files), files[:5]

#second step: load and combine
dfs = []
for f in files:
    df = pd.read_csv(f)
    df["source_file"] = f.name
    dfs.append(df)

all_df = pd.concat(dfs, ignore_index=True)
all_df.head()

#third step: basic summaries
print("Total rows:", len(all_df))
print(all_df[["scenario", "run_id", "source_file"]].drop_duplicates().head())

print("\nMissing values per column:")
print(all_df.isna().mean().sort_values(ascending=False))

#fourth step: example plots
# Pick one run
one_run = all_df[all_df["source_file"] == files[0].name].copy()
one_run["t0"] = one_run["timestamp_unix_s"] - one_run["timestamp_unix_s"].min()

# Altitude vs time
plt.figure(figsize=(8, 4))
plt.plot(one_run["t0"], one_run["rel_alt_m"])
plt.xlabel("Time since start (s)")
plt.ylabel("Relative altitude (m)")
plt.title(f"Altitude vs time ({files[0].name})")
plt.grid(True)
plt.show()

# Accel norm vs time
import numpy as np

accel = np.sqrt(
    one_run["accel_x_mps2"]**2 +
    one_run["accel_y_mps2"]**2 +
    one_run["accel_z_mps2"]**2
)

plt.figure(figsize=(8, 4))
plt.plot(one_run["t0"], accel)
plt.xlabel("Time since start (s)")
plt.ylabel("Accel norm (m/s^2)")
plt.title(f"Accel norm vs time ({files[0].name})")
plt.grid(True)
plt.show()

# Battery remaining vs time
plt.figure(figsize=(8, 4))
plt.plot(one_run["t0"], one_run["battery_remaining_pct"])
plt.xlabel("Time since start (s)")
plt.ylabel("Battery remaining (%)")
plt.title(f"Battery vs time ({files[0].name})")
plt.grid(True)
plt.show()
