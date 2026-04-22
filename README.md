# Complete Ubuntu VM Setup for PX4 SITL + Gazebo + MAVSDK

**Project:** Lightweight Privacy-Preserving AI/ML for Securing Autonomous CPS (Drones)  
**Target:** `make px4_sitl gz_x500` (PX4 SITL with Gazebo Sim and x500 quadrotor)

---

## Prerequisites

| Item | Minimum | Recommended |
|------|---------|-------------|
| VirtualBox | 7.0+ | Latest |
| Ubuntu ISO | 22.04 LTS (Jammy) | 22.04.x LTS |
| VM RAM | 4 GB | 8 GB |
| VM CPUs | 2 | 4 |
| VM Disk | 50 GB | 80 GB |
| Video Memory | 64 MB | 128 MB |
| 3D Acceleration | Enabled | Enabled |

> **`gz_x500` uses Gazebo Sim (formerly Ignition Gazebo), NOT Gazebo Classic.**  
> PX4 v1.14+ defaults to Gazebo Harmonic/Garden for `gz_*` targets.

---

## PHASE 1: VirtualBox VM Configuration

### Step 1 — Create the VM (in VirtualBox on Windows)

1. Download **Ubuntu 22.04 LTS Desktop ISO** from https://releases.ubuntu.com/22.04/
2. Open VirtualBox → **New**
   - Name: `Ubuntu_CPS_Drone`
   - Type: Linux, Version: Ubuntu (64-bit)
   - RAM: **8192 MB** (minimum 4096)
   - Create a virtual hard disk: **VDI, Dynamically allocated, 80 GB**
3. Before starting, go to **Settings**:

**System → Processor:**
- CPUs: **4**
- Enable PAE/NX: checked

**Display → Screen:**
- Video Memory: **128 MB**
- Graphics Controller: **VMSVGA**
- Enable 3D Acceleration: **checked** ← critical for Gazebo GUI

**Network:**
- Adapter 1: NAT (default, fine for internet)

4. **Start** the VM and install Ubuntu normally (minimal install is fine).
5. After install, reboot, then install **VirtualBox Guest Additions**:

```bash
sudo apt update
sudo apt install -y build-essential dkms linux-headers-$(uname -r)
```

Then in VirtualBox menu: **Devices → Insert Guest Additions CD Image**, then:

```bash
ls /media/$USER
```
You should see :

```bash
VBox_GAs_7.2.6
```

Run the installer

```bash
sudo sh /media/$USER/VBox_GAs_*/VBoxLinuxAdditions.run
```
Reboot the VM
```bash
sudo reboot
```

<!-- ```bash
sudo mount /dev/cdrom /mnt
sudo /mnt/VBoxLinuxAdditions.run
sudo reboot
``` -->

After reboot, confirm 3D acceleration is working:

```bash
glxinfo | grep "direct rendering"
```

Expected output: `direct rendering: Yes`

If `glxinfo` is not found:

```bash
sudo apt install -y mesa-utils
glxinfo | grep "direct rendering"
```

---

## PHASE 2: System Dependencies

### Step 2 — Update system and install base packages

Open a terminal in Ubuntu and run everything below. Copy-paste each block.

```bash
sudo apt update && sudo apt upgrade -y
```

```bash
sudo apt install -y \
    git \
    wget \
    curl \
    python3 \
    python3-pip \
    python3-venv \
    cmake \
    build-essential \
    ninja-build \
    gcc-arm-none-eabi \
    genromfs \
    exiftool \
    astyle \
    libxml2-dev \
    libxml2-utils \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    xterm \
    openjdk-11-jdk \
    ant \
    protobuf-compiler \
    libeigen3-dev \
    libopencv-dev \
    unzip \
    gawk \
    dmidecode \
    mesa-utils
```

---

## PHASE 3: Install Gazebo Harmonic

### Step 3 — Install Gazebo Harmonic (required for `gz_x500`)

`gz_x500` uses **Gazebo Sim** (the new Gazebo, formerly Ignition). PX4 v1.14/v1.15 works
with Gazebo Harmonic on Ubuntu 22.04.

```bash
sudo apt install -y lsb-release gnupg
```

```bash
sudo curl https://packages.osrfoundation.org/gazebo.gpg \
    --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
| sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null
```

```bash
sudo apt update
```

```bash
sudo apt install -y gz-harmonic
```


This installs all Gazebo Harmonic libraries, `gz sim`, rendering engine, physics, etc.

**Verify installation:**

```bash
gz sim --version
```

You should see something like `Gazebo Sim, version 8.x.x` (Harmonic series).

