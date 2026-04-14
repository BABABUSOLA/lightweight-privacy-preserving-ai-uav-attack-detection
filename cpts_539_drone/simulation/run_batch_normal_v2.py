from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Union

PYTHON = "python3"  # or "python" depending on your VM

# ---------------------------------------------------------------------------
# Scenario configs
# Each scenario maps to a dict of overrides.  Anything not listed falls back
# to the defaults defined in DEFAULTS below.
# ---------------------------------------------------------------------------
DEFAULTS = {
    "duration": 90.0,
    "rate_hz": 50.0,
    "runs": 5,
    "do_takeoff": False,
}

SCENARIOS = {
    # --- core manoeuvres ---
    "hover":          {"duration": 60.0},
    "square":         {"duration": 120.0},
    "altitude_step":  {"duration": 60.0},

    # --- trajectory / tracking ---
    "circle":         {"duration": 120.0},
    "figure_eight":   {"duration": 120.0},
    "waypoint":       {"duration": 120.0},

    # --- heading / rotation ---
    "yaw_spin":       {"duration": 60.0},

    # --- environmental robustness ---
    "wind_gust":      {"duration": 90.0},

    # --- safety ---
    "failsafe":       {"duration": 45.0, "runs": 3, "do_takeoff": False},
    "landing":        {"duration": 45.0},
}

# ---------------------------------------------------------------------------
# Global takeoff override.
# When True, every scenario runs with --do-takeoff UNLESS the scenario
# explicitly sets "do_takeoff": False in its own config above.
#
# Flip to True ONLY when:
#   1. Sim environment loads cleanly (vehicle spawns, sensors initialise)
#   2. A dry hover run (do_takeoff=False) logs without errors
#   3. Flight controller preflight passes (EKF converged, no sensor faults)
#   4. Arming + takeoff logic verified in a single manual test run
# ---------------------------------------------------------------------------
DO_TAKEOFF_GLOBAL = False


def get(scenario_name: str, key: str) -> Union[float, int, bool]:
    """Return a scenario-specific value or fall back to the default."""
    return SCENARIOS[scenario_name].get(key, DEFAULTS[key])


def should_takeoff(scenario: str) -> bool:
    """Decide whether a scenario should arm and take off."""
    # Per-scenario setting wins; otherwise fall back to the global flag.
    if "do_takeoff" in SCENARIOS[scenario]:
        return SCENARIOS[scenario]["do_takeoff"]
    return DO_TAKEOFF_GLOBAL


def main() -> None:
    base_cmd = [PYTHON, "simulation/fly_and_log.py"]
    failed_runs: list[str] = []

    for scenario in SCENARIOS:
        runs = int(get(scenario, "runs"))
        duration = float(get(scenario, "duration"))
        rate = float(get(scenario, "rate_hz"))
        takeoff = should_takeoff(scenario)

        for run_idx in range(1, runs + 1):
            run_id = f"{run_idx:02d}"
            tag = f"{scenario} run {run_id}"
            cmd = base_cmd + [
                "--scenario", scenario,
                "--run-id",   run_id,
                "--duration", str(duration),
                "--rate",     str(rate),
            ]

            if takeoff:
                cmd.append("--do-takeoff")

            print(f"\n{'='*60}")
            print(f"  Scenario : {scenario}")
            print(f"  Run      : {run_id}/{runs:02d}")
            print(f"  Duration : {duration}s  |  Rate: {rate} Hz")
            print(f"  Takeoff  : {'YES' if takeoff else 'NO (dry run)'}")
            print(f"  Command  : {' '.join(cmd)}")
            print(f"{'='*60}")

            try:
                result = subprocess.run(cmd)

                if result.returncode != 0:
                    print(f"[WARN] {tag} exited with code {result.returncode}")
                    failed_runs.append(f"{tag} (exit {result.returncode})")

            except FileNotFoundError:
                print(f"[ERROR] Could not find '{cmd[0]}' or "
                      f"'{cmd[1]}' — check your paths")
                failed_runs.append(f"{tag} (file not found)")

            except Exception as exc:
                print(f"[ERROR] {tag} unexpected error: {exc}")
                failed_runs.append(f"{tag} ({type(exc).__name__})")

    # ---- summary ----
    total = sum(int(get(s, "runs")) for s in SCENARIOS)

    print(f"\n{'='*60}")
    print(f"  All done — {total} total runs across {len(SCENARIOS)} scenarios")

    if failed_runs:
        print(f"  {len(failed_runs)} run(s) failed:")
        for tag in failed_runs:
            print(f"    • {tag}")
    else:
        print("  All runs completed successfully.")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()