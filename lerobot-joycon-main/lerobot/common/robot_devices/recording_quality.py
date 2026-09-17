"""Audited observation-before-action capture for JoyCon SO-100 recording."""

import time

import numpy as np
import torch


def protected_target(requested, present, load, max_relative_target=None):
    requested = np.asarray(requested, dtype=np.float64)
    present = np.asarray(present, dtype=np.float64)
    if requested.shape != (6,) or present.shape != (6,):
        raise ValueError("Expected six calibrated targets and positions per arm")
    if not np.isfinite(requested).all() or not np.isfinite(present).all():
        raise ValueError("Non-finite motor target or position")
    load = np.asarray(load, dtype=np.int64)
    if load.shape != (6,) or np.any((load < 0) | (load > 2047)):
        raise ValueError("Invalid STS3215 load register")
    # Bit 10 is direction; protection must work for either direction.
    magnitude = load & 1023
    target = requested.copy()
    if max_relative_target is not None:
        limit = np.asarray(max_relative_target)
        target = present + np.clip(target - present, -limit, limit)
    limited = magnitude > 800
    limited[-1] |= (target[-1] < present[-1]) and magnitude[-1] > 120
    target[limited] = present[limited]
    # Preserve the existing bus target quantization and record that exact target.
    target = target.astype(np.int32)
    if not 0 <= target[-1] <= 100:
        raise ValueError("Gripper command outside calibrated percent range")
    return target, limited


def audited_step(robot, max_camera_age_s=0.1, max_camera_skew_s=0.05):
    started = time.monotonic()
    positions, loads, requests, targets = {}, {}, {}, {}
    audit = {"started_monotonic_s": started, "arms": {}, "cameras": {}}
    for name, bus in robot.follower_arms.items():
        read_started = time.monotonic()
        positions[name] = np.asarray(bus.read("Present_Position")).copy()
        position_time = time.monotonic()
        loads[name] = np.asarray(bus.read("Present_Load")).copy()
        audit["arms"][name] = {
            "read_started_s": read_started, "position_received_s": position_time,
            "load_received_s": time.monotonic(), "load_raw": loads[name].tolist(),
        }
    observation = {"observation.state": torch.from_numpy(
        np.concatenate(list(positions.values())).astype(np.float32))}
    camera_times = []
    for name, camera in robot.cameras.items():
        packet = getattr(camera, "frame_snapshot", None)
        if packet is None:
            raise RuntimeError(f"Camera {name} has no timestamped frame")
        image, received_at = packet
        if time.monotonic() - received_at > max_camera_age_s:
            raise RuntimeError(f"Stale camera frame: {name}")
        if image.dtype != np.uint8 or image.shape != (480, 640, 3):
            raise ValueError(f"Expected RGB uint8 640x480 from {name}")
        observation[f"observation.images.{name}"] = torch.from_numpy(image.copy())
        camera_times.append(received_at)
        audit["cameras"][name] = {"host_received_s": received_at}
    if camera_times and max(camera_times) - min(camera_times) > max_camera_skew_s:
        raise RuntimeError("Camera receipt timestamps exceed skew limit")
    for name, controller in robot.controllers.items():
        request, button, _ = controller.get_command(positions[name])
        if not getattr(controller, "last_ik_success", False):
            raise RuntimeError(f"Inverse kinematics failed for {name}")
        if name == "right":
            robot.button_control = button
        requests[name] = np.asarray(request).copy()
    # Validate every arm before writing either arm.
    for name in robot.follower_arms:
        target, limited = protected_target(
            requests[name], positions[name], loads[name], robot.config.max_relative_target)
        targets[name] = target
        audit["arms"][name].update(requested=requests[name].tolist(),
                                   target=target.tolist(), protected=limited.tolist())
    if camera_times and time.monotonic() - min(camera_times) > max_camera_age_s:
        raise RuntimeError("Camera frame became stale before motor command")
    for name, bus in robot.follower_arms.items():
        audit["arms"][name]["write_started_s"] = time.monotonic()
        bus.write("Goal_Position", targets[name].copy())
        audit["arms"][name]["write_finished_s"] = time.monotonic()
    audit["finished_monotonic_s"] = time.monotonic()
    return observation, torch.from_numpy(np.concatenate(list(targets.values())).astype(np.float32)), audit


def timing_quality(rows, fps):
    if len(rows) < 2:
        return {"passed": False, "reason": "fewer than two frames"}
    intervals = np.diff([r["started_monotonic_s"] for r in rows])
    measured_fps = 1 / float(intervals.mean())
    p95 = float(np.percentile(intervals, 95))
    max_gap = float(intervals.max())
    return {"passed": bool(measured_fps >= fps * 0.9 and p95 <= 1.5 / fps and max_gap <= 3 / fps),
            "measured_fps": measured_fps, "p95_interval_s": p95, "max_interval_s": max_gap}
