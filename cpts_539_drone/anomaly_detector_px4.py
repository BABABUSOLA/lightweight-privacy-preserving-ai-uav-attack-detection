"""
Real-time Anomaly Detection for PX4 SITL
=========================================
Connects to a PX4 SITL drone, collects telemetry, runs the trained
GRU autoencoder, and raises alerts when anomalies are detected.

Prerequisites:
    pip install mavsdk pymavlink joblib torch numpy

Usage:
    1. Start PX4 SITL:    make px4_sitl gazebo
    2. Run this script:    python anomaly_detector_px4.py
"""

import asyncio
import collections
import logging
import time
import numpy as np
import torch
import joblib
import torch.nn as nn
from datetime import datetime

from mavsdk import System

# ─────────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────────
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ─────────────────────────────────────────────────
# 1. GRU Model Definition (must match training)
# ─────────────────────────────────────────────────
class GRUAutoencoder(nn.Module):
    def __init__(self, n_features=10, hidden_size=32, latent_size=16, num_layers=1):
        super().__init__()
        self.encoder_gru = nn.GRU(
            input_size=n_features,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
        )
        self.encoder_fc = nn.Linear(hidden_size, latent_size)
        self.decoder_fc = nn.Linear(latent_size, hidden_size)
        self.decoder_gru = nn.GRU(
            input_size=hidden_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
        )
        self.output_layer = nn.Linear(hidden_size, n_features)

    def forward(self, x):
        batch_size, seq_len, _ = x.size()
        _, h_n = self.encoder_gru(x)
        h_last = h_n[-1]
        z = self.encoder_fc(h_last)
        dec_in = self.decoder_fc(z).unsqueeze(1).repeat(1, seq_len, 1)
        dec_out, _ = self.decoder_gru(dec_in)
        recon = self.output_layer(dec_out)
        return recon


# ─────────────────────────────────────────────────
# 2. Configuration
# ─────────────────────────────────────────────────
MODEL_PATH = "models/gru_autoencoder.pth"
SCALER_PATH = "models/scaler.pkl"
THRESHOLD = 0.9468           # your GRU 95th percentile threshold
SEQ_LEN = 50                 # must match training
SITL_ADDRESS = "udp://:14540"  # default PX4 SITL address
ALERT_COOLDOWN = 5.0         # seconds between repeated alerts
TIMEOUT_CONNECTION_S = 20.0
TIMEOUT_HEALTH_S = 30.0


# ─────────────────────────────────────────────────
# 3. Load model and scaler
# ─────────────────────────────────────────────────
def load_model():
    model = GRUAutoencoder(n_features=10, hidden_size=32, latent_size=16)
    model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    model.eval()
    logger.info(f"[INIT] Model loaded from {MODEL_PATH}")
    return model


def load_scaler():
    scaler = joblib.load(SCALER_PATH)
    logger.info(f"[INIT] Scaler loaded from {SCALER_PATH}")
    return scaler


# ─────────────────────────────────────────────────
# 4. Anomaly detection logic
# ─────────────────────────────────────────────────
def check_anomaly(buffer, model, scaler, threshold):
    """
    Takes the last SEQ_LEN readings, scales them,
    runs inference, returns (is_anomaly, mse_error).
    """
    if len(buffer) < SEQ_LEN:
        return False, 0.0

    raw = np.array(list(buffer)[-SEQ_LEN:])       # (50, 10)
    scaled = scaler.transform(raw)                  # same normalization as training
    tensor = torch.from_numpy(scaled).float().unsqueeze(0)  # (1, 50, 10)

    with torch.no_grad():
        recon = model(tensor)
        mse = ((recon - tensor) ** 2).mean().item()

    return mse > threshold, mse