Quick smoke test (optional — will open a Gazebo window):

```bash
gz sim -r shapes.sdf
LIBGL_ALWAYS_SOFTWARE=1 gz sim -r shapes.sdf
```

If you see a Gazebo window with shapes, your 3D acceleration and Gazebo are working.
Close it with Ctrl+C.

---

## PHASE 4: Clone and Build PX4

### Step 4 — Clone PX4-Autopilot

```bash
cd ~
git clone https://github.com/PX4/PX4-Autopilot.git --recursive
```

This takes a while (several GB). If it fails partway, retry:

```bash
cd ~/PX4-Autopilot
git submodule update --init --recursive
```

### Step 5 — Run PX4's dependency installer

PX4 ships its own setup script that installs remaining dependencies:

```bash
cd ~/PX4-Autopilot
bash ./Tools/setup/ubuntu.sh
```

**Reboot after this script finishes:**

```bash
sudo reboot
```

### Step 6 — First build (this compiles PX4 firmware + SITL)

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500
```
if you move the folder to another run
```bash
make distclean
```

**First build takes 10–30 minutes** depending on VM specs. Subsequent builds are fast.

When successful, you will see:
1. Terminal output showing PX4 booting (with `pxh>` prompt)
2. **A Gazebo window** opening with the **x500 quadrotor** sitting on the ground

If the Gazebo window appears with the drone visible, congratulations — SITL is working.

**Leave this terminal running.** The drone is now simulated and listening on `udp://:14540`.

Press `Ctrl+C` to stop it when done testing.

### Troubleshooting: Gazebo window doesn't appear

If the PX4 shell starts but no Gazebo window:

1. **Check 3D acceleration:**
```bash
glxinfo | grep "direct rendering"
```
Must say `Yes`. If not, check VirtualBox Display settings.

2. **Check DISPLAY variable:**
```bash
echo $DISPLAY
```
Should output `:0` or `:1`. If empty:
```bash
export DISPLAY=:0
```

3. **Headless fallback** (if 3D never works in your VM — runs SITL without the GUI):
```bash
HEADLESS=1 make px4_sitl gz_x500
```
The simulation still runs; you just won't see the 3D view. Your MAVSDK scripts
will still work fine.

4. **Software rendering fallback** (slower but works without GPU):
```bash
export LIBGL_ALWAYS_SOFTWARE=1
make px4_sitl gz_x500
```
5. **Install QGroundControl from github**
```bash
wget https://github.com/mavlink/qgroundcontrol/releases/latest/download/QGroundControl-x86_64.AppImage

chmod +x QGroundControl-x86_64.AppImage

./QGroundControl-x86_64.AppImage
 or

LIBGL_ALWAYS_SOFTWARE=1 ./QGroundControl-x86_64.AppImage

THEN

cd ~/PX4-Autopilot
make px4_sitl gz_x500
```

---

## PHASE 5: Python Environment and MAVSDK

### Step 7 — Set up your project on the VM

Create the project folder (mirrors your Windows workspace):

```bash
mkdir -p ~/cpts_539_project/simulation
mkdir -p ~/cpts_539_project/data
mkdir -p ~/cpts_539_project/models
mkdir -p ~/cpts_539_project/docs
```

### Step 8 — Create Python virtual environment

```bash
cd ~/cpts_539_project
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

### Step 9 — Install Python packages

```bash
pip install mavsdk aioconsole numpy pandas matplotlib scikit-learn
```

For later phases (LSTM model, optimization):

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install onnx onnxruntime
```

> We use CPU-only PyTorch since this is a VM. This is fine for lightweight LSTM training.

### Step 10 — Create the test scripts

**File: `~/cpts_539_project/simulation/mavsdk_test.py`**

```bash
cat << 'PYEOF' > ~/cpts_539_project/simulation/mavsdk_test.py
import asyncio
from mavsdk import System


async def run() -> None:
    drone = System()
    await drone.connect(system_address="udp://:14540")

    print("Waiting for connection...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print("Connected to drone")
            break

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
PYEOF
```

**File: `~/cpts_539_project/simulation/mavsdk_takeoff_test.py`**

```bash
cat << 'PYEOF' > ~/cpts_539_project/simulation/mavsdk_takeoff_test.py
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
    await drone.connect(system_address="udp://:14540")

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
PYEOF
```

---

## PHASE 6: Run the Full Stack

