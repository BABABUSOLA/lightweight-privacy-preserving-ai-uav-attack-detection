"""
preflight_check.py – Best-effort preflight check for PX4 SITL via MAVSDK.

Usage
-----
  python preflight_check.py [OPTIONS]

Options
-------
  --system-address TEXT   MAVSDK connection string (default: udpin://0.0.0.0:14030)
  --timeout-s FLOAT       Max seconds to wait for connection and GPS health (default: 20.0)
  --arm-probe             Attempt arm → disarm cycle as an extra readiness check

Exit codes
----------
  0   All checks passed.
  1   Unexpected error (connection timeout, GPS timeout, etc.).
  2   Arm probe failed (ActionError from the flight controller).

Examples
--------
  # Basic health check on the default port:
  python3 preflight_check.py

  # Custom address with a longer timeout:
  python3 preflight_check.py --system-address udpin://0.0.0.0:14540 --timeout-s 30

  # Include an arm/disarm probe:
  python3 preflight_check.py --arm-probe

  # Combine all options:
  python3 preflight_check.py --system-address udpin://0.0.0.0:14540 --timeout-s 45 --arm-probe
"""
import argparse
import asyncio
import sys

import config
import utils
from mavsdk import System
from mavsdk.action import ActionError

logger = utils.setup_logging(__name__)

async def main() -> int:
    p = argparse.ArgumentParser(
        description="Best-effort preflight check for PX4 SITL via MAVSDK."
    )
    p.add_argument("--timeout-s", type=float, default=20.0)
    p.add_argument("--system-address", default=config.DEFAULT_SYSTEM_ADDRESS)
    p.add_argument(
        "--arm-probe",
        action="store_true",
        help="If set, try arm then immediately disarm (stronger signal than health alone).",
    )
    args = p.parse_args()

    drone = System()
    await drone.connect(system_address=args.system_address)

    logger.info("Running preflight checks...")
    await utils.wait_for_connection(drone, timeout_s=args.timeout_s)
    await utils.wait_for_health(drone, timeout_s=args.timeout_s)

    if args.arm_probe:
        logger.info("Running arm/disarm probe...")
        try:
            await drone.action.arm()
            await asyncio.sleep(0.25)
            await drone.action.disarm()
        except ActionError:
            logger.error("Arm probe failed.")
            return 2

    logger.info("Preflight checks passed.")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:
        logger.error(f"Preflight check failed: {e}")
        rc = 1
    sys.exit(rc)