# ─────────────────────────────────────────────────
# 5. Alert handler
# ─────────────────────────────────────────────────
class AlertManager:
    def __init__(self, cooldown=5.0, log_file="anomaly_log.csv"):
        self.cooldown = cooldown
        self.last_alert_time = 0
        self.total_alerts = 0
        self.total_checks = 0
        self.log_file = log_file

        # Create log file with header
        with open(self.log_file, "w", encoding="utf-8", newline="") as f:
            f.write("timestamp,mse_error,threshold,is_anomaly,lat,lon,alt\n")

    def handle(self, is_anomaly, mse, lat, lon, alt):
        self.total_checks += 1
        now = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # Log every check
        with open(self.log_file, "a", encoding="utf-8", newline="") as f:
            f.write(f"{timestamp},{mse:.6f},{THRESHOLD:.6f},"
                    f"{int(is_anomaly)},{lat:.6f},{lon:.6f},{alt:.3f}\n")

        if is_anomaly:
            self.total_alerts += 1

            if now - self.last_alert_time > self.cooldown:
                self.last_alert_time = now
                print(f"\n{'!'*60}")
                print(f"  ANOMALY DETECTED at {timestamp}")
                print(f"  MSE: {mse:.4f}  (threshold: {THRESHOLD:.4f})")
                print(f"  Location: lat={lat:.6f}, lon={lon:.6f}, alt={alt:.2f}m")
                print(f"  Total alerts: {self.total_alerts}/{self.total_checks}")
                print(f"{'!'*60}\n")
                return True  # alert sent
            else:
                print(f"  [ANOMALY] MSE={mse:.4f} (suppressed — cooldown)")
        else:
            # Print status every 50 checks
            if self.total_checks % 50 == 0:
                print(f"  [OK] check #{self.total_checks} | MSE={mse:.4f} | "
                      f"alerts so far: {self.total_alerts}")

        return False


# ─────────────────────────────────────────────────
# 6. MAVSDK alert sender (sends to QGroundControl)
# ─────────────────────────────────────────────────
async def send_mavlink_alert(drone, mse):
    """
    Sends a STATUSTEXT message that appears in QGroundControl.
    Optionally trigger RTL (return to launch) for critical alerts.
    """
    # This will show as a warning in QGroundControl
    # MAVSDK doesn't have direct statustext, so we use action
    print(f"  >> MAVLink alert would be sent: MSE={mse:.4f}")

    # Uncomment below to trigger automatic RETURN TO LAUNCH:
    # print("  >> TRIGGERING RETURN TO LAUNCH")
    # await drone.action.return_to_launch()

    # Uncomment below to trigger automatic LAND:
    # print("  >> TRIGGERING LAND")
    # await drone.action.land()


# ─────────────────────────────────────────────────
# 7. Telemetry collection tasks
# ─────────────────────────────────────────────────

# Shared state for latest telemetry values
latest = {
    "lat": 0.0, "lon": 0.0, "rel_alt": 0.0,
    "accel_x": 0.0, "accel_y": 0.0, "accel_z": 0.0,
    "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0,
    "battery_pct": 0.0,
    "ready": False,
}


async def wait_for_connection(drone: System, timeout_s: float = TIMEOUT_CONNECTION_S) -> None:
    """Wait for MAVSDK connection with timeout."""
    start = time.time()
    while True:
        async for state in drone.core.connection_state():
            if state.is_connected:
                logger.info("[CONN] Drone connected!")
                return

        if time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out waiting for connection after {timeout_s}s")
        await asyncio.sleep(0.2)


async def wait_for_health(drone: System, timeout_s: float = TIMEOUT_HEALTH_S) -> None:
    """Wait for global/home position readiness with timeout."""
    start = time.time()
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            logger.info("[CONN] GPS fix acquired!")
            return
        if time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out waiting for health after {timeout_s}s")
        await asyncio.sleep(0.5)


