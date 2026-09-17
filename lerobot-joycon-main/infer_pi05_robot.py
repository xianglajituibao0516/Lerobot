#!/usr/bin/env python3
"""Stream a local bimanual SO-100 to an OpenPI websocket policy server."""

import argparse
import collections
import dataclasses
import functools
import logging
import math
import threading
import time

import cv2
import msgpack
import numpy as np
import torch
import websockets.sync.client

from lerobot.common.robot_devices.robots.factory import make_robot
from lerobot.common.utils.utils import init_hydra_config


def pack_array(obj):
    if isinstance(obj, np.ndarray):
        return {
            b"__ndarray__": True,
            b"data": obj.tobytes(),
            b"dtype": obj.dtype.str,
            b"shape": obj.shape,
        }
    if isinstance(obj, np.generic):
        return {
            b"__npgeneric__": True,
            b"data": obj.item(),
            b"dtype": obj.dtype.str,
        }
    return obj


def unpack_array(obj):
    if b"__ndarray__" in obj:
        return np.ndarray(buffer=obj[b"data"], dtype=np.dtype(obj[b"dtype"]), shape=obj[b"shape"])
    if b"__npgeneric__" in obj:
        return np.dtype(obj[b"dtype"]).type(obj[b"data"])
    return obj


packb = functools.partial(msgpack.packb, default=pack_array)
unpackb = functools.partial(msgpack.unpackb, object_hook=unpack_array)


@dataclasses.dataclass(frozen=True)
class ObservationSnapshot:
    sequence: int
    captured_at: float
    observation: dict[str, torch.Tensor]


@dataclasses.dataclass(frozen=True)
class ActionPlan:
    observation_sequence: int
    observation_time: float
    received_at: float
    actions: np.ndarray


class StreamingState:
    def __init__(self):
        self.condition = threading.Condition()
        self.latest_observation: ObservationSnapshot | None = None
        self.plans: collections.deque[ActionPlan] = collections.deque(maxlen=8)
        self.error: BaseException | None = None


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--robot-path",
        default="lerobot/configs/robot/so100_joycon_double_pi05.yaml",
    )
    parser.add_argument(
        "--prompt",
        default="Use both robot arms to pick up the object and place it in the container",
    )
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("--duration-s", type=float, default=60.0)
    parser.add_argument("--max-relative-target", type=float, default=5.0)
    parser.add_argument(
        "--ensemble-decay",
        type=float,
        default=0.25,
        help="Favor newer overlapping action plans; 0 averages them equally.",
    )
    parser.add_argument(
        "--no-action-limit",
        action="store_true",
        help="Disable relative action clipping and send policy targets directly.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send returned actions to the robot. Without this flag, only test inference.",
    )
    args = parser.parse_args()
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be between 1 and 100")
    if args.fps <= 0:
        parser.error("--fps must be positive")
    return args


def encode_jpeg(image: torch.Tensor, quality: int) -> bytes:
    rgb = image.cpu().numpy()
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise RuntimeError("Could not encode camera image as JPEG")
    return encoded.tobytes()


def make_request(snapshot: ObservationSnapshot, prompt: str, jpeg_quality: int) -> bytes:
    observation = snapshot.observation
    request = {
        "state": observation["observation.state"].cpu().numpy().astype(np.float32),
        "images": {
            "left_wrist": encode_jpeg(observation["observation.images.left_wrist"], jpeg_quality),
            "right_wrist": encode_jpeg(observation["observation.images.right_wrist"], jpeg_quality),
        },
        "prompt": prompt,
    }
    return packb(request)


