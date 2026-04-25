import argparse
import asyncio
import csv
import math
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple

from mavsdk import System
from mavsdk.mission import MissionItem, MissionPlan

import config
import utils

logger = utils.setup_logging(__name__)

async def get_home_latlon(
    drone: System, timeout_s: float = 15.0
) -> Tuple[float, float]:
    """Get initial GPS position for mission origin."""
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


def build_triangle_mission(
    origin_lat: float,
    origin_lon: float,
    size_m: float,
    rel_alt_m: float,
    cruise_speed_m_s: float,
) -> MissionPlan:
    # Equilateral-ish triangle around origin (north/east offsets in meters)
    r = size_m / 2.0
    pts_m = [
        (+r, 0.0),
        (-r / 2.0, +r * 0.866),
        (-r / 2.0, -r * 0.866),
    ]

    items = []
    for north_m, east_m in pts_m:
        lat = origin_lat + utils.meters_to_lat_offset(north_m)
        lon = origin_lon + utils.meters_to_lon_offset(east_m, origin_lat)
        items.append(
            MissionItem(
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
        )
    plan = MissionPlan(items)
    plan.mission_items = items
    return plan


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


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fly a small triangle mission in PX4 SITL and log telemetry to CSV."
    )
    parser.add_argument(
        "--system-address",
        default=config.DEFAULT_SYSTEM_ADDRESS,
        help="MAVSDK system address",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Override output directory (default: {config.DEFAULT_LOG_DIR})",
    )
    parser.add_argument("--scenario", default="triangle", help="Scenario label for this run")
    parser.add_argument("--run-id", default="01", help="Run identifier (e.g., 01, 02)")
    parser.add_argument(
        "--duration",
        type=float,
        default=config.DEFAULT_LOGGING_DURATION_S,
        help="Logging duration in seconds",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=config.DEFAULT_LOGGING_RATE_HZ,
        help="Logging rate (Hz)",
    )
    parser.add_argument("--do-takeoff", action="store_true", help="If set, arm + fly the mission")
    parser.add_argument(
        "--debug-log-on-fail",
        action="store_true",
        help=(
            "If set, still write CSV telemetry even when --do-takeoff is requested "
            "but mission start fails."
        ),
    )
    parser.add_argument("--mission-size-m", type=float, default=25.0, help="Triangle size (meters)")
    parser.add_argument("--mission-alt-m", type=float, default=10.0, help="Relative altitude (meters)")
    parser.add_argument("--cruise-speed-mps", type=float, default=4.0, help="Mission cruise speed (m/s)")
    args = parser.parse_args()

    try:
        validate_args(args)
    except ValueError as e:
        logger.error(f"Invalid arguments: {e}")
        parser.print_help()
        return 1

    if args.output_dir:
        raw_dir = Path(args.output_dir)
    else:
        raw_dir = Path(config.DEFAULT_LOG_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = raw_dir / f"{args.scenario}_run{args.run_id}_{ts}.csv"
    out_tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    logger.info(f"Scenario: {args.scenario}, Run: {args.run_id}")
    logger.info(f"Duration: {args.duration}s, Rate: {args.rate} Hz")
    logger.info(
        f"Mission: {args.mission_size_m}m triangle @ "
        f"{args.mission_alt_m}m altitude, {args.cruise_speed_mps} m/s cruise"
    )
    logger.info(f"Output: {out_path}")

    drone = System()
    try:
        await drone.connect(system_address=args.system_address)
        logger.info(f"Connecting to {args.system_address}...")
    except Exception as e:
        logger.error(f"Failed to initiate connection: {e}", exc_info=True)
        return 1

    try:
        await utils.wait_for_connection(drone, verbose=True)
        await utils.wait_for_health(drone, verbose=True)
        origin_lat, origin_lon = await get_home_latlon(drone)
        logger.info(f"Mission origin: {origin_lat:.6f}, {origin_lon:.6f}")
    except TimeoutError as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.error(f"Error during preflight: {e}", exc_info=True)
        return 1

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

    async def read_velocity():
        async for v in drone.telemetry.velocity_ned():
            latest["vel_n_m_s"] = v.north_m_s
            latest["vel_e_m_s"] = v.east_m_s
            latest["vel_d_m_s"] = v.down_m_s

    tasks = [
        asyncio.create_task(utils.read_position(drone, latest)),
        asyncio.create_task(read_velocity()),
        asyncio.create_task(utils.read_imu(drone, latest)),
        asyncio.create_task(utils.read_battery(drone, latest)),
    ]

    flew_mission = False
    row_count = 0
    logging_ok = False
    landing_ok = False
    try:
        if not args.do_takeoff:
            logger.warning(
                "Takeoff not requested (--do-takeoff not set); skipping CSV logging "
                "because this run did not execute a flight."
            )
            return 1

        if args.do_takeoff:
            try:
                logger.info("Building triangle mission...")
                plan = build_triangle_mission(
                    origin_lat=origin_lat,
                    origin_lon=origin_lon,
                    size_m=args.mission_size_m,
                    rel_alt_m=args.mission_alt_m,
                    cruise_speed_m_s=args.cruise_speed_mps,
                )
                logger.info(
                    f"Uploading mission with {len(plan.mission_items)} waypoints..."
                )
                await drone.mission.set_return_to_launch_after_mission(True)
                await drone.mission.upload_mission(plan)

                logger.info("Arming...")
                await drone.action.arm()
                logger.info("Starting mission...")
                await drone.mission.start_mission()
                flew_mission = True
                logger.info("Mission started successfully")
            except Exception as e:
                if not args.debug_log_on_fail:
                    logger.warning(
                        f"Mission start failed: {e}. Skipping CSV logging. "
                        "Use --debug-log-on-fail to capture telemetry for debugging."
                    )
                    return 1
                logger.warning(
                    f"Mission start failed: {e}. Debug override enabled; "
                    "logging telemetry without successful flight."
                )

        fieldnames = [
            "timestamp_unix_s",
            "timestamp_iso",
            "scenario",
            "run_id",
            *latest.keys(),
        ]

        logger.info(f"Logging for {args.duration:.1f}s at {args.rate:.1f} Hz -> {out_tmp_path}")
        start = time.time()
        period = 1.0 / args.rate

        with out_tmp_path.open("w", newline="", encoding="utf-8") as f:
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
        logging_ok = row_count > 0

    except IOError as e:
        logger.error(f"Failed to write CSV: {e}", exc_info=True)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if flew_mission:
            logger.info("Landing...")
            try:
                await drone.action.land()
                landing_ok = await utils.wait_for_landed_hold_state(
                    drone,
                    timeout_s=max(config.SLEEP_DURING_LANDING_S, 20.0),
                    poll_s=1.0,
                    verbose=True,
                )
                if landing_ok:
                    logger.info("Landing complete and verified.")
            except Exception as e:
                logger.warning(f"Land failed: {e}")
                landing_ok = False

    if not logging_ok:
        logger.error("Run failed: telemetry logging did not complete successfully.")
    if not landing_ok:
        logger.error("Run failed: landing was not confirmed.")

    csv_ok = logging_ok and utils.validate_csv_output(str(out_tmp_path), verbose=True)
    if not csv_ok:
        logger.error("Run failed: temporary CSV validation did not pass.")

    if logging_ok and landing_ok and csv_ok:
        try:
            out_tmp_path.replace(out_path)
            logger.info(f"Logged {row_count} rows to {out_path}")
            logger.info(f"Triangle mission flight logging complete: {row_count} samples")
            return 0
        except Exception as e:
            logger.error(f"Failed to promote temp CSV to final output: {e}", exc_info=True)

    try:
        if out_tmp_path.exists():
            out_tmp_path.unlink()
            logger.info(f"Deleted temporary CSV due to failed run: {out_tmp_path}")
    except Exception as e:
        logger.warning(f"Failed to delete temporary CSV {out_tmp_path}: {e}")
    return 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
        sys.exit(rc)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

