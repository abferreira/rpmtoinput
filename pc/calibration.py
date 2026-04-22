from __future__ import annotations
import statistics
import sys
import time
from pathlib import Path

import serial


STOPPED_THRESHOLD = 3.0  # RPM below this counts as stopped


class CalibrationError(Exception):
    pass


def _open_serial(port: str, baud: int, num_magnets: int) -> serial.Serial:
    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2.0)  # wait for Arduino DTR reset
        ser.reset_input_buffer()
        ser.write(f"MAGNETS:{num_magnets}\n".encode())
        return ser
    except serial.SerialException as e:
        raise CalibrationError(f"Cannot open {port}: {e}") from e


def _read_rpm_for(ser: serial.Serial, duration_s: float) -> list[float]:
    samples: list[float] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        line = ser.readline().decode("ascii", errors="ignore").strip()
        if line.startswith("RPM:"):
            try:
                samples.append(float(line[4:]))
            except ValueError:
                pass
    return samples


def _measure_friction(ser: serial.Serial, timeout: float = 60.0) -> float:
    # Check if RPM is already at zero
    warmup = _read_rpm_for(ser, 1.0)
    if warmup and max(warmup) < STOPPED_THRESHOLD:
        print("RPM is already at zero — recording 0.0s friction time.")
        return 0.0

    t_start = time.monotonic()
    deadline = t_start + timeout
    while time.monotonic() < deadline:
        line = ser.readline().decode("ascii", errors="ignore").strip()
        if line.startswith("RPM:"):
            try:
                rpm = float(line[4:])
                elapsed = time.monotonic() - t_start
                print(f"\r  RPM: {rpm:6.1f}  ({elapsed:.1f}s)", end="", flush=True)
                if rpm < STOPPED_THRESHOLD:
                    print()
                    return elapsed
            except ValueError:
                pass

    print(f"\nWarning: RPM did not reach zero within {timeout:.0f}s. Recording {timeout:.0f}s.")
    return timeout


def run_calibration(port: str, baud: int, num_magnets: int = 1) -> dict:
    print(f"\n=== Calibration (magnets: {num_magnets}) ===")
    input("Phase 1: Pedal at a comfortable pace for 30 seconds.\nPress Enter when ready...")
    ser = _open_serial(port, baud, num_magnets)
    try:
        print("Recording baseline... (30 seconds)")
        samples = _read_rpm_for(ser, 30.0)
        baseline_rpm = statistics.mean(samples) if samples else 0.0
        print(f"  Baseline RPM: {baseline_rpm:.1f}")

        input("\nPhase 2: Pedal at maximum effort for 10 seconds.\nPress Enter when ready...")
        print("Recording max effort... (10 seconds)")
        samples = _read_rpm_for(ser, 10.0)
        max_rpm = max(samples) if samples else baseline_rpm
        print(f"  Peak RPM: {max_rpm:.1f}")

        input("\nPhase 3: Stabilize at ~100 RPM, then release the pedals when prompted.\n"
              "Press Enter when you are ready to release...")
        print("Measuring coast-down time... (release pedals now)")
        friction_time = _measure_friction(ser, timeout=60.0)
        print(f"  Coast-down time: {friction_time:.2f}s")

    finally:
        ser.close()

    return {
        "baseline_rpm": round(baseline_rpm, 2),
        "max_rpm": round(max_rpm, 2),
        "friction_time": round(friction_time, 2),
    }


def maybe_run_calibration_for_new_profile(
    profile: dict,
    manager,  # ProfileManager
    port: str,
    baud: int,
    num_magnets: int = 1,
) -> None:
    answer = input("\nRun calibration for this profile? (y/n): ").strip().lower()
    if answer != "y":
        print("Skipping calibration. Using default max_rpm from config.")
        return

    try:
        result = run_calibration(port, baud, num_magnets)
    except CalibrationError as e:
        print(f"Serial unavailable: {e}\nSkipping calibration.")
        return
    except KeyboardInterrupt:
        print("\nCalibration cancelled.")
        return

    manager.update_calibration(profile["name"], result)
    profile["calibration"] = result
    print("\nCalibration saved.")


if __name__ == "__main__":
    # Subprocess entry point called by the GUI's Re-Calibrate button.
    # Args: <port> <baud> <profile_name> <profiles_path> <num_magnets>
    if len(sys.argv) < 5:
        print("Usage: calibration.py <port> <baud> <profile_name> <profiles_path> [num_magnets]")
        sys.exit(1)

    port_arg = sys.argv[1]
    baud_arg = int(sys.argv[2])
    profile_name = sys.argv[3]
    profiles_path = sys.argv[4]
    magnets_arg = int(sys.argv[5]) if len(sys.argv) > 5 else 1

    sys.path.insert(0, str(Path(__file__).parent))
    from profile_manager import ProfileManager  # noqa: E402

    mgr = ProfileManager(profiles_path)
    mgr.load()

    profile = mgr.get_profile(profile_name)
    if profile is None:
        print(f"Profile '{profile_name}' not found.")
        sys.exit(1)

    print(f"\nRe-calibrating profile: {profile_name}")
    try:
        result = run_calibration(port_arg, baud_arg, magnets_arg)
    except CalibrationError as e:
        print(f"Serial error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCalibration cancelled.")
        sys.exit(1)

    mgr.update_calibration(profile_name, result)
    print(f"\nProfile '{profile_name}' updated. You can close this window.")