def inference_loop(websocket, state: StreamingState, stop: threading.Event, args) -> None:
    last_sequence = -1
    try:
        while not stop.is_set():
            with state.condition:
                state.condition.wait_for(
                    lambda: stop.is_set()
                    or (
                        state.latest_observation is not None
                        and state.latest_observation.sequence != last_sequence
                    )
                )
                if stop.is_set():
                    return
                snapshot = state.latest_observation
                assert snapshot is not None
                last_sequence = snapshot.sequence

            encode_started = time.perf_counter()
            payload = make_request(snapshot, args.prompt, args.jpeg_quality)
            encode_ms = (time.perf_counter() - encode_started) * 1000
            request_started = time.perf_counter()
            websocket.send(payload)
            response = websocket.recv()
            if isinstance(response, str):
                raise RuntimeError(f"Policy server error: {response}")

            result = unpackb(response)
            actions = np.asarray(result["actions"], dtype=np.float32)
            if actions.ndim != 2 or actions.shape[1] != 12:
                raise ValueError(f"Expected action chunk shaped (N, 12), got {actions.shape}")
            received_at = time.monotonic()
            plan = ActionPlan(snapshot.sequence, snapshot.captured_at, received_at, actions)
            with state.condition:
                state.plans.append(plan)
                state.condition.notify_all()

            logging.info(
                "Plan %d: %s, total=%.3fs, age=%.3fs, encode=%.1fms, payload=%.0fKiB, server=%s",
                snapshot.sequence,
                actions.shape,
                time.perf_counter() - request_started,
                received_at - snapshot.captured_at,
                encode_ms,
                len(payload) / 1024,
                result.get("server_timing", {}),
            )
    except BaseException as error:
        with state.condition:
            state.error = error
            state.condition.notify_all()
        stop.set()


def select_action(plans: list[ActionPlan], now: float, fps: float, decay: float):
    candidates = []
    for plan in plans:
        action_index = int((now - plan.observation_time) * fps)
        if 0 <= action_index < len(plan.actions):
            candidates.append((plan.observation_time, plan.actions[action_index]))
    if not candidates:
        return None

    newest_time = max(timestamp for timestamp, _ in candidates)
    weights = np.asarray(
        [math.exp(-decay * max(0.0, (newest_time - timestamp) * fps)) for timestamp, _ in candidates],
        dtype=np.float64,
    )
    actions = np.stack([action for _, action in candidates])
    return np.average(actions, axis=0, weights=weights).astype(np.float32)


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    max_relative_target = "null" if args.no_action_limit else str(args.max_relative_target)
    cfg = init_hydra_config(args.robot_path, [f"max_relative_target={max_relative_target}"])
    robot = make_robot(cfg)
    uri = f"ws://{args.host}:{args.port}"
    stop = threading.Event()
    state = StreamingState()
    action_count = 0
    missed_actions = 0

    logging.info("Connecting to policy server at %s", uri)
    with websockets.sync.client.connect(
        uri,
        compression=None,
        max_size=None,
        ping_interval=None,
        close_timeout=5,
    ) as websocket:
        metadata = unpackb(websocket.recv())
        logging.info("Policy server connected; metadata=%s", metadata)
        robot.connect()
        logging.info("Robot connected; execute=%s", args.execute)
        worker = threading.Thread(
            target=inference_loop,
            args=(websocket, state, stop, args),
            name="pi05-inference",
            daemon=True,
        )
        worker.start()

        started_at = time.monotonic()
        next_tick = started_at
        sequence = 0
        worker_error = None
        try:
            while time.monotonic() - started_at < args.duration_s and not stop.is_set():
                captured_at = time.monotonic()
                observation = robot.capture_observation()
                snapshot = ObservationSnapshot(sequence, captured_at, observation)
                with state.condition:
                    state.latest_observation = snapshot
                    plans = list(state.plans)
                    error = state.error
                    state.condition.notify_all()
                if error is not None:
                    raise error

                action = select_action(plans, captured_at, args.fps, args.ensemble_decay)
                if action is not None:
                    if not args.execute:
                        logging.info("Streaming dry run complete; rerun with --execute to move the robot")
                        return
                    robot.send_action(torch.from_numpy(action))
                    action_count += 1
                else:
                    missed_actions += 1

                sequence += 1
                next_tick += 1.0 / args.fps
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()
        finally:
            stop.set()
            with state.condition:
                state.condition.notify_all()
            worker.join(timeout=6)
            with state.condition:
                worker_error = state.error
            if robot.is_connected:
                robot.disconnect()
            logging.info(
                "Stopped after sending %d actions; %d control ticks had no time-aligned plan",
                action_count,
                missed_actions,
            )
        if worker_error is not None:
            raise worker_error


if __name__ == "__main__":
    main()
