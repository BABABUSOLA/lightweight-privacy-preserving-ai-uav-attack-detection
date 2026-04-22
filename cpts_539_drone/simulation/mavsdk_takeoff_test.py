import asyncio

import config
import utils
from mavsdk import System

logger = utils.setup_logging(__name__)

async def run() -> None:
    drone = System()
    await drone.connect(system_address=config.DEFAULT_SYSTEM_ADDRESS)

    await utils.wait_for_connection(drone, timeout_s=15.0)
    await utils.wait_for_health(drone, timeout_s=20.0)

    took_off = await utils.maybe_arm_and_takeoff(drone, do_takeoff=True)
    if not took_off:
        logger.error("Unable to arm/takeoff; aborting test.")
        return

    logger.info("Hovering for 5 seconds...")
    await asyncio.sleep(5)

    logger.info("Landing...")
    await drone.action.land()

    logger.info("Done")


if __name__ == "__main__":
    asyncio.run(run())
