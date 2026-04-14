import itertools
import subprocess
from pathlib import Path

PYTHON = "python3"  # or "python" depending on your VM


def main():
    # NOTE:
    # - Hover-style "normal" logging: fly_and_log.py
    # - Moving "normal" logging: mission_square_fly_and_log.py (mission waypoints)
    hover_logger = [PYTHON, "simulation/fly_and_log.py"]
    square_logger = [PYTHON, "simulation/mission_square_fly_and_log.py"]

    # Define runs
    # Keep scenario names explicit so downstream pipelines can separate dynamics.
    scenarios = [
        ("normal_hover", hover_logger),
        ("normal_square", square_logger),
    ]
    runs_per_scenario = 3  # total CSVs per scenario
    duration_s = 60.0
    rate_hz = 10.0

    # Takeoff policy:
    # - "never": never try to arm/takeoff (most reliable for logging)
    # - "auto": run a quick preflight check; enable takeoff only if it passes
    # - "always": always try takeoff (will fail noisily if preflight not clean)
    takeoff_policy = "auto"

    # Square mission settings (only used by mission_square_fly_and_log.py)
    mission_size_m = 25.0
    mission_alt_m = 10.0
    cruise_speed_mps = 4.0

    do_takeoff = False
    if takeoff_policy == "always":
        do_takeoff = True
    elif takeoff_policy == "auto":
        print("\n=== Preflight check (auto) ===")
        check_cmd = [
            PYTHON,
            "simulation/check_preflight.py",
            "--timeout-s",
            "60",
            "--arm-probe",
        ]
        print("Command:", " ".join(check_cmd))
        res = subprocess.run(check_cmd)
        do_takeoff = res.returncode == 0
        print(f"Preflight status: {'CLEAN' if do_takeoff else 'NOT CLEAN'} (rc={res.returncode})")

    for (scenario, base_cmd), run_idx in itertools.product(scenarios, range(1, runs_per_scenario + 1)):
        run_id = f"{run_idx:02d}"
        cmd = base_cmd + [
            "--scenario", scenario,
            "--run-id", run_id,
            "--duration", str(duration_s),
            "--rate", str(rate_hz),
        ]

        # Add mission parameters only for the square mission logger.
        if "mission_square_fly_and_log.py" in base_cmd[-1]:
            cmd += [
                "--mission-size-m", str(mission_size_m),
                "--mission-alt-m", str(mission_alt_m),
                "--cruise-speed-mps", str(cruise_speed_mps),
            ]

        if do_takeoff:
            cmd.append("--do-takeoff")

        print(f"\n=== Running {scenario} run {run_id} ===")
        print("Command:", " ".join(cmd))
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"[WARN] Run {scenario} {run_id} exited with code {result.returncode}")


if __name__ == "__main__":
    # Ensure working dir is project root when calling this script
    main()