import argparse
import asyncio
import csv
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from mavsdk import System
from mavsdk.action import ActionError
from mavsdk.mission import MissionItem, MissionPlan


def meters_to_lat_offset(meters: float) -> float:
    return meters / 111_111.0


def meters_to_lon_offset(meters: float, lat_deg: float) -> float:
    return meters / (111_111.0 * math.cos(math.radians(lat_deg)))


async def wait_for_connection(drone: System) -> None:
    print("Waiting for drone connection...")
    while True:
        try:
            async for state in drone.core.connection_state():
                if state.is_connected:
                    print("Connected to drone.")
                    return
        except RuntimeError as e:
            if "Core plugin has not been initialized" not in str(e):
                raise
        await asyncio.sleep(0.2)


async def wait_for_health(drone: System) -> None:
    print("Waiting for global/home position estimate...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("Health checks passed.")
            return
        await asyncio.sleep(1)


async def get_home_latlon(drone: System, timeout_s: float = 15.0) -> Tuple[float, float]:
    """
    Get a reasonable reference lat/lon for mission waypoints.
    Uses telemetry.position() once it becomes available.
    """
    start = time.time()
    async for p in drone.telemetry.position():
        if p.latitude_deg != 0.0 or p.longitude_deg != 0.0:
            return (p.latitude_deg, p.longitude_deg)
        if time.time() - start > timeout_s:
            raise TimeoutError("Timed out waiting for non-zero GPS position.")
        await asyncio.sleep(0.1)
    raise TimeoutError("No position stream received.")


def build_square_mission(
    origin_lat: float,
    origin_lon: float,
    size_m: float,
    rel_alt_m: float,
    cruise_speed_m_s: float,
    rtl: bool,
) -> MissionPlan:
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
        lat = origin_lat + meters_to_lat_offset(north_m)
        lon = origin_lon + meters_to_lon_offset(east_m, origin_lat)
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
    # Return-to-launch behavior is controlled by vehicle settings; we keep it explicit here too.
    plan.mission_items = items
    return plan


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fly a small square mission in PX4 SITL and log telemetry to CSV."
    )
    parser.add_argument("--scenario", default="square", help="Scenario label for this run")
    parser.add_argument("--run-id", default="01", help="Run identifier (e.g., 01, 02)")
    parser.add_argument("--duration", type=float, default=90.0, help="Logging duration in seconds")
    parser.add_argument("--rate", type=float, default=10.0, help="Logging rate (Hz)")
    parser.add_argument("--do-takeoff", action="store_true", help="If set, arm + fly the mission")
    parser.add_argument("--mission-size-m", type=float, default=25.0, help="Square size (meters)")
    parser.add_argument("--mission-alt-m", type=float, default=10.0, help="Relative altitude (meters)")
    parser.add_argument("--cruise-speed-mps", type=float, default=4.0, help="Mission cruise speed (m/s)")
    args = parser.parse_args()

    raw_dir = Path("data") / "normal_flights" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = raw_dir / f"{args.scenario}_run{args.run_id}_{ts}.csv"

    drone = System()
    await drone.connect(system_address="udpin://0.0.0.0:14030")
    await wait_for_connection(drone)

    await wait_for_health(drone)
    origin_lat, origin_lon = await get_home_latlon(drone)

    # Latest telemetry snapshot
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

    async def read_position():
        async for p in drone.telemetry.position():
            latest["lat_deg"] = p.latitude_deg
            latest["lon_deg"] = p.longitude_deg
            latest["abs_alt_m"] = p.absolute_altitude_m
            latest["rel_alt_m"] = p.relative_altitude_m

    async def read_velocity():
        async for v in drone.telemetry.velocity_ned():
            latest["vel_n_m_s"] = v.north_m_s
            latest["vel_e_m_s"] = v.east_m_s
            latest["vel_d_m_s"] = v.down_m_s

    async def read_imu():
        async for imu in drone.telemetry.imu():
            latest["accel_x_mps2"] = imu.acceleration_frd.forward_m_s2
            latest["accel_y_mps2"] = imu.acceleration_frd.right_m_s2
            latest["accel_z_mps2"] = imu.acceleration_frd.down_m_s2
            latest["gyro_x_rps"] = imu.angular_velocity_frd.forward_rad_s
            latest["gyro_y_rps"] = imu.angular_velocity_frd.right_rad_s
            latest["gyro_z_rps"] = imu.angular_velocity_frd.down_rad_s

    async def read_battery():
        async for b in drone.telemetry.battery():
            latest["battery_remaining_pct"] = b.remaining_percent
            latest["battery_voltage_v"] = getattr(b, "voltage_v", None)

    tasks = [
        asyncio.create_task(read_position()),
        asyncio.create_task(read_velocity()),
        asyncio.create_task(read_imu()),
        asyncio.create_task(read_battery()),
    ]

    flew_mission = False
    try:
        if args.do_takeoff:
            print("Uploading square mission...")
            plan = build_square_mission(
                origin_lat=origin_lat,
                origin_lon=origin_lon,
                size_m=args.mission_size_m,
                rel_alt_m=args.mission_alt_m,
                cruise_speed_m_s=args.cruise_speed_mps,
                rtl=True,
            )
            await drone.mission.set_return_to_launch_after_mission(True)
            await drone.mission.upload_mission(plan)

            print("Arming...")
            await drone.action.arm()
            print("Starting mission...")
            await drone.mission.start_mission()
            flew_mission = True
        else:
            print("DO_TAKEOFF is False: skipping arming/mission. Logging only.")

        fieldnames = [
            "timestamp_unix_s",
            "timestamp_iso",
            "scenario",
            "run_id",
            *latest.keys(),
        ]

        print(f"Logging for {args.duration:.1f}s at {args.rate:.1f} Hz -> {out_path}")
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
                await asyncio.sleep(period)

    except ActionError as e:
        print(f"[WARN] MAVSDK action error: {e}")
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if flew_mission:
            print("Landing...")
            try:
                await drone.action.land()
                await asyncio.sleep(5)
            except ActionError as e:
                print(f"[WARN] Land failed: {e}")

    print("Done. Log saved.")


if __name__ == "__main__":
    asyncio.run(main())

