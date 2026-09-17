# Audited SO-100 Recording

Use `record_quality_dataset.py` for the new dataset. The old `record_pi05_cam26_100.sh`
still uses the legacy collector and does not enable these quality gates.

```bash
cd ~/桌面/lerobot-joycon-main
conda activate lerobot_
python record_quality_dataset.py \
  --root datasets/pick_put_quality150 \
  --repo-id task/pick_put_quality150 \
  --episodes 150 --seconds 25 --fps 30 \
  --task "Use both robot arms to pick up the object and place it in the container"
```

Run five pilot demonstrations first by using `--episodes 5`. After reviewing those,
use the same command with `--episodes 150 --resume` to reach 150 total accepted takes.
Do not run another program controlling the arms/cameras at the same time.

Each take has five seconds of JoyCon positioning with two named previews, then
recording for up to `--seconds`. The right JoyCon end signal ends a take; its -1
signal rejects it. `q` in a preview rejects; Ctrl+C aborts the session.
Both previews must show the intended left/right camera views. The operator must
type `yes` after a complete successful grasp and placement. Only accepted takes
count toward the target. The robot disconnects and disables torque on exit.

## Data contract

- Standard LeRobot v2 fields: RGB images, absolute calibrated `observation.state`
  and absolute `action`, task, episode and frame indices. Order follows the config:
  right five joints + right gripper, then left five joints + left gripper.
- Joints use degrees; grippers use calibrated linear percent. Default open target
  remains 57.2957795 percent to preserve the previously used physical opening.
  `--open-percent` is explicit. Do not change it in a resumed dataset.
- Observation is read before the command. All arm targets are checked before either
  arm is written. There is one goal-position write per arm, after load protection.
  The label is the exact integer calibrated target passed to the bus, not the
  requested target, measured future state or raw encoder ticks. Calibration may
  introduce additional encoder quantization.
- `quality/manifest.json` records config, calibration, units and source hashes.
  `quality/attempts/*.json` records accepted/rejected status, requested targets,
  actual bus targets, raw load, protection flags and per-arm/per-camera host times.
  Rejected attempts keep their audit but are excluded from the training table.
- LeRobot timestamps remain frame/fps for format compatibility. Actual measured
  timing is in the audit, and must be used to diagnose jitter or resample for a
  different model. Camera times are HOST RECEIPT, not exposure timestamps. This
  does not provide hardware synchronization or measure hidden USB camera buffering.
- Resume rejects changed config/calibration/code/task/fps. It cannot automatically
  identify a physical camera swap when USB numeric indices are reused; check both
  previews on every session. Camera mounting and scene changes require operator review.

## Quality gates

Reject stale frames (>100ms host age), two-camera skew >50ms, failed IK, invalid
targets/load readings, incomplete takes (<2 seconds by default), average capture
rate below 90% of requested FPS, p95 intervals >1.5 periods, or any gap >3 periods.
Serial reads and JPEG/PNG writing performance still require an actual hardware pilot.
These thresholds reject poor timing; they do not magically guarantee 30Hz.

Every saved PNG is decoded before commit. Encoded videos are fully decoded and
counted after save. `quality/pending.json` blocks resume after an interrupted or
failed commit; do not train on that dataset until the pending episode is inspected.
Keep original PNGs during initial collection. Training-specific normalization and
train/validation episode splits are performed later, in the target model's adapter.

This is model-neutral capture, not a guarantee that a particular pi0.5/g0.5 loader
accepts this format unchanged. Deployment must match this calibration, action units,
camera mapping and protection semantics; the old inference client is not updated here.

## Verification

```bash
python -m tests.test_recording_quality_standalone
```

Hardware-free tests cover target/label agreement, read-before-write ordering,
load direction, stale/skewed cameras, invalid targets, capture timing and missing
images. A temporary dataset has also passed PNG write, MP4 encode/decode and
LeRobot reload. Hardware grasp success remains an operator/physical test.