### Step 11 — Terminal A: Start PX4 SITL with Gazebo

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500
```

Wait until you see the `pxh>` prompt AND the Gazebo window with the x500 drone.

### Step 12 — Terminal B: Run MAVSDK connectivity test

Open a **second terminal** (Ctrl+Alt+T), then:

```bash
cd ~/cpts_539_project
source .venv/bin/activate
python3 simulation/mavsdk_test.py
```

**Expected output:**

```
Waiting for connection...
Connected to drone
Position -> lat=47.397742, lon=8.545594, rel_alt=0.01m
Health -> global_pos_ok=True, home_pos_ok=True
```

### Step 13 — Terminal B: Run takeoff test

```bash
python3 simulation/mavsdk_takeoff_test.py
```

**Expected output:**

```
Waiting for connection...
Connected to drone
Waiting for global and home position estimates...
Position estimate is good
Arming...
Taking off...
Hovering for 5 seconds...
Landing...
Done
```

In the Gazebo window, you should SEE the drone take off, hover, and land.

---

## PHASE 6B: System-level GPS spoofing (EKF belief shift) via MAVLink injection

This section verifies that **PX4's EKF2 belief actually shifts** under injected GNSS data (not just that messages are received).

### Terminal A: Start PX4 SITL (headless recommended in VM)

```bash
cd ~/PX4-Autopilot
HEADLESS=1 make px4_sitl gz_x500
```

Wait for `pxh>` prompt.

### Terminal B: Start the GPS spoofer (send-only)

From your project folder:

```bash
cd ~/cpts_539_project
source .venv/bin/activate
python3 simulation/gps_input_spoofer.py \
  --master udpout:127.0.0.1:14540 \
  --rate-hz 10 \
  --attack-start 10 \
  --drift-rate-m-per-s 1.5 \
  --drift-direction-deg 45 \
  --gps-id 1 \
  --sats 12 --hdop 0.8 --vdop 1.2 --hacc-m 0.7 --vacc-m 1.5 --sacc-mps 0.2
```

Optional: if your PX4 build appears to ignore `GPS_INPUT`, try also sending a raw GNSS message:

```bash
python3 simulation/gps_input_spoofer.py \
  --master udpout:127.0.0.1:14540 \
  --rate-hz 10 \
  --attack-start 10 \
  --drift-rate-m-per-s 1.5 \
  --drift-direction-deg 45 \
  --use-gps2-raw
```

### PX4 shell (Terminal A): confirm EKF is shifting

Run these commands while the spoofer is running:

```bash
listener sensor_gps
listener vehicle_gps_position
listener vehicle_global_position
ekf2 status
```

**What you want to see:**
- `sensor_gps`: a GNSS instance updating at ~10 Hz with the spoofed lat/lon drift.
- `vehicle_gps_position`: selected GPS data reflects the spoofed drift.
- `vehicle_global_position`: EKF output position drifts consistent with the spoof.

If `sensor_gps` shows updates but `vehicle_global_position` does not move, EKF2 may be fusing a different GNSS source (e.g., the simulator GPS). In that case:
- Confirm GNSS fusion is enabled (`EKF2_GPS_CTRL` in PX4 parameters).
- Try disabling the competing GPS source (simulation-provided) or adjust estimator sensor selection in your PX4 version, then reboot SITL and re-test.

---

## PHASE 7: Sensor Data Logging Script (for your LSTM training data)

### Step 14 — Create the flight + data logging script

This script flies a mission while logging GPS, IMU, and battery data to CSV — exactly what
you need for training your LSTM autoencoder.

```bash
cat << 'PYEOF' > ~/cpts_539_project/simulation/fly_and_log.py
import asyncio
import csv
import time
from mavsdk import System
from mavsdk.offboard import OffboardError, VelocityNedYaw


