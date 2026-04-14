import argparse
import asyncio
import csv
import os
import time
from datetime import datetime
from pathlib import Path

from mavsdk import System
from mavsdk.action import ActionError


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
    """
    Wait until PX4 reports good global + home position.
    In SITL this should become true after a few seconds.
    """
    print("Waiting for global/home position estimate...")
    async for health in drone.telemetry.health():
        print(
            "  Health status -> "
            f"global_pos_ok={health.is_global_position_ok}, "
            f"home_pos_ok={health.is_home_position_ok}"
        )
        if health.is_global_position_ok and health.is_home_position_ok:
            print("Health checks passed.")
            return
        await asyncio.sleep(1)


async def maybe_arm_and_takeoff(drone: System, do_takeoff: bool) -> bool:
    """
    Try to arm + take off.
    Returns True if takeoff sequence was started, False otherwise.
    Never raises; prints errors and lets logging continue.
    """
    if not do_takeoff:
        print("DO_TAKEOFF is False: skipping arm/takeoff.")
        return False

    await wait_for_health(drone)

    print("Attempting to arm...")
    try:
        await drone.action.arm()
        print("Armed.")
    except ActionError as e:
        print(f"[WARN] Arm failed: {e}. Logging will continue without takeoff.")
        return False

    print("Attempting takeoff...")
    try:
        await drone.action.takeoff()
        print("Takeoff command sent.")
        await asyncio.sleep(3)
        return True
    except ActionError as e:
        print(f"[WARN] Takeoff failed: {e}. Logging will continue.")
        return False


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="hover", help="Scenario label for this run")
    parser.add_argument("--run-id", default="01", help="Run identifier (e.g., 01, 02)")
    parser.add_argument("--duration", type=float, default=60.0, help="Logging duration in seconds")
    parser.add_argument("--rate", type=float, default=10.0, help="Logging rate (Hz)")
    parser.add_argument("--do-takeoff", action="store_true", help="If set, try to arm + takeoff")
    args = parser.parse_args()

    # Output directories
    raw_dir = Path("data") / "normal_flights" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Output CSV file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"{args.scenario}_run{args.run_id}_{ts}.csv"
    out_path = raw_dir / out_name

    # Connect to PX4 SITL
    drone = System()
    await drone.connect(system_address="udpin://0.0.0.0:14030")

    await wait_for_connection(drone)

    # Try to arm/takeoff if requested, but keep logging even if it fails
    took_off = await maybe_arm_and_takeoff(drone, args.do_takeoff)

    # Shared latest telemetry snapshot
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

    async def read_position():
        async for p in drone.telemetry.position():
            latest["lat_deg"] = p.latitude_deg
            latest["lon_deg"] = p.longitude_deg
            latest["abs_alt_m"] = p.absolute_altitude_m
            latest["rel_alt_m"] = p.relative_altitude_m

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
            # voltage_v may not be available in all builds
            latest["battery_voltage_v"] = getattr(b, "voltage_v", None)

    tasks = [
        asyncio.create_task(read_position()),
        asyncio.create_task(read_imu()),
        asyncio.create_task(read_battery()),
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

    print(f"Logging for {args.duration:.1f}s at {args.rate:.1f} Hz -> {out_path}")
    start = time.time()
    period = 1.0 / args.rate

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
                await asyncio.sleep(period)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if took_off:
            print("Landing...")
            try:
                await drone.action.land()
                await asyncio.sleep(5)
            except ActionError as e:
                print(f"[WARN] Land failed: {e}")

    print("Done. Log saved.")


if __name__ == "__main__":
    asyncio.run(main())