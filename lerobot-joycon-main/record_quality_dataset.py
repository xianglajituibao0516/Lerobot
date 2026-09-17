#!/usr/bin/env python3
"""Record explicitly accepted SO-100 demonstrations with timing/provenance audits."""

import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid

import cv2
from omegaconf import OmegaConf
from PIL import Image

from lerobot.common.datasets.lerobot_dataset import LeRobotDataset
from lerobot.common.robot_devices.recording_quality import audited_step, timing_quality
from lerobot.common.robot_devices.robots.factory import make_robot
from lerobot.common.utils.utils import init_hydra_config


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    temporary.replace(path)


def provenance(cfg):
    config = OmegaConf.to_container(cfg, resolve=True)
    calibration = {}
    for name in config["follower_arms"]:
        path = Path(config["calibration_dir"]) / f"{name}_follower.json"
        calibration[name] = json.loads(path.read_text())
    sources = {}
    for path in [Path(__file__), Path("lerobot/common/robot_devices/recording_quality.py"),
                 Path("lerobot/common/robot_devices/controllers/joycon_controller.py"),
                 Path("lerobot/common/robot_devices/cameras/opencv.py")]:
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"schema_version": 1, "config": config, "calibration": calibration,
            "source_sha256": sources, "action_semantics": "absolute calibrated bus target, observation before write",
            "units": "five joints in degrees then gripper in percent, arm order from config",
            "camera_time_semantics": "host receipt monotonic time, NOT sensor exposure time",
            "timestamp_semantics": "LeRobot uniform frame/fps; actual timing in quality/attempts"}


def check_images(dataset):
    dataset._wait_image_writer()
    for key in dataset.meta.camera_keys:
        for filename in dataset.episode_buffer[key]:
            with Image.open(filename) as image:
                if image.size != (640, 480) or image.mode != "RGB":
                    raise ValueError(f"Invalid saved image: {filename}")
                image.load()