async def run() -> None:
    drone = System()
    await drone.connect(system_address="udp://:14540")

    print("Waiting for connection...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print("Connected to drone")
            break

    print("Waiting for position estimate...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("Position OK")
            break

    log_file = f"data/flight_log_{int(time.time())}.csv"
    rows = []

    async def log_telemetry(duration_s: float) -> None:
        start = time.time()
        while time.time() - start < duration_s:
            pos = await anext(drone.telemetry.position().__aiter__())
            imu = await anext(drone.telemetry.imu().__aiter__())
            bat = await anext(drone.telemetry.battery().__aiter__())

            row = {
                "timestamp": time.time(),
                "lat": pos.latitude_deg,
                "lon": pos.longitude_deg,
                "abs_alt": pos.absolute_altitude_m,
                "rel_alt": pos.relative_altitude_m,
                "accel_x": imu.acceleration_frd.forward_m_s2,
                "accel_y": imu.acceleration_frd.right_m_s2,
                "accel_z": imu.acceleration_frd.down_m_s2,
                "gyro_x": imu.angular_velocity_frd.forward_rad_s,
                "gyro_y": imu.angular_velocity_frd.right_rad_s,
                "gyro_z": imu.angular_velocity_frd.down_rad_s,
                "battery_v": bat.voltage_v,
                "battery_pct": bat.remaining_percent,
            }
            rows.append(row)
            await asyncio.sleep(0.1)

    print("Arming...")
    await drone.action.arm()

    print("Taking off...")
    await drone.action.takeoff()
    await asyncio.sleep(3)

    print("Logging normal flight data...")
    await log_telemetry(30.0)

    print("Landing...")
    await drone.action.land()
    await asyncio.sleep(5)

    if rows:
        with open(log_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"Saved {len(rows)} rows to {log_file}")
    else:
        print("No data collected")


if __name__ == "__main__":
    asyncio.run(run())
PYEOF
```

Run it (with SITL already running in Terminal A):

```bash
cd ~/cpts_539_project
source .venv/bin/activate
python3 simulation/fly_and_log.py
```

This creates a CSV file in `~/cpts_539_project/data/` with timestamped sensor readings.

---

## Quick Reference: Commands You'll Use Daily

| Action | Command |
|--------|---------|
| Start SITL | `cd ~/PX4-Autopilot && make px4_sitl gz_x500` |
| Start SITL headless | `cd ~/PX4-Autopilot && HEADLESS=1 make px4_sitl gz_x500` |
| Activate Python env | `cd ~/cpts_539_project && source .venv/bin/activate` |
| Test connection | `python3 simulation/mavsdk_test.py` |
| Test takeoff | `python3 simulation/mavsdk_takeoff_test.py` |
| Log flight data | `python3 simulation/fly_and_log.py` |
| Stop SITL | `Ctrl+C` in Terminal A |
| Check Gazebo | `gz sim --version` |

---

## Summary: Complete Command Sequence (copy-paste order)

For a brand new Ubuntu 22.04 VM, run these in order:

```bash
# 1. System update
sudo apt update && sudo apt upgrade -y

# 2. Base packages
sudo apt install -y git wget curl python3 python3-pip python3-venv cmake \
    build-essential ninja-build gcc-arm-none-eabi genromfs exiftool astyle \
    libxml2-dev libxml2-utils libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly xterm openjdk-11-jdk ant protobuf-compiler \
    libeigen3-dev libopencv-dev unzip gawk dmidecode mesa-utils \
    lsb-release gnupg

# 3. Install Gazebo Harmonic
sudo curl https://packages.osrfoundation.org/gazebo.gpg \
    --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
| sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null

sudo apt update
sudo apt install -y gz-harmonic

# 4. Clone PX4
cd ~
git clone https://github.com/PX4/PX4-Autopilot.git --recursive

# 5. Run PX4 dependency installer
cd ~/PX4-Autopilot
bash ./Tools/setup/ubuntu.sh
sudo reboot
```

After reboot:

```bash
# 6. First build + test (takes 10-30 min)
cd ~/PX4-Autopilot
make px4_sitl gz_x500

# (you should see Gazebo window + pxh> prompt)
# Press Ctrl+C to stop after confirming it works

# 7. Set up Python project
mkdir -p ~/cpts_539_project/{simulation,data,models,docs}
cd ~/cpts_539_project
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install mavsdk aioconsole numpy pandas matplotlib scikit-learn
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# 8. Create scripts (copy the mavsdk_test.py, mavsdk_takeoff_test.py, fly_and_log.py
#    from the sections above)

# 9. Start SITL in one terminal, run scripts in another
```

---

## What Each Tool Does

| Tool | Purpose in Your Project |
|------|------------------------|
| **VirtualBox** | Runs Ubuntu VM on your Windows machine |
| **Ubuntu 22.04** | OS where everything runs |
| **PX4-Autopilot** | Flight controller firmware (SITL mode = simulated) |
| **Gazebo Harmonic** | 3D physics simulator; renders the drone + world |
| **gz_x500** | PX4 build target: SITL + Gazebo + x500 quadrotor model |
| **MAVSDK (Python)** | MAVLink SDK to command the drone + read telemetry |
| **PyTorch** | Train LSTM autoencoder for anomaly detection |
| **NumPy/Pandas** | Process CSV sensor logs |
| **Matplotlib** | Visualize flight data and anomaly scores |

```bash
pip3 install notebook

cd Documents
jupyter notebook

<!if commmand not found , refresh -->
python3 -m notebook


```
