"""
mission_square_fly_and_log.py
=============================
Fly a square mission in PX4 SITL and log telemetry to CSV.

Uploads a square mission with 4 waypoints, logs position/velocity/IMU/battery data
at a configurable rate.

Usage:
    python3 mission_square_fly_and_log.py --scenario square --duration 120 --do-takeoff
    python3 mission_square_fly_and_log.py --scenario square --duration 120 \\
        --mission-size-m 50 --mission-alt-m 20 --cruise-speed-mps 5
"""

import argparse
import asyncio
import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple

from mavsdk import System
from mavsdk.mission import MissionItem, MissionPlan

import config
import utils

# Setup logging
logger = utils.setup_logging(__name__)


def build_square_mission(
    origin_lat: float,
    origin_lon: float,
    size_m: float,
    rel_alt_m: float,
    cruise_speed_m_s: float,
) -> MissionPlan:
    """
    Build a square mission with 4 waypoints.

    Args:
        origin_lat: Reference latitude (degrees).
        origin_lon: Reference longitude (degrees).
        size_m: Side length of square (meters).
        rel_alt_m: Relative altitude for waypoints (meters).
        cruise_speed_m_s: Cruise speed for mission (m/s).

    Returns:
        MissionPlan object with 4 waypoints forming a square.
    """
    half = size_m / 2.0
    # Square centered on origin, order: NE, SE, SW, NW (relative to origin)
    corners_m = [
        (+half, +half),
        (-half, +half),
        (-half, -half),
        (+half, -half),
    ]

    items = []
    for north_m, east_m in corners_m:
        lat = origin_lat + utils.meters_to_lat_offset(north_m)
        lon = origin_lon + utils.meters_to_lon_offset(east_m, origin_lat)
        item = MissionItem(
            vehicle_action=MissionItem.VehicleAction.NONE,
            latitude_deg=lat,
            longitude_deg=lon,
            relative_altitude_m=rel_alt_m,
            speed_m_s=cruise_speed_m_s,
            is_fly_through=True,
            gimbal_pitch_deg=float("nan"),
            gimbal_yaw_deg=float("nan"),
            camera_action=MissionItem.CameraAction.NONE,
            loiter_time_s=0.0,
            camera_photo_interval_s=float("nan"),
            acceptance_radius_m=2.0,
            yaw_deg=float("nan"),
            camera_photo_distance_m=float("nan"),
        )
        items.append(item)

    plan = MissionPlan(items)
    plan.mission_items = items
    return plan


async def get_home_latlon(
    drone: System, timeout_s: float = 15.0
) -> Tuple[float, float]:
    """
    Get initial GPS position for mission origin.

    Args:
        drone: MAVSDK System object.
        timeout_s: Timeout in seconds.

    Returns:
        (latitude_deg, longitude_deg) tuple.

    Raises:
        TimeoutError: If position not received within timeout.
    """
    start = time.time()
    async for p in drone.telemetry.position():
        if p.latitude_deg != 0.0 or p.longitude_deg != 0.0:
            return (p.latitude_deg, p.longitude_deg)
        if time.time() - start > timeout_s:
            msg = f"No valid GPS position within {timeout_s}s"
            logger.error(msg)
            raise TimeoutError(msg)
        await asyncio.sleep(0.1)
    msg = "Position stream ended without data"
    logger.error(msg)
    raise TimeoutError(msg)


def validate_args(args) -> None:
    """Validate command-line arguments."""
    if args.duration <= 0:
        raise ValueError("--duration must be positive")
    if args.rate <= 0:
        raise ValueError("--rate must be positive")
    if args.rate > 1000:
        logger.warning("--rate > 1000 Hz may cause performance issues")
    if args.mission_size_m <= 0:
        raise ValueError("--mission-size-m must be positive")
    if args.mission_alt_m < 0:
        raise ValueError("--mission-alt-m must be non-negative")
    if args.cruise_speed_mps <= 0:
        raise ValueError("--cruise-speed-mps must be positive")