def verify_videos(dataset, episode, count):
    for key in dataset.meta.video_keys:
        path = dataset.root / dataset.meta.get_video_file_path(episode, key)
        capture = cv2.VideoCapture(str(path))
        decoded = 0
        try:
            while True:
                ok, image = capture.read()
                if not ok:
                    break
                if image.shape != (480, 640, 3):
                    raise ValueError(f"Wrong video dimensions: {path}")
                decoded += 1
        finally:
            capture.release()
        if decoded != count:
            raise ValueError(f"Video {path}: {decoded} frames, expected {count}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--robot-path", default="lerobot/configs/robot/so100_joycon_double_pi05.yaml")
    parser.add_argument("--episodes", type=int, default=150, help="Total accepted episodes, including resumed episodes")
    parser.add_argument("--seconds", type=float, default=25)
    parser.add_argument("--min-seconds", type=float, default=2)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--open-percent", type=float, default=57.29577951308232)
    args = parser.parse_args()
    if args.episodes < 1 or args.seconds <= 0 or args.fps < 1:
        parser.error("episodes, seconds and fps must be positive")
    if not 0 < args.min_seconds <= args.seconds:
        parser.error("Expected 0 < min-seconds <= seconds")
    if not 0 < args.open_percent <= 100:
        parser.error("open-percent must be in (0,100]")
    cfg = init_hydra_config(args.robot_path, [])
    for controller in cfg.controllers.values():
        controller.gripper_open_percent = args.open_percent
        controller.gripper_closed_percent = 0.0
    manifest = provenance(cfg)
    manifest.update(fps=args.fps, task=args.task, repo_id=args.repo_id)
    quality = args.root / "quality"
    if args.root.exists():
        if not args.resume:
            parser.error("root already exists; use a new directory or --resume")
        if (quality / "pending.json").exists():
            parser.error("An interrupted save needs inspection: quality/pending.json")
        if json.loads((quality / "manifest.json").read_text()) != manifest:
            parser.error("Config, calibration, code, task or fps changed; create a new dataset")
    elif args.resume:
        parser.error("Cannot resume a nonexistent dataset")

    robot = make_robot(cfg)
    dataset = None
    try:
        if args.resume:
            dataset = LeRobotDataset(args.repo_id, root=args.root, local_files_only=True)
            dataset.start_image_writer(num_processes=0, num_threads=4)
        else:
            dataset = LeRobotDataset.create(args.repo_id, args.fps, root=args.root,
                                           robot=robot, use_videos=True, image_writer_threads=4)
            quality.mkdir(parents=True)
            write_json(quality / "manifest.json", manifest)
        (quality / "attempts").mkdir(exist_ok=True)
        robot.connect()
        for camera in robot.cameras.values():
            camera.async_read()
        print("Place cameras and arms as intended. Verify BOTH named previews during warmup.")
        print("Right JoyCon end button ends a take; -1 rejects. q rejects; Ctrl+C stops.")
        while dataset.num_episodes < args.episodes:
            if input("Enter to warm up/position for 5s; q to stop: ").strip().lower() == "q":
                break
            rows = []
            attempt = quality / "attempts" / f"{uuid.uuid4().hex}.json"
            outcome = {"accepted": False, "episode_index": dataset.num_episodes, "rows": rows}
            recording = False
            try:
                warmup_end = time.monotonic() + 5
                for phase in ("warmup", "record"):
                    recording = phase == "record"
                    print("RECORDING" if recording else "WARMUP")
                    robot.button_control = 0
                    end = time.monotonic() + args.seconds if recording else warmup_end
                    while time.monotonic() < end:
                        tick = time.monotonic()
                        obs, action, audit = audited_step(robot)
                        if recording:
                            rows.append(audit)
                            dataset.add_frame({**obs, "action": action})
                        for name in robot.cameras:
                            frame = obs[f"observation.images.{name}"].numpy()
                            cv2.imshow(name, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            raise RuntimeError("Operator rejected take")
                        if recording and robot.button_control:
                            if robot.button_control == -1:
                                raise RuntimeError("JoyCon rejected take")
                            break
                        time.sleep(max(0, 1 / args.fps - (time.monotonic() - tick)))
                outcome["timing"] = timing_quality(rows, args.fps)
                if len(rows) < args.min_seconds * args.fps:
                    outcome["timing"]["passed"] = False
                    outcome["timing"]["reason"] = "Take shorter than min-seconds"
                print(json.dumps(outcome["timing"], indent=2))
                if not outcome["timing"]["passed"]:
                    raise RuntimeError("Timing failed; take excluded from training")
                check_images(dataset)
                if input("Successful complete grasp AND placement, both camera views correct? Type yes: ").strip().lower() != "yes":
                    raise RuntimeError("Take not confirmed successful")
                episode = dataset.num_episodes
                write_json(quality / "pending.json", {"episode": episode, "attempt": str(attempt)})
                dataset.save_episode(args.task)
                verify_videos(dataset, episode, len(rows))
                outcome["accepted"] = True
                outcome["operator_confirmed_success"] = True
                write_json(attempt, outcome)
                (quality / "pending.json").unlink()
                print(f"Accepted {dataset.num_episodes}/{args.episodes}")
            except KeyboardInterrupt:
                outcome["error"] = "Operator interrupted"
                write_json(attempt, outcome)
                raise
            except Exception as error:
                outcome["error"] = str(error)
                write_json(attempt, outcome)
                if (quality / "pending.json").exists():
                    raise RuntimeError("Save incomplete; inspect pending.json before training/resuming") from error
                print(f"REJECTED: {error}")
                dataset._wait_image_writer()
                dataset.clear_episode_buffer()
                # Communication/IK/frame errors require inspection before trying again.
                if not rows or (recording and "timing" not in outcome):
                    raise
    finally:
        if dataset is not None:
            dataset.stop_image_writer()
        if robot.is_connected:
            robot.disconnect()
        else:
            # connect() may have opened only part of the hardware before failing.
            for bus in robot.follower_arms.values():
                if getattr(bus, "is_connected", False):
                    try:
                        bus.write("Torque_Enable", 0)
                    finally:
                        bus.disconnect()
            for camera in robot.cameras.values():
                if getattr(camera, "is_connected", False):
                    camera.disconnect()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
