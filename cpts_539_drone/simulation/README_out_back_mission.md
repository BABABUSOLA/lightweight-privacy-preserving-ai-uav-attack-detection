# Out-and-Back Fast Mission (Normal + Real-Time Attack)

This guide adds a simple fast mission profile for PX4 SITL + Gazebo:

- Fly out and back in about 10 seconds at high speed
- Return to the initial location
- Save normal data to `data/normal_flights/raw`
- Save attack data to `data/attack_flights/raw`

## Files Created

- `cpts_539_drone/simulation/mission_out_back_fly_and_log.py`
- `cpts_539_drone/simulation/mission_out_back_gps_spoof_fly_and_log.py`

## Prerequisites

- PX4 SITL + Gazebo is already running in your Ubuntu VM.
- MAVSDK endpoint is reachable at the default address:
  - `udpin://0.0.0.0:14030`
- Run commands from repository root.

## 1) Run the Normal Fast Flight

This command runs a fast out-and-back profile and logs telemetry to:
`data/normal_flights/raw`

```bash
python3 cpts_539_drone/simulation/mission_out_back_fly_and_log.py \
  --do-takeoff \
  --scenario out_back_fast_10s \
  --run-id 01 \
  --cruise-speed-mps 12 \
  --cruise-seconds 10 \
  --mission-alt-m 12 \
  --output-dir data/normal_flights/raw
```

### How timing works

- Total cruise target = `--cruise-seconds` (default `10`)
- Out leg time = `cruise_seconds / 2`
- Back leg time = `cruise_seconds / 2`
- One-way waypoint distance is auto-computed as:
  - `distance = speed * (cruise_seconds / 2)`

## 2) Run Real-Time Attack on the Same Flight Profile

This command flies the same out-and-back path but injects spoofed GPS into the
logged coordinates during the flight. It saves to:
`data/attack_flights/raw`

```bash
python3 cpts_539_drone/simulation/mission_out_back_gps_spoof_fly_and_log.py \
  --do-takeoff \
  --scenario out_back_fast_10s_spoof \
  --run-id 01 \
  --cruise-speed-mps 12 \
  --cruise-seconds 10 \
  --mission-alt-m 12 \
  --attack-start 3 \
  --drift-rate-m-per-s 4.0 \
  --drift-direction-deg 90 \
  --output-dir data/attack_flights/raw
```

## Important Notes

- Normal mission script logs standard telemetry fields.
- Attack mission script logs both true and spoofed GPS:
  - `true_lat_deg`, `true_lon_deg` (ground truth)
  - `lat_deg`, `lon_deg` (possibly spoofed)
  - `label` (`normal` or `attack`)
  - `attack_elapsed_s`
- Both scripts require `--do-takeoff`; otherwise they skip logging.
- Both scripts validate CSV output before moving temp files into final output.

## Optional Tuning

- Increase speed: `--cruise-speed-mps 14` (or higher if your SITL tolerates it)
- Keep 10-second profile: leave `--cruise-seconds 10`
- Stronger spoofing: increase `--drift-rate-m-per-s`
- Change spoof direction:
  - `0` = north
  - `90` = east
  - `180` = south
  - `270` = west
