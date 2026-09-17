# Lerobot: JoyCon 遥操作双臂具身智能工作区

用 Nintendo Joy-Con 遥操作 SO-100 / SO-101 双臂机器人，采集高质量示教数据，
再接入 PI0.5 等策略做推理的完整本地工作区。仓库把驱动、运动学和 LeRobot 流程
整合到一处，开箱即可复现「遥操作 → 采集 → 推理」这一条链路。

## 仓库组成

| 目录 | 作用 | 上游项目 |
| --- | --- | --- |
| `lerobot-joycon-main/` | LeRobot 分支，含 JoyCon 遥操作采集、数据质检、PI0.5 推理工具 | [huggingface/lerobot](https://github.com/huggingface/lerobot) |
| `joycon-robotics/` | Joy-Con 驱动与遥操作封装，含 hidapi / 蓝牙 / udev 免 root 配置 | [box2ai-robotics/joycon-robotics](https://github.com/box2ai-robotics/joycon-robotics) |
| `lerobot-kinematics-main/` | SO-100 / SO-101 正逆运动学库与 MuJoCo 仿真示例 | [box2ai-robotics/lerobot-kinematics](https://github.com/box2ai-robotics/lerobot-kinematics) |

### lerobot-joycon-main

- `record_quality_dataset.py`：带质检门的采集入口，默认 30 FPS、单回合 25 秒，
  自动拒绝陈旧帧、双摄时间偏差过大、IK 失败、无效目标与丢帧严重的回合。
- `record_pi05_cam26_100.sh`：旧的批量采集入口（`control_robot.py`，100 回合）。
- `infer_pi05_robot.py`：通过 websocket 把本地双臂接到 OpenPI 策略服务做闭环推理。
- `set_motors_half_encode.py`：舵机半圈编码相关设置脚本。
- `QUALITY_RECORDING.md`：数据契约、质检阈值与采集注意事项。
- `lerobot/configs/robot/`：`so100_joycon_double_pi05.yaml` 等双臂/单臂机器人配置。

### joycon-robotics

- `joyconrobotics/`：驱动包，输出 `[x, y, z, roll, pitch, yaw]`、夹爪与按键。
- `joycon_read.py`：单手柄自检脚本，只打印数值不落盘，用来确认连接正常。
- `make install`：安装 DKMS 内核模块与 `udev/99-nitendo.rules`，固定设备号，
  避免每次插拔后 ACM 口漂移导致校准文件错位。
- `joyconrobotics_tutorial.ipynb`、`盒桥智能-通用机器人数据采集手柄说明手册V0.5.pdf`：教程与手册。

### lerobot-kinematics-main

- `lerobot_kinematics/`：正运动学、逆运动学与关节参数表（含 SO-101 新校准）。
- `examples/`：MuJoCo 键盘关节角 / 末端位姿示例，以及 JoyCon 仿真与真机示例
  （`lerobot_joycon_gpos_real.py`）。

## 环境要求

- Ubuntu 20.04 / 22.04 双系统（本项目未验证虚拟机、WSL 与 Windows）
- 可用的蓝牙适配器
- NVIDIA 独立显卡（训练与推理）
- Miniconda，Python 3.10（与 LeRobot 保持一致）

硬件：SO-100 / SO-101 机械臂（飞特 STS3215 舵机）、双臂结构、USB 摄像头、
Joy-Con 左右手柄各一只。

## 快速开始

### 1. 安装 Joy-Con 驱动

```bash
cd joycon-robotics
pip install -e .
sudo apt-get update
sudo apt-get install -y dkms libevdev-dev libudev-dev cmake
make install
python joycon_read.py --device right
```

配对与绑定：长按手柄侧边圆形按钮 3 秒进入配对模式，在系统蓝牙里连接
`Joycon (L)` / `Joycon (R)`。单手柄模式同时长按 `R + ZR` 3 秒绑定；
双手柄模式在震动后同时按左手的 `L` 与右手的 `R`。

### 2. 安装运动学库

```bash
cd lerobot-kinematics-main
pip install -e .
python examples/lerobot_keycon_qpos.py   # 键盘控制关节角
python examples/lerobot_keycon_gpos.py   # 键盘控制末端位姿
```

仿真实例建议先用鼠标点一下终端窗口再按键，避免 MuJoCo 视角被误改。

### 3. 配置 LeRobot 环境

```bash
cd lerobot-joycon-main
pip install -e ".[feetech]"
conda install -y -c conda-forge ffmpeg
```

### 4. 采集数据

先跑 5 个回合试拍，确认左右摄像头画面与抓放动作都正确：

```bash
python record_quality_dataset.py \
  --root datasets/pick_put_quality150 \
  --repo-id task/pick_put_quality150 \
  --episodes 5 --seconds 25 --fps 30 \
  --task "Use both robot arms to pick up the object and place it in the container"
```

确认无误后接着采满 150 个可用回合：

```bash
python record_quality_dataset.py \
  --root datasets/pick_put_quality150 \
  --repo-id task/pick_put_quality150 \
  --episodes 150 --seconds 25 --fps 30 \
  --task "Use both robot arms to pick up the object and place it in the container" \
  --resume
```

采集期间不要再运行其他控制机械臂或摄像头的程序。每个回合先用 Joy-Con 花 5 秒
摆位，预览确认后再正式录制；右手柄的信号键结束回合，`-1` 信号则标记该回合无效。

### 5. 推理

先在推理机上启动 OpenPI 策略服务（默认监听 8000 端口），再执行：

```bash
cd lerobot-joycon-main
python infer_pi05_robot.py --host 127.0.0.1 --port 8000 --fps 30
```

## 版本控制说明

以下内容是本地产物，体积大且可重新生成，因此不进版本库：

- `lerobot-joycon-main/datasets/`（录制数据集）、`lerobot-joycon-main/outputs/`
- `lerobot-joycon-main/.codex_tmp/`、各类 `__pycache__/`、`*.egg-info/`、`MUJOCO_LOG.TXT`

`lerobot-joycon-main/.cache/calibration/` 是例外，校准文件会随代码一起提交，
换机器复现时需要它们。需要备份数据集请用 rsync / 网盘，或改用 Git LFS
与 Release 附件。

## 常见问题

- `libtinfo.so.6: no version information available`：conda 环境的 libtinfo
  与系统 bash 冲突导致，不影响命令执行。
- 首次提交报「作者身份未知」：需要先设置 `git config --global user.name` 与
  `user.email`。
- 串口一会儿 `ttyACM0` 一会儿 `ttyACM1`：udev 规则未安装或未重载，
  回到 `joycon-robotics` 重新 `make install` 并重新插拔设备。
- MuJoCo 报 `GLX: Failed to create context`：默认走了集成显卡，
  `sudo prime-select nvidia` 后重启。

## 上游项目与许可

本仓库是在以下项目基础上的本地化整合与流程改动，各子目录保留各自的 LICENSE
与原作者署名：

- [huggingface/lerobot](https://github.com/huggingface/lerobot)（Apache-2.0）
- [box2ai-robotics/joycon-robotics](https://github.com/box2ai-robotics/joycon-robotics)（MIT）
- [box2ai-robotics/lerobot-kinematics](https://github.com/box2ai-robotics/lerobot-kinematics)

硬件、内核驱动与校准问题请优先参考上游文档。交流 QQ 群：948755626。
