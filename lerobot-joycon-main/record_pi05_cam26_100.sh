#!/bin/bash
set -e

cd "$(dirname "$0")"

python lerobot/scripts/control_robot.py record \
  --robot-path lerobot/configs/robot/so100_joycon_double_pi05.yaml \
  --fps 30 \
  --tags so100 pi05 bimanual \
  --warmup-time-s 5 \
  --episode-time-s 25 \
  --reset-time-s 5 \
  --num-episodes 100 \
  --push-to-hub 0 \
  --local-files-only 1 \
  --root datasets/pick_put_double_cam26 \
  --repo-id task/pick_put_double_cam26 \
  --single-task "Use both robot arms to pick up the object and place it in the container" \
  --resume 1
