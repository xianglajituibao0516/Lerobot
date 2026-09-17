"""Hardware-free checks; run directly to avoid repository hardware fixtures."""

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image

from lerobot.common.robot_devices.recording_quality import audited_step, protected_target, timing_quality
from record_quality_dataset import check_images, provenance
from lerobot.common.utils.utils import init_hydra_config


class RecordingQualityTest(unittest.TestCase):
    def robot(self):
        events = []
        position = np.array([10, 20, 30, 40, 50, 25], dtype=np.float32)
        class Bus:
            def read(self, register):
                events.append(register)
                return position.copy() if register == "Present_Position" else np.zeros(6, dtype=int)
            def write(self, register, value):
                events.append((register, value.copy()))
        class Controller:
            last_ik_success = True
            def get_command(self, pos):
                return np.array([11.8, 21, 31, 41, 51, 0]), 0, 0
        camera = SimpleNamespace(frame_snapshot=(np.zeros((480, 640, 3), np.uint8), time.monotonic()))
        robot = SimpleNamespace(follower_arms={"right": Bus(), "left": Bus()},
                                controllers={"right": Controller(), "left": Controller()},
                                cameras={"left_wrist": camera, "right_wrist": camera},
                                config=SimpleNamespace(max_relative_target=None))
        return robot, events

    def test_action_is_written_target_and_state_precedes_writes(self):
        robot, events = self.robot()
        obs, action, audit = audited_step(robot)
        writes = [e[1] for e in events if isinstance(e, tuple)]
        np.testing.assert_array_equal(action.numpy(), np.concatenate(writes))
        np.testing.assert_array_equal(obs["observation.state"][:6], [10, 20, 30, 40, 50, 25])
        self.assertTrue(all(isinstance(e, str) for e in events[:4]))
        self.assertEqual(audit["arms"]["right"]["requested"][0], 11.8)

    def test_both_load_directions_and_gripper_open(self):
        for load in (150, 1174):
            target, limited = protected_target([0]*6, [20]*6, [0]*5+[load])
            self.assertEqual(target[-1], 20)
            self.assertTrue(limited[-1])
            target, _ = protected_target([0]*5+[57], [20]*6, [0]*5+[load])
            self.assertEqual(target[-1], 57)

    def test_nonfinite_second_arm_does_not_write_first(self):
        robot, events = self.robot()
        robot.controllers["left"].get_command = lambda pos: (np.full(6, np.nan), 0, 0)
        with self.assertRaises(ValueError):
            audited_step(robot)
        self.assertFalse(any(isinstance(e, tuple) for e in events))

    def test_stale_and_skewed_frames_rejected(self):
        robot, events = self.robot()
        image = robot.cameras["left_wrist"].frame_snapshot[0]
        robot.cameras["left_wrist"] = SimpleNamespace(frame_snapshot=(image, time.monotonic()-1))
        with self.assertRaises(RuntimeError):
            audited_step(robot)
        self.assertFalse(any(isinstance(e, tuple) for e in events))
        robot.cameras["left_wrist"].frame_snapshot = (image, time.monotonic()-0.075)
        with self.assertRaises(RuntimeError):
            audited_step(robot)

    def test_slow_capture_fails_quality(self):
        self.assertTrue(timing_quality([{"started_monotonic_s": i/30} for i in range(60)],30)["passed"])
        self.assertFalse(timing_quality([{"started_monotonic_s": i/15} for i in range(60)],30)["passed"])

    def test_missing_image_prevents_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"frame.png"
            ds = SimpleNamespace(_wait_image_writer=lambda: None, meta=SimpleNamespace(camera_keys=["camera"]),
                                 episode_buffer={"camera": [str(path)]})
            with self.assertRaises(FileNotFoundError):
                check_images(ds)
            Image.new("RGB", (640,480)).save(path)
            check_images(ds)

    def test_existing_config_provenance_without_hardware(self):
        cfg = init_hydra_config("lerobot/configs/robot/so100_joycon_double_pi05.yaml", [])
        result = provenance(cfg)
        self.assertEqual(list(result["calibration"]), ["right", "left"])


if __name__ == "__main__":
    unittest.main()
