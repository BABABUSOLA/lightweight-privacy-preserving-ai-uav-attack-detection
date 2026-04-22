"""
utils.py
========
Shared utility functions for simulation scripts.
Eliminates code duplication and ensures consistency across all flight scripts.
"""

import asyncio
import logging
import math
import time
from typing import Optional

from mavsdk import System
from mavsdk.action import ActionError

import config

logger = logging.getLogger(__name__)


def _safe_anext(awaitable, timeout_s: float):
    """Read one async stream item with timeout."""
    return asyncio.wait_for(anext(awaitable), timeout=timeout_s)


# ============================================================================
# Connection & Health Checks (WITH TIMEOUT GUARDS)
# ============================================================================

async def wait_for_connection(
    drone: System,
    timeout_s: Optional[float] = None,
    verbose: bool = True,
) -> None:
    """
    Wait for drone connection with timeout guard.

    Args:
        drone: MAVSDK System object.
        timeout_s: Timeout in seconds. Defaults to config.TIMEOUT_CONNECTION_S.
        verbose: If True, print status messages.

    Raises:
        TimeoutError: If connection not established within timeout.
        RuntimeError: If unexpected error occurs.
    """
    if timeout_s is None:
        timeout_s = config.TIMEOUT_CONNECTION_S

    if verbose:
        print(f"Waiting for drone connection (timeout={timeout_s}s)...")

    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_s:
            msg = (
                f"Connection timeout after {elapsed:.1f}s. "
                f"Is PX4 SITL running and listening on {config.DEFAULT_SYSTEM_ADDRESS}?"
            )
            logger.error(msg)
            raise TimeoutError(msg)

        try:
            state = await _safe_anext(
                drone.core.connection_state(), timeout_s=config.SLEEP_CONNECTION_LOOP_S
            )
            if state.is_connected:
                if verbose:
                    print(f"Connected to drone (elapsed={elapsed:.1f}s).")
                logger.info("Drone connected successfully.")
                return
        except asyncio.TimeoutError:
            pass
        except RuntimeError as e:
            error_str = str(e)
            # Only suppress this specific expected error during initialization
            if "Core plugin has not been initialized" not in error_str:
                logger.debug(f"Connection error (non-fatal): {e}")
        except Exception as e:
            logger.error(f"Unexpected error during connection check: {e}", exc_info=True)
            raise

        await asyncio.sleep(config.SLEEP_CONNECTION_LOOP_S)


async def wait_for_health(
    drone: System,
    timeout_s: Optional[float] = None,
    verbose: bool = True,
) -> None:
    """
    Wait for global and home position estimates with timeout guard.

    Args:
        drone: MAVSDK System object.
        timeout_s: Timeout in seconds. Defaults to config.TIMEOUT_HEALTH_S.
        verbose: If True, print status messages.

    Raises:
        TimeoutError: If health checks not satisfied within timeout.
    """
    if timeout_s is None:
        timeout_s = config.TIMEOUT_HEALTH_S

    if verbose:
        print(f"Waiting for global/home position estimate (timeout={timeout_s}s)...")

    start_time = time.time()

    async for health in drone.telemetry.health():
        elapsed = time.time() - start_time

        if verbose:
            status_str = (
                f"global_pos_ok={health.is_global_position_ok}, "
                f"home_pos_ok={health.is_home_position_ok}"
            )
            print(f"  Health: {status_str} (elapsed={elapsed:.1f}s)")

        if health.is_global_position_ok and health.is_home_position_ok:
            if verbose:
                print(f"Position estimates valid (elapsed={elapsed:.1f}s).")
            logger.info("Health checks passed: EKF converged.")
            return

        if elapsed > timeout_s:
            msg = (
                f"Health check timeout after {elapsed:.1f}s. "
                f"Global position ok={health.is_global_position_ok}, "
                f"Home position ok={health.is_home_position_ok}. "
                f"Is GPS working in SITL?"
            )
            logger.error(msg)
            raise TimeoutError(msg)

        await asyncio.sleep(config.SLEEP_HEALTH_LOOP_S)


