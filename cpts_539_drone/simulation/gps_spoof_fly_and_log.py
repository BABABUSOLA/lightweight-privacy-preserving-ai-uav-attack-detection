"""
GPS Spoofing Flight Logger
===========================
Logs telemetry from PX4 SITL with optional GPS spoofing.

After --attack-start seconds, logged GPS coordinates are offset by a drift
that grows linearly, simulating a gradual GPS spoofing attack.

IMU and battery values are always logged truthfully, creating realistic
GPS-vs-IMU inconsistency for LSTM autoencoder anomaly detection.

Columns:
  - true_lat_deg / true_lon_deg: Ground-truth GPS (unspoofed)
  - lat_deg / lon_deg: Reported GPS (spoofed if attack active)
  - label: "normal" (before attack) or "attack" (during spoofing)
  - attack_elapsed_s: Seconds since spoofing began (0 if normal)
"""

import argparse
import asyncio
import csv
import logging
import math
import time
from datetime import datetime
from pathlib import Path

from mavsdk import System

import config
import utils

# Setup logging
logger = utils.setup_logging(__name__)


async def main() -> None:
    """Main GPS-spoofing flight logger coroutine."""
    parser = argparse.ArgumentParser(
        description="Log flight telemetry with optional GPS spoofing.",
        epilog="""
Examples:
  python3 gps_spoof_fly_and_log.py --scenario hover --duration 60
  python3 gps_spoof_fly_and_log.py --scenario hover --duration 90 \\
      --attack-start 30 --drift-rate-m-per-s 1.5 --do-takeoff
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Flight parameters
    parser.add_argument(
        "--scenario", default="spoof_drift", help="Scenario label for output"
    )
    parser.add_argument(
        "--run-id", default="01", help="Run identifier (e.g., 01, 02)"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=config.DEFAULT_LOGGING_DURATION_S,
        help="Total logging duration (seconds)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=config.DEFAULT_LOGGING_RATE_HZ,
        help="Logging rate (Hz)",
    )
    parser.add_argument(
        "--do-takeoff", action="store_true", help="Arm and takeoff before logging"
    )
    parser.add_argument(
        "--debug-log-on-fail",
        action="store_true",
        help=(
            "If set, still write CSV telemetry even when --do-takeoff is requested "
            "but arm/takeoff fails."
        ),
    )
    parser.add_argument(
        "--system-address",
        default=config.DEFAULT_SYSTEM_ADDRESS,
        help="MAVSDK system address",
    )

    # Attack parameters
    parser.add_argument(
        "--attack-start",
        type=float,
        default=10.0,
        help="Delay before spoofing begins (seconds)",
    )
    parser.add_argument(
        "--drift-rate-m-per-s",
        type=float,
        default=config.GPS_DRIFT_RATE_MPS,
        help="GPS drift speed (meters/second)",
    )
    parser.add_argument(
        "--drift-direction-deg",
        type=float,
        default=config.GPS_DRIFT_DIRECTION_DEG,
        help="Drift direction in degrees (0=North, 90=East)",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.duration <= 0 or args.rate <= 0:
        logger.error("Duration and rate must be positive")
        parser.print_help()
        return

    if args.attack_start < 0:
        logger.error("attack-start must be non-negative")
        return

    if args.attack_start >= args.duration:
        logger.warning("attack-start >= duration; spoofing never begins")

    # Setup output directory
    raw_dir = Path(config.DEFAULT_ATTACK_LOG_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Generate output filename
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"flight_{ts}.csv"
    out_path = raw_dir / out_name

    logger.info(f"Scenario: {args.scenario}, Run: {args.run_id}")
    logger.info(f"Duration: {args.duration}s, Rate: {args.rate} Hz")
    logger.info(
        f"Attack starts at {args.attack_start}s, "
        f"drift={args.drift_rate_m_per_s:.1f} m/s @ {args.drift_direction_deg:.0f}°"
    )
    logger.info(f"Output: {out_path}")

    # Connect to PX4 SITL
    drone = System()
    try:
        await drone.connect(system_address=args.system_address)
        logger.info(f"Connecting to {args.system_address}...")
    except Exception as e:
        logger.error(f"Failed to initiate connection: {e}", exc_info=True)
        return

    # Wait for connection with timeout
    try:
        await utils.wait_for_connection(drone, verbose=True)
    except TimeoutError as e:
        logger.error(str(e))
        return
    except Exception as e:
        logger.error(f"Unexpected error during connection: {e}", exc_info=True)
        return

    # Policy: only log successful flight runs (no ground-only logging).
    if not args.do_takeoff:
        logger.warning(
            "Takeoff not requested (--do-takeoff not set); skipping CSV logging "
            "because this run did not execute a flight."
        )
        return

    # Try to arm/takeoff if requested
    took_off = await utils.maybe_arm_and_takeoff(drone, args.do_takeoff, verbose=True)
    if args.do_takeoff and not took_off and not args.debug_log_on_fail:
        logger.warning(
            "Takeoff was requested but arm/takeoff failed; skipping CSV logging. "
            "Use --debug-log-on-fail to capture disarmed telemetry for debugging."
        )
        return

    # Initialize telemetry snapshot dict
    latest = {
        "lat_deg": None,
        "lon_deg": None,
        "abs_alt_m": None,
        "rel_alt_m": None,
        "accel_x_mps2": None,
        "accel_y_mps2": None,
        "accel_z_mps2": None,
        "gyro_x_rps": None,
        "gyro_y_rps": None,
        "gyro_z_rps": None,
        "battery_remaining_pct": None,
        "battery_voltage_v": None,
    }

    # Create background telemetry streaming tasks
    tasks = [
        asyncio.create_task(utils.read_position(drone, latest)),
        asyncio.create_task(utils.read_imu(drone, latest)),
        asyncio.create_task(utils.read_battery(drone, latest)),
    ]

    # Precompute drift components (North/East decomposition)
    dir_rad = math.radians(args.drift_direction_deg)
    drift_north_mps = args.drift_rate_m_per_s * math.cos(dir_rad)
    drift_east_mps = args.drift_rate_m_per_s * math.sin(dir_rad)

    fieldnames = [
        "timestamp_unix_s",
        "timestamp_iso",
        "scenario",
        "run_id",
        "label",  # "normal" or "attack"
        "attack_elapsed_s",  # Time since spoofing began (0 if normal)
        "true_lat_deg",
        "true_lon_deg",  # Ground-truth GPS
        "lat_deg",
        "lon_deg",  # Reported GPS (may be spoofed)
        "abs_alt_m",
        "rel_alt_m",
        "accel_x_mps2",
        "accel_y_mps2",
        "accel_z_mps2",
        "gyro_x_rps",
        "gyro_y_rps",
        "gyro_z_rps",
        "battery_remaining_pct",
        "battery_voltage_v",
    ]

    # Main logging loop
    print(
        f"Logging {args.duration:.0f}s @ {args.rate:.0f} Hz -> {out_path}"
    )
    start = time.time()
    period = 1.0 / args.rate
    row_count = 0

    try:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            while (time.time() - start) < args.duration:
                now = time.time()
                elapsed = now - start

                true_lat = latest["lat_deg"]
                true_lon = latest["lon_deg"]

                # Compute spoofed GPS coordinates if attack is active
                if elapsed >= args.attack_start and true_lat is not None:
                    attack_t = elapsed - args.attack_start
                    offset_north_m = drift_north_mps * attack_t
                    offset_east_m = drift_east_mps * attack_t
                    spoofed_lat = true_lat + utils.meters_to_lat_offset(
                        offset_north_m
                    )
                    spoofed_lon = true_lon + utils.meters_to_lon_offset(
                        offset_east_m, true_lat
                    )
                    label = "attack"
                else:
                    spoofed_lat = true_lat
                    spoofed_lon = true_lon
                    attack_t = 0.0
                    label = "normal"

                row = {
                    "timestamp_unix_s": now,
                    "timestamp_iso": datetime.utcfromtimestamp(now).isoformat(),
                    "scenario": args.scenario,
                    "run_id": args.run_id,
                    "label": label,
                    "attack_elapsed_s": round(attack_t, 3),
                    "true_lat_deg": true_lat,
                    "true_lon_deg": true_lon,
                    "lat_deg": spoofed_lat,
                    "lon_deg": spoofed_lon,
                    "abs_alt_m": latest["abs_alt_m"],
                    "rel_alt_m": latest["rel_alt_m"],
                    "accel_x_mps2": latest["accel_x_mps2"],
                    "accel_y_mps2": latest["accel_y_mps2"],
                    "accel_z_mps2": latest["accel_z_mps2"],
                    "gyro_x_rps": latest["gyro_x_rps"],
                    "gyro_y_rps": latest["gyro_y_rps"],
                    "gyro_z_rps": latest["gyro_z_rps"],
                    "battery_remaining_pct": latest["battery_remaining_pct"],
                    "battery_voltage_v": latest["battery_voltage_v"],
                }
                writer.writerow(row)
                row_count += 1
                await asyncio.sleep(period)

    except IOError as e:
        logger.error(f"Failed to write CSV: {e}", exc_info=True)
        return
    finally:
        # Gracefully shutdown telemetry tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Land if we took off
        if took_off:
            print("Landing...")
            try:
                await drone.action.land()
                await asyncio.sleep(config.SLEEP_DURING_LANDING_S)
                logger.info("Landing complete.")
            except Exception as e:
                logger.warning(f"Land failed: {e}")

    # Validate output
    print(f"Logged {row_count} rows to {out_path}")
    utils.validate_csv_output(str(out_path), verbose=True)
    logger.info(f"GPS spoof flight logging complete: {row_count} samples")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

'''
# Gradual drift: 1.5 m/s northeast starting at t=10s, log for 60s
python3 simulation/gps_spoof_fly_and_log.py \
  --scenario spoof_drift --run-id 01 --duration 60 --rate 10 \
  --attack-start 10 --drift-rate-m-per-s 1.5 --drift-direction-deg 45

# Faster drift, different direction
python3 simulation/gps_spoof_fly_and_log.py \
  --scenario spoof_fast --run-id 01 --duration 60 --rate 10 \
  --attack-start 15 --drift-rate-m-per-s 3.0 --drift-direction-deg 90

# With takeoff
python3 simulation/gps_spoof_fly_and_log.py \
  --scenario spoof_drift --run-id 02 --duration 60 --rate 10 \
  --attack-start 10 --drift-rate-m-per-s 1.5 --drift-direction-deg 45 --do-takeoff
'''