def _get_imu_axes(imu):
    """
    Return accel/gyro axes from whichever MAVSDK IMU layout is available.
    Prefers FRD fields used in the rest of this project.
    """
    if hasattr(imu, "acceleration_frd") and hasattr(imu, "angular_velocity_frd"):
        accel = imu.acceleration_frd
        gyro = imu.angular_velocity_frd
        return (
            accel.forward_m_s2,
            accel.right_m_s2,
            accel.down_m_s2,
            gyro.forward_rad_s,
            gyro.right_rad_s,
            gyro.down_rad_s,
        )

    # Fallback for older/alternate message layouts.
    if hasattr(imu, "acceleration_fwd") and hasattr(imu, "angular_velocity_body"):
        accel_x = imu.acceleration_fwd
        accel_y = getattr(imu, "acceleration_right", 0.0)
        accel_z = getattr(imu, "acceleration_down", 0.0)
        body = imu.angular_velocity_body
        return (
            accel_x,
            accel_y,
            accel_z,
            getattr(body, "roll_rad_s", 0.0),
            getattr(body, "pitch_rad_s", 0.0),
            getattr(body, "yaw_rad_s", 0.0),
        )

    raise AttributeError("Unsupported IMU schema received from MAVSDK telemetry")


async def collect_position(drone):
    async for pos in drone.telemetry.position():
        latest["lat"] = pos.latitude_deg
        latest["lon"] = pos.longitude_deg
        latest["rel_alt"] = pos.relative_altitude_m


async def collect_imu(drone):
    async for imu in drone.telemetry.imu():
        try:
            (
                latest["accel_x"],
                latest["accel_y"],
                latest["accel_z"],
                latest["gyro_x"],
                latest["gyro_y"],
                latest["gyro_z"],
            ) = _get_imu_axes(imu)
        except AttributeError as e:
            logger.warning(f"[DETECT] IMU mapping warning: {e}")
            continue
        latest["ready"] = True  # IMU is the fastest stream


async def collect_battery(drone):
    async for bat in drone.telemetry.battery():
        latest["battery_pct"] = bat.remaining_percent


# ─────────────────────────────────────────────────
# 8. Main detection loop
# ─────────────────────────────────────────────────
async def detection_loop(drone, model, scaler, alert_mgr):
    """
    Runs at ~10 Hz: reads latest telemetry, buffers it,
    runs the model when buffer is full, handles alerts.
    """
    buffer = collections.deque(maxlen=SEQ_LEN)

    print("[DETECT] Waiting for telemetry...")
    while not latest["ready"]:
        await asyncio.sleep(0.1)
    print("[DETECT] Telemetry streaming. Starting detection.\n")

    while True:
        # Build a reading with the same 10 features as training
        reading = [
            latest["lat"],
            latest["lon"],
            latest["rel_alt"],
            latest["accel_x"],
            latest["accel_y"],
            latest["accel_z"],
            latest["gyro_x"],
            latest["gyro_y"],
            latest["gyro_z"],
            latest["battery_pct"],
        ]
        buffer.append(reading)

        # Run detection once buffer is full
        if len(buffer) == SEQ_LEN:
            is_anomaly, mse = check_anomaly(buffer, model, scaler, THRESHOLD)
            should_alert = alert_mgr.handle(
                is_anomaly, mse,
                latest["lat"], latest["lon"], latest["rel_alt"]
            )
            if should_alert:
                await send_mavlink_alert(drone, mse)

        # ~10 Hz sampling rate (adjust to match your training data rate)
        await asyncio.sleep(0.1)


# ─────────────────────────────────────────────────
# 9. Main entry point
# ─────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  PX4 SITL Anomaly Detector")
    print("  GRU Autoencoder — Real-time Detection")
    print("=" * 60)

    # Load model and scaler
    model = load_model()
    scaler = load_scaler()
    alert_mgr = AlertManager(cooldown=ALERT_COOLDOWN)

    # Connect to PX4 SITL
    drone = System()
    logger.info(f"[CONN] Connecting to {SITL_ADDRESS}...")
    await drone.connect(system_address=SITL_ADDRESS)
    logger.info("[CONN] Waiting for drone to connect...")
    await wait_for_connection(drone)
    logger.info("[CONN] Waiting for GPS fix...")
    await wait_for_health(drone)
    print("")

    # Start all tasks concurrently
    print("[START] Launching telemetry collectors + detection loop\n")
    await asyncio.gather(
        collect_position(drone),
        collect_imu(drone),
        collect_battery(drone),
        detection_loop(drone, model, scaler, alert_mgr),
    )


if __name__ == "__main__":
    asyncio.run(main())