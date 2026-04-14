import asyncio
from mavsdk import System


async def wait_for_connection(drone: System) -> None:
    print("Waiting for connection...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print("Connected to drone")
            return


async def wait_for_health(drone: System) -> None:
    print("Waiting for global and home position estimates...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("Position estimate is good")
            return


async def run() -> None:
    drone = System()
    await drone.connect(system_address="udpin://0.0.0.0:14540")

    await wait_for_connection(drone)
    await wait_for_health(drone)

    print("Arming...")
    await drone.action.arm()

    print("Taking off...")
    await drone.action.takeoff()

    print("Hovering for 5 seconds...")
    await asyncio.sleep(5)

    print("Landing...")
    await drone.action.land()

    print("Done")


if __name__ == "__main__":
    asyncio.run(run())
