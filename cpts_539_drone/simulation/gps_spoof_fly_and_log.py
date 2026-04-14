"""
GPS Spoofing Flight Logger
===========================
Logs telemetry from PX4 SITL.  After --attack-start seconds the *logged*
GPS coordinates are offset by a drift that grows linearly, simulating a
gradual GPS spoofing attack.

IMU and battery values are always logged truthfully, so the resulting CSV
contains a realistic GPS-vs-IMU inconsistency that the LSTM autoencoder
should learn to detect.

Columns marked "spoofed_*" contain the attacker-modified values.
Columns "true_lat_deg" / "true_lon_deg" keep the real values for
ground-truth evaluation.
"""

import argparse
import asyncio
import csv
import math
import time
from datetime import datetime
from pathlib import Path

from mavsdk import System
from mavsdk.action import ActionError


# ── helpers ──────────────────────────────────────────────────────────────

def meters_to_lat_offset(meters: float) -> float:
    """Approximate meter offset → latitude degree offset."""
    return meters / 111_111.0


def meters_to_lon_offset(meters: float, lat_deg: float) -> float:
    """Approximate meter offset → longitude degree offset at given lat."""
    return meters / (111_111.0 * math.cos(math.radians(lat_deg)))

async def wait_for_connection(drone: System) -> None:
    print("[*] Waiting for drone connection...")
    while True:
        try:
            async for state in drone.core.connection_state():
                if state.is_connected:
                    print("[+] Connected to drone.")
                    return
        except RuntimeError as e:
            if "Core plugin has not been initialized" not in str(e):
                raise
        await asyncio.sleep(0.2)


async def wait_for_health(drone: System) -> None:
    print("[*] Waiting for global/home position estimate...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("[+] Health checks passed.")
            return
        await asyncio.sleep(1)


async def maybe_arm_and_takeoff(drone: System, do_takeoff: bool) -> bool:
    if not do_takeoff:
        print("[*] --do-takeoff not set: skipping arm/takeoff.")
        return False
    await wait_for_health(drone)
    try:
        print("[*] Arming...")
        await drone.action.arm()
        print("[+] Armed.")
    except ActionError as e:
        print(f"[!] Arm failed: {e}. Logging continues without takeoff.")
        return False
    try:
        print("[*] Taking off...")
        await drone.action.takeoff()
        await asyncio.sleep(3)
        return True
    except ActionError as e:
        print(f"[!] Takeoff failed: {e}. Logging continues.")
        return False


# ── main ─────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log flight telemetry with optional GPS spoofing."
    )
    # flight params
    parser.add_argument("--scenario", default="spoof_drift")
    parser.add_argument("--run-id", default="01")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="Total logging duration (seconds)")
    parser.add_argument("--rate", type=float, default=10.0,
                        help="Logging rate (Hz)")
    parser.add_argument("--do-takeoff", action="store_true")

    # attack params
    parser.add_argument("--attack-start", type=float, default=10.0,
                        help="Seconds after logging starts to begin spoofing")
    parser.add_argument("--drift-rate-m-per-s", type=float, default=1.5,
                        help="GPS drift speed in meters/second (north-east)")
    parser.add_argument("--drift-direction-deg", type=float, default=45.0,
                        help="Direction of drift in degrees (0=north, 90=east)")

    args = parser.parse_args()

    # output
    raw_dir = Path("data") / "attack_flights" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"{args.scenario}_run{args.run_id}_{ts}.csv"
    out_path = raw_dir / out_name

    # connect
    drone = System()
    await drone.connect(system_address="udpin://0.0.0.0:14030")
    await wait_for_connection(drone)
    took_off = await maybe_arm_and_takeoff(drone, args.do_takeoff)

    # shared latest telemetry
    latest: dict = {
        "lat_deg": None, "lon_deg": None,
        "abs_alt_m": None, "rel_alt_m": None,
        "accel_x_mps2": None, "accel_y_mps2": None, "accel_z_mps2": None,
        "gyro_x_rps": None, "gyro_y_rps": None, "gyro_z_rps": None,
        "battery_remaining_pct": None, "battery_voltage_v": None,
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
            latest["battery_voltage_v"] = getattr(b, "voltage_v", None)

    tasks = [
        asyncio.create_task(read_position()),
        asyncio.create_task(read_imu()),
        asyncio.create_task(read_battery()),
    ]

    # precompute drift components
    dir_rad = math.radians(args.drift_direction_deg)
    drift_north_mps = args.drift_rate_m_per_s * math.cos(dir_rad)
    drift_east_mps = args.drift_rate_m_per_s * math.sin(dir_rad)

    fieldnames = [
        "timestamp_unix_s", "timestamp_iso", "scenario", "run_id",
        "label",                       # "normal" or "attack"
        "attack_elapsed_s",            # seconds since spoofing began (0 if normal)
        "true_lat_deg", "true_lon_deg",  # ground-truth GPS
        "lat_deg", "lon_deg",          # what the "sensor" reports (spoofed if attack)
        "abs_alt_m", "rel_alt_m",
        "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps",
        "battery_remaining_pct", "battery_voltage_v",
    ]

    print(f"[*] Logging {args.duration:.0f}s @ {args.rate:.0f} Hz -> {out_path}")
    print(f"[*] Attack starts at t={args.attack_start:.1f}s, "
          f"drift={args.drift_rate_m_per_s:.1f} m/s @ {args.drift_direction_deg:.0f}°")

    start = time.time()
    period = 1.0 / args.rate

    try:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            while (time.time() - start) < args.duration:
                now = time.time()
                elapsed = now - start

                true_lat = latest["lat_deg"]
                true_lon = latest["lon_deg"]

                # Compute spoofed GPS if past attack start
                if elapsed >= args.attack_start and true_lat is not None:
                    attack_t = elapsed - args.attack_start
                    offset_north_m = drift_north_mps * attack_t
                    offset_east_m = drift_east_mps * attack_t
                    spoofed_lat = true_lat + meters_to_lat_offset(offset_north_m)
                    spoofed_lon = true_lon + meters_to_lon_offset(offset_east_m, true_lat)
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
                await asyncio.sleep(period)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if took_off:
            print("[*] Landing...")
            try:
                await drone.action.land()
                await asyncio.sleep(5)
            except ActionError as e:
                print(f"[!] Land failed: {e}")

    print(f"[+] Done. Saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

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
