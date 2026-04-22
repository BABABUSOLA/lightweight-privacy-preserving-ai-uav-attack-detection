"""
fly_and_log.py
==============
Connects to PX4 SITL, optionally arms/takes off, and logs telemetry to CSV.

Logs position, IMU, and battery data at a configurable rate.
Uses timeout-protected connection waits to prevent indefinite hangs.

Usage:
    python3 fly_and_log.py --scenario hover --duration 60 --do-takeoff
    python3 fly_and_log.py --scenario square --duration 120

Output:
    CSV file saved to data/normal_flights/raw/
"""

import argparse
import asyncio
import csv
import logging
import time
from datetime import datetime
from pathlib import Path

from mavsdk import System

import config
import utils

# Setup logging
logger = utils.setup_logging(__name__)


def validate_args(args) -> None:
    """Validate command-line arguments."""
    if args.duration <= 0:
        raise ValueError("--duration must be positive")
    if args.rate <= 0:
        raise ValueError("--rate must be positive")
    if args.rate > 1000:
        logger.warning("--rate > 1000 Hz may cause performance issues")
    if len(args.scenario) == 0:
        raise ValueError("--scenario cannot be empty")


async def main() -> None:
    """Main flight logging coroutine."""
    parser = argparse.ArgumentParser(
        description="Log telemetry from PX4 SITL drone with optional takeoff.",
        epilog="""
Examples:
  python3 fly_and_log.py --scenario hover --duration 60
  python3 fly_and_log.py --scenario square --duration 120 --do-takeoff
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scenario", default="hover", help="Scenario label for output filename"
    )
    parser.add_argument("--run-id", default="01", help="Run identifier (e.g., 01, 02)")
    parser.add_argument(
        "--duration",
        type=float,
        default=config.DEFAULT_LOGGING_DURATION_S,
        help="Logging duration (seconds)",
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

    args = parser.parse_args()

    # Validate arguments
    try:
        validate_args(args)
    except ValueError as e:
        logger.error(f"Invalid arguments: {e}")
        parser.print_help()
        return

    # Setup output directory
    raw_dir = Path(config.DEFAULT_LOG_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Generate output filename
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"flight_{ts}.csv"
    out_path = raw_dir / out_name

    logger.info(f"Scenario: {args.scenario}, Run: {args.run_id}")
    logger.info(f"Duration: {args.duration}s, Rate: {args.rate} Hz")
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

    # Try to arm/takeoff if requested
    took_off = await utils.maybe_arm_and_takeoff(
        drone, args.do_takeoff, verbose=True
    )
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

    fieldnames = [
        "timestamp_unix_s",
        "timestamp_iso",
        "scenario",
        "run_id",
        "lat_deg",
        "lon_deg",
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

    # Log telemetry to CSV
    print(f"Logging for {args.duration:.1f}s at {args.rate:.1f} Hz -> {out_path}")
    start = time.time()
    period = 1.0 / args.rate
    row_count = 0

    try:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            while (time.time() - start) < args.duration:
                now = time.time()
                row = {
                    "timestamp_unix_s": now,
                    "timestamp_iso": datetime.utcfromtimestamp(now).isoformat(),
                    "scenario": args.scenario,
                    "run_id": args.run_id,
                    **latest,
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
    logger.info(f"Flight logging complete: {row_count} samples")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)