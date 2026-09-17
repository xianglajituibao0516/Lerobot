import argparse
import time

from joyconrobotics import JoyconRobotics
from joyconrobotics.device import get_L_id, get_R_id


def main():
    parser = argparse.ArgumentParser(description="Print Joy-Con control data.")
    parser.add_argument("--device", choices=("right", "left"), default="right")
    args = parser.parse_args()

    device_id = get_R_id() if args.device == "right" else get_L_id()
    if device_id[0] is None:
        parser.exit(1, f"No {args.device} Joy-Con found. Check its connection.\n")

    print("Place the controller flat on the desk for calibration.", flush=True)
    controller = JoyconRobotics(args.device)
    print("pose: [x, y, z, roll, pitch, yaw] (meters, radians)")
    print("Press Ctrl+C to stop. Data is displayed only, not saved.")
    try:
        while True:
            pose, gripper, button = controller.get_control()
            values = ", ".join(f"{value:.3f}" for value in pose)
            print(f"pose=[{values}] gripper={gripper} button={button}", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        controller.running = False
        controller.thread.join()
        controller.disconnnect()


if __name__ == "__main__":
    main()
