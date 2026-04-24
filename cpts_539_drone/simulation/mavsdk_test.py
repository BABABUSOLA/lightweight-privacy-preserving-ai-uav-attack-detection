import asyncio
from mavsdk import System


async def run() -> None:
    drone = System()
    await drone.connect(system_address="udpin://0.0.0.0:14540")

    print("Waiting for connection...")
    start = asyncio.get_event_loop().time()
    connected = False
    while True:
        try:
            async for state in drone.core.connection_state():
                if state.is_connected:
                    print("Connected to drone")
                    connected = True
                    break
        except RuntimeError as e:
            # MAVSDK can briefly report uninitialized plugins right after connect().
            if "Core plugin has not been initialized" not in str(e):
                raise

        if connected:
            break

        if asyncio.get_event_loop().time() - start > 15.0:
            raise TimeoutError("Timed out waiting for MAVSDK core plugin/connection.")
        await asyncio.sleep(0.2)

    async for position in drone.telemetry.position():
        print(
            f"Position -> lat={position.latitude_deg:.6f}, "
            f"lon={position.longitude_deg:.6f}, "
            f"rel_alt={position.relative_altitude_m:.2f}m"
        )
        break

    async for health in drone.telemetry.health():
        print(
            "Health -> "
            f"global_pos_ok={health.is_global_position_ok}, "
            f"home_pos_ok={health.is_home_position_ok}"
        )
        break


if __name__ == "__main__":
    asyncio.run(run())
