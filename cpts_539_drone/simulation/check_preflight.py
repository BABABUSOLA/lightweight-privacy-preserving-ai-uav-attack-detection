import argparse
import asyncio
import sys
import time

from mavsdk import System
from mavsdk.action import ActionError


async def wait_for_connection(drone: System, timeout_s: float) -> None:
    start = time.time()
    while True:
        try:
            async for state in drone.core.connection_state():
                if state.is_connected:
                    return
        except RuntimeError as e:
            if "Core plugin has not been initialized" not in str(e):
                raise

        if time.time() - start > timeout_s:
            raise TimeoutError("Timed out waiting for connection.")
        await asyncio.sleep(0.2)


async def wait_for_health(drone: System, timeout_s: float) -> None:
    start = time.time()
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            return
        if time.time() - start > timeout_s:
            raise TimeoutError("Timed out waiting for global/home position OK.")
        await asyncio.sleep(0.5)


async def main() -> int:
    p = argparse.ArgumentParser(
        description="Best-effort preflight check for PX4 SITL via MAVSDK."
    )
    p.add_argument("--timeout-s", type=float, default=20.0)
    p.add_argument("--system-address", default="udpin://0.0.0.0:14030")
    p.add_argument(
        "--arm-probe",
        action="store_true",
        help="If set, try arm then immediately disarm (stronger signal than health alone).",
    )
    args = p.parse_args()

    drone = System()
    await drone.connect(system_address=args.system_address)

    await wait_for_connection(drone, timeout_s=args.timeout_s)
    await wait_for_health(drone, timeout_s=args.timeout_s)

    if args.arm_probe:
        try:
            await drone.action.arm()
            await asyncio.sleep(0.25)
            await drone.action.disarm()
        except ActionError:
            return 2

    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception:
        rc = 1
    sys.exit(rc)