async def maybe_arm_and_takeoff(
    drone: System,
    do_takeoff: bool,
    max_arm_retries: Optional[int] = None,
    verbose: bool = True,
) -> bool:
    """
    Attempt to arm and takeoff with retry logic and detailed error reporting.

    Args:
        drone: MAVSDK System object.
        do_takeoff: If False, skip arm/takeoff and return False immediately.
        max_arm_retries: Number of retry attempts. Defaults to config.MAX_ARM_RETRIES.
        verbose: If True, print status messages.

    Returns:
        True if takeoff sequence was initiated, False otherwise.
        Never raises; returns False on transient or permanent failures.
    """
    if not do_takeoff:
        if verbose:
            print("Takeoff disabled: skipping arm and takeoff.")
        return False

    if max_arm_retries is None:
        max_arm_retries = config.MAX_ARM_RETRIES

    # Ensure position is ready
    try:
        await wait_for_health(drone, verbose=verbose)
    except TimeoutError as e:
        logger.warning(f"Skipping takeoff due to health timeout: {e}")
        print(f"[WARN] Cannot confirm position readiness; skipping takeoff.")
        return False

    # ARM with retry
    for attempt in range(max_arm_retries):
        try:
            if verbose and attempt > 0:
                print(f"Arm attempt {attempt + 1}/{max_arm_retries}...")
            elif verbose:
                print("Attempting to arm...")

            await drone.action.arm()
            if verbose:
                print("Armed successfully.")
            logger.info("Drone armed.")
            break
        except ActionError as e:
            if attempt < max_arm_retries - 1:
                wait_time = config.ARM_RETRY_BACKOFF_FACTOR ** attempt
                if verbose:
                    print(f"  Arm failed: {e}. Retrying in {wait_time:.1f}s...")
                logger.warning(f"Arm attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(wait_time)
            else:
                if verbose:
                    print(f"[WARN] Arm failed after {max_arm_retries} attempts: {e}")
                logger.error(f"Arm failed after {max_arm_retries} attempts: {e}")
                return False

    # Takeoff
    try:
        if verbose:
            print("Attempting takeoff...")
        await drone.action.takeoff()
        if verbose:
            print("Takeoff command sent.")
        logger.info("Takeoff initiated.")
        await asyncio.sleep(config.SLEEP_AFTER_TAKEOFF_S)
        return True
    except ActionError as e:
        if verbose:
            print(f"[WARN] Takeoff failed: {e}. Logging will continue.")
        logger.warning(f"Takeoff failed: {e}")
        return False


async def wait_for_landed_hold_state(
    drone: System,
    timeout_s: float = 30.0,
    poll_s: float = 1.0,
    verbose: bool = True,
) -> bool:
    """
    Wait until the vehicle is safely landed and stabilized.

    Success condition:
        - flight mode is HOLD
        - armed is False
        - in_air is False
    """
    start = time.time()
    while (time.time() - start) < timeout_s:
        try:
            mode = await asyncio.wait_for(anext(drone.telemetry.flight_mode()), timeout=poll_s)
            armed = await asyncio.wait_for(anext(drone.telemetry.armed()), timeout=poll_s)
            in_air = await asyncio.wait_for(anext(drone.telemetry.in_air()), timeout=poll_s)
        except asyncio.TimeoutError:
            if verbose:
                print("Waiting for landing telemetry...")
            await asyncio.sleep(poll_s)
            continue
        except Exception as e:
            logger.warning(f"Landing gate telemetry read failed: {e}")
            await asyncio.sleep(poll_s)
            continue

        mode_name = getattr(mode, "name", str(mode))
        if mode_name == "HOLD" and (armed is False) and (in_air is False):
            if verbose:
                print("Landing gate passed: HOLD + disarmed + on-ground.")
            logger.info("Landing gate passed.")
            return True

        if verbose:
            print(
                f"Landing gate pending: mode={mode_name}, "
                f"armed={armed}, in_air={in_air}"
            )
        await asyncio.sleep(poll_s)

    logger.error(
        "Landing gate timeout: vehicle did not reach HOLD/disarmed/on-ground state "
        f"within {timeout_s:.1f}s."
    )
    return False


# ============================================================================
# Telemetry Streaming Helpers
# ============================================================================

async def read_position(drone: System, latest: dict) -> None:
    """
    Continuously update position fields in shared 'latest' dict.

    Fields updated:
        - lat_deg, lon_deg, abs_alt_m, rel_alt_m
    """
    async for p in drone.telemetry.position():
        latest["lat_deg"] = p.latitude_deg
        latest["lon_deg"] = p.longitude_deg
        latest["abs_alt_m"] = p.absolute_altitude_m
        latest["rel_alt_m"] = p.relative_altitude_m


async def read_imu(drone: System, latest: dict) -> None:
    """
    Continuously update IMU fields in shared 'latest' dict.

    Fields updated:
        - accel_x_mps2, accel_y_mps2, accel_z_mps2
        - gyro_x_rps, gyro_y_rps, gyro_z_rps
    """
    async for imu in drone.telemetry.imu():
        latest["accel_x_mps2"] = imu.acceleration_frd.forward_m_s2
        latest["accel_y_mps2"] = imu.acceleration_frd.right_m_s2
        latest["accel_z_mps2"] = imu.acceleration_frd.down_m_s2
        latest["gyro_x_rps"] = imu.angular_velocity_frd.forward_rad_s
        latest["gyro_y_rps"] = imu.angular_velocity_frd.right_rad_s
        latest["gyro_z_rps"] = imu.angular_velocity_frd.down_rad_s


async def read_battery(drone: System, latest: dict) -> None:
    """
    Continuously update battery fields in shared 'latest' dict.

    Fields updated:
        - battery_remaining_pct, battery_voltage_v
    """
    async for b in drone.telemetry.battery():
        latest["battery_remaining_pct"] = b.remaining_percent
        # voltage_v may not be available in all builds
        if hasattr(b, "voltage_v"):
            latest["battery_voltage_v"] = b.voltage_v
        else:
            latest["battery_voltage_v"] = None


# ============================================================================
# Geodetic Utilities
# ============================================================================

def meters_to_lat_offset(meters: float) -> float:
    """
    Convert North/South distance (meters) to latitude offset (degrees).

    Latitude offset is constant everywhere on Earth:
    1 degree = ~111,111 meters

    Args:
        meters: Distance in meters (positive = North, negative = South).

    Returns:
        Latitude offset in degrees.
    """
    return meters / config.METERS_PER_DEGREE_LAT


def meters_to_lon_offset(meters: float, latitude_deg: float) -> float:
    """
    Convert East/West distance (meters) to longitude offset (degrees).

    Longitude offset depends on latitude due to Earth's curvature:
    offset_deg = meters / (111,111 * cos(latitude))

    Args:
        meters: Distance in meters (positive = East, negative = West).
        latitude_deg: Reference latitude in degrees.

    Returns:
        Longitude offset in degrees.
    """
    lat_rad = math.radians(latitude_deg)
    cos_lat = math.cos(lat_rad)
    if abs(cos_lat) < 0.001:  # Avoid division by zero near poles
        return 0.0
    return meters / (config.METERS_PER_DEGREE_LON_AT_EQUATOR * cos_lat)


# ============================================================================
# CSV Output Validation
# ============================================================================

def validate_csv_output(
    csv_path: str,
    min_rows: Optional[int] = None,
    max_none_fraction: Optional[float] = None,
    verbose: bool = True,
) -> bool:
    """
    Validate CSV output file for data quality.

    Args:
        csv_path: Path to CSV file.
        min_rows: Minimum required rows. Defaults to config.MIN_CSV_ROWS_REQUIRED.
        max_none_fraction: Max allowed fraction of None values.
                           Defaults to config.MAX_NONE_FRACTION_ALLOWED.
        verbose: If True, print validation results.

    Returns:
        True if validation passed, False otherwise.
    """
    if min_rows is None:
        min_rows = config.MIN_CSV_ROWS_REQUIRED
    if max_none_fraction is None:
        max_none_fraction = config.MAX_NONE_FRACTION_ALLOWED

    try:
        import pandas as pd

        df = pd.read_csv(csv_path)

        # Check row count
        if len(df) < min_rows:
            msg = f"CSV has only {len(df)} rows; minimum required: {min_rows}"
            logger.warning(msg)
            if verbose:
                print(f"[WARN] {msg}")
            return False

        # Check none fraction
        total_cells = len(df) * len(df.columns)
        none_count = df.isna().sum().sum()
        none_fraction = none_count / total_cells if total_cells > 0 else 0.0

        if none_fraction > max_none_fraction:
            msg = (
                f"CSV has {none_fraction*100:.1f}% None values "
                f"(max allowed: {max_none_fraction*100:.1f}%)"
            )
            logger.warning(msg)
            if verbose:
                print(f"[WARN] {msg}")
            return False

        if verbose:
            print(
                f"[OK] CSV validation passed: {len(df)} rows, "
                f"{none_fraction*100:.1f}% None values"
            )
        logger.info(f"CSV validation passed: {len(df)} rows, {none_fraction*100:.1f}% None")
        return True

    except ImportError:
        logger.warning("pandas not available; skipping CSV validation")
        if verbose:
            print("[WARN] pandas not installed; skipping validation")
        return True
    except Exception as e:
        logger.error(f"CSV validation error: {e}", exc_info=True)
        if verbose:
            print(f"[ERROR] Validation failed: {e}")
        return False


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(name: str) -> logging.Logger:
    """
    Configure logging for a script.

    Args:
        name: Script name (usually __name__).

    Returns:
        Configured logger object.
    """
    logger = logging.getLogger(name)
    logger.setLevel(config.LOG_LEVEL)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(config.LOG_LEVEL)

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(console_handler)

    return logger