async def main() -> None:
    """Main mission flight logging coroutine."""
    parser = argparse.ArgumentParser(
        description="Fly a square mission in PX4 SITL and log telemetry.",
        epilog="""
Examples:
  python3 mission_square_fly_and_log.py --scenario square --duration 120
  python3 mission_square_fly_and_log.py --scenario square --duration 120 \\
      --mission-size-m 50 --mission-alt-m 20 --cruise-speed-mps 5 --do-takeoff
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scenario", default="square", help="Scenario label for output"
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
        "--do-takeoff", action="store_true", help="Arm and execute mission"
    )
    parser.add_argument(
        "--system-address",
        default=config.DEFAULT_SYSTEM_ADDRESS,
        help="MAVSDK system address",
    )
    parser.add_argument(
        "--mission-size-m",
        type=float,
        default=25.0,
        help="Square mission size (meters)",
    )
    parser.add_argument(
        "--mission-alt-m",
        type=float,
        default=10.0,
        help="Mission relative altitude (meters)",
    )
    parser.add_argument(
        "--cruise-speed-mps",
        type=float,
        default=4.0,
        help="Mission cruise speed (m/s)",
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
    logger.info(
        f"Mission: {args.mission_size_m}m square @ "
        f"{args.mission_alt_m}m altitude, {args.cruise_speed_mps} m/s cruise"
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

    # Wait for health and get mission origin
    try:
        await utils.wait_for_health(drone, verbose=True)
        origin_lat, origin_lon = await get_home_latlon(drone)
        logger.info(f"Mission origin: {origin_lat:.6f}, {origin_lon:.6f}")
    except TimeoutError as e:
        logger.error(str(e))
        return
    except Exception as e:
        logger.error(f"Error during preflight: {e}", exc_info=True)
        return

    # Initialize telemetry snapshot dict
    latest = {
        "lat_deg": None,
        "lon_deg": None,
        "abs_alt_m": None,
        "rel_alt_m": None,
        "vel_n_m_s": None,
        "vel_e_m_s": None,
        "vel_d_m_s": None,
        "accel_x_mps2": None,
        "accel_y_mps2": None,
        "accel_z_mps2": None,
        "gyro_x_rps": None,
        "gyro_y_rps": None,
        "gyro_z_rps": None,
        "battery_remaining_pct": None,
        "battery_voltage_v": None,
    }

    # Create additional telemetry reader for velocity
    async def read_velocity():
        """Continuously update velocity fields in shared 'latest' dict."""
        async for v in drone.telemetry.velocity_ned():
            latest["vel_n_m_s"] = v.north_m_s
            latest["vel_e_m_s"] = v.east_m_s
            latest["vel_d_m_s"] = v.down_m_s

    # Create background telemetry streaming tasks
    tasks = [
        asyncio.create_task(utils.read_position(drone, latest)),
        asyncio.create_task(read_velocity()),
        asyncio.create_task(utils.read_imu(drone, latest)),
        asyncio.create_task(utils.read_battery(drone, latest)),
    ]

    flew_mission = False
    row_count = 0

    fieldnames = [
        "timestamp_unix_s",
        "timestamp_iso",
        "scenario",
        "run_id",
        *latest.keys(),
    ]

    try:
        if args.do_takeoff:
            # Build and upload mission
            try:
                logger.info("Building square mission...")
                plan = build_square_mission(
                    origin_lat=origin_lat,
                    origin_lon=origin_lon,
                    size_m=args.mission_size_m,
                    rel_alt_m=args.mission_alt_m,
                    cruise_speed_m_s=args.cruise_speed_mps,
                )
                logger.info(f"Uploading mission with {len(plan.mission_items)} waypoints...")
                await drone.mission.set_return_to_launch_after_mission(True)
                await drone.mission.upload_mission(plan)

                # Arm and start mission
                logger.info("Arming...")
                await drone.action.arm()
                logger.info("Starting mission...")
                await drone.mission.start_mission()
                flew_mission = True
                logger.info("Mission started successfully")
            except Exception as e:
                logger.warning(f"Mission start failed: {e}. Logging will continue.")
        else:
            logger.info("Takeoff disabled: logging telemetry only")

        # Main logging loop
        logger.info(f"Logging for {args.duration:.1f}s at {args.rate:.1f} Hz -> {out_path}")
        start = time.time()
        period = 1.0 / args.rate

        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            while (time.time() - start) < args.duration:
                now = time.time()
                writer.writerow(
                    {
                        "timestamp_unix_s": now,
                        "timestamp_iso": datetime.utcfromtimestamp(now).isoformat(),
                        "scenario": args.scenario,
                        "run_id": args.run_id,
                        **latest,
                    }
                )
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

        # Land if we started mission
        if flew_mission:
            logger.info("Landing...")
            try:
                await drone.action.land()
                await asyncio.sleep(config.SLEEP_DURING_LANDING_S)
                logger.info("Landing complete.")
            except Exception as e:
                logger.warning(f"Land failed: {e}")

    # Validate output
    logger.info(f"Logged {row_count} rows to {out_path}")
    utils.validate_csv_output(str(out_path), verbose=True)
    logger.info(f"Mission flight logging complete: {row_count} samples")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

    logger.info("Done. Log saved.")