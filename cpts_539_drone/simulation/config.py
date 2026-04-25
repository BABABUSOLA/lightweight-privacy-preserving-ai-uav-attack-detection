"""
config.py
=========
Centralized configuration for all simulation scripts.
Avoids scattering magic numbers and enables easy parameter tuning.
"""

# ============================================================================
# SITL Connection Settings
# ============================================================================
DEFAULT_SYSTEM_ADDRESS = "udpin://0.0.0.0:14030"  # Default PX4 SITL telemetry port
"""Primary system address for drone connection (SITL on UDP port 14030)."""

# ============================================================================
# Timeout Settings (seconds)
# ============================================================================
TIMEOUT_CONNECTION_S = 20.0
"""Max time to wait for drone connection."""

TIMEOUT_HEALTH_S = 30.0
"""Max time to wait for global/home position estimates."""

TIMEOUT_HEARTBEAT_S = 15.0
"""Max time to wait for MAVLink heartbeat (gps_input_spoofer)."""

TIMEOUT_SUBPROCESS_S = 3600.0
"""Max time to allow subprocess to run (1 hour for batch flights)."""

# ============================================================================
# Sleep/Loop Intervals
# ============================================================================
SLEEP_CONNECTION_LOOP_S = 0.2
"""Poll interval while checking connection state."""

SLEEP_HEALTH_LOOP_S = 0.5
"""Poll interval while checking health status."""

SLEEP_ACTION_RETRY_S = 1.0
"""Wait time between retries for arm/takeoff failures."""

# ============================================================================
# Telemetry & Logging Settings
# ============================================================================
DEFAULT_LOGGING_DURATION_S = 60.0
"""Default flight duration for telemetry logging."""

DEFAULT_LOGGING_RATE_HZ = 10.0
"""Default telemetry sample rate (10 Hz = ~1 sample every 100ms)."""

DEFAULT_LOG_DIR = "data/normal_flights/raw"
"""Default directory for raw flight logs (CSV)."""

DEFAULT_ATTACK_LOG_DIR = "data/attack_flights/raw"
"""Default directory for attack-injected flight logs."""

# ============================================================================
# Flight Action Timeouts
# ============================================================================
SLEEP_AFTER_ARM_S = 1.0
"""Wait time after arming before takeoff attempt."""

SLEEP_AFTER_TAKEOFF_S = 3.0
"""Wait time after takeoff command before normal logging begins."""

SLEEP_AFTER_MISSION_S = 2.0
"""Wait time after mission completion before landing."""

SLEEP_DURING_LANDING_S = 20.0
"""Wait time while landing to stabilize."""

# ============================================================================
# GPS Spoofing Parameters (gps_input_spoofer.py)
# ============================================================================
GPS_DRIFT_RATE_MPS = 1.5
"""Default GPS drift rate (meters/second)."""

GPS_DRIFT_DIRECTION_DEG = 45.0
"""Default drift direction (degrees, 0=North, 90=East)."""

GPS_SPOOF_MESSAGE_RATE_HZ = 10.0
"""Rate at which to send GPS_INPUT messages."""

GPS_SPOOF_SAT_COUNT = 12
"""Number of satellites to report in spoofed message."""

GPS_SPOOF_HDOP = 0.8
"""Horizontal dilution of precision (lower = better)."""

GPS_SPOOF_VDOP = 1.2
"""Vertical dilution of precision."""

GPS_SPOOF_ACCURACY_M = 0.7
"""Horizontal accuracy (meters)."""

GPS_SPOOF_VERTICAL_ACCURACY_M = 1.5
"""Vertical accuracy (meters)."""

GPS_SPOOF_SPEED_ACCURACY_MPS = 0.2
"""Speed accuracy (m/s)."""

# ============================================================================
# Geodetic Constants
# ============================================================================
METERS_PER_DEGREE_LAT = 111_111.0
"""Conversion factor: meters per degree of latitude (constant everywhere)."""

# Longitude varies by latitude, but we use this as approximation at equator:
METERS_PER_DEGREE_LON_AT_EQUATOR = 111_111.0
"""Conversion factor: meters per degree of longitude at equator."""

# ============================================================================
# Retry Settings
# ============================================================================
MAX_ARM_RETRIES = 3
"""Maximum attempts to arm drone."""

MAX_TAKEOFF_RETRIES = 3
"""Maximum attempts to issue takeoff command."""

ARM_RETRY_BACKOFF_FACTOR = 2.0
"""Exponential backoff multiplier for retries (wait 1s, 2s, 4s, ...)."""

# ============================================================================
# Validation Settings
# ============================================================================
MAX_NONE_FRACTION_ALLOWED = 0.1
"""Fail output validation if > 10% of samples are None."""

MIN_CSV_ROWS_REQUIRED = 10
"""Minimum rows required in output CSV to be considered valid."""

# ============================================================================
# Logging/Debugging
# ============================================================================
LOG_LEVEL = "INFO"
"""Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL."""

LOG_FILE_DIR = "logs"
"""Directory to store structured logs (optional; set to None to skip file logging)."""
