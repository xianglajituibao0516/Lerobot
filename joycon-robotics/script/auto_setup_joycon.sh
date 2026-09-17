#!/usr/bin/env bash
# ------------------------------------------------------------
# setup_joycon.sh  —  自动发现 + 配对 + 信任 + 连接 + 启用自动重连
# 适用于 Ubuntu 22.04+ / BlueZ 5.63+
# ------------------------------------------------------------
set -euo pipefail

SCAN_SECS=15                    # 每轮扫描时长
CONF=/etc/bluetooth/main.conf   # BlueZ 全局配置
NEEDED_KEYS=(
  "AutoEnable=true"
  "ReconnectTrusted=true"
  "ReconnectAttempts=7"
  "ReconnectIntervals=1,2,5"
  "ReconnectProfiles=HID"
  "JustWorksRepairing=always"
  "ControllerMode=bredr"
)

msg() { printf '\e[1;32m%s\e[0m\n' "$*"; }

# ---------- 0) 前置检查 ----------
systemctl is-active --quiet dbus        || { echo "D-Bus 未运行"; exit 1; }   # :contentReference[oaicite:1]{index=1}
systemctl is-active --quiet bluetooth   || { echo "bluetooth.service 未运行"; exit 1; }
rfkill list bluetooth | grep -q "Soft blocked: yes" && { 
    echo "Bluetooth 被 rfkill 软封锁，正在解封…"; rfkill unblock bluetooth;     # :contentReference[oaicite:2]{index=2}
}

# ---------- 1) 扫描 Joy-Con 并保存全部 MAC ----------
msg "🔍 正在扫描 Joy-Con（${SCAN_SECS}s）…请长按 Joy-Con 的 SYNC 小圆键使四灯流水闪烁"   # :contentReference[oaicite:3]{index=3}
mapfile -t macs < <(bluetoothctl --timeout "$SCAN_SECS" scan on \
        | awk '/Joy-Con/ {print $3}' | sort -u)

[[ ${#macs[@]} -eq 0 ]] && { echo "未找到 Joy-Con，退出"; exit 1; }
msg "🎮 发现 ${#macs[@]} 个 Joy-Con：${macs[*]}"

# ---------- 2) 逐个 pair / trust / connect ----------
for mac in "${macs[@]}"; do
  echo "🔗 [$mac] pair/trust/connect with timeout…"
  bluetoothctl --timeout 5 -- pair    "$mac" || { echo "pair 超时";  continue; }
  bluetoothctl --timeout 5 -- trust   "$mac" || { echo "trust 超时"; continue; }
  bluetoothctl --timeout 5 -- connect "$mac" || { echo "connect 超时"; continue; }
done 

# ---------- 3) 写入自动重连配置 ----------
msg "⚙️  检查 $CONF …"
backup="${CONF}.$(date +%s).bak"
grep -q "ReconnectTrusted" "$CONF" || cp "$CONF" "$backup"
for key in "${NEEDED_KEYS[@]}"; do
  grep -q "^$key" "$CONF" || echo "$key" >> "$CONF"
done
msg "✅ 已更新 $CONF（备份于 $backup） —— 启用 ReconnectTrusted 以自动重连"   

# ---------- 4) 重启蓝牙服务 ----------
systemctl restart bluetooth
msg "♻️  bluetooth.service 已重启"

# ---------- 5) 完成 ----------
cat <<EOF

🟢 完成！今后 Joy-Con 只要亮灯就会在 1–2 秒内自动连回系统。
若想合并 L+R 为一个手柄，可自行安装 *joycond*（hid-nintendo 驱动已在内核 5.16+ 合入）。

常见排错：
  • Joy-Con 长按 SYNC 后仍未发现 — 再次确认LED流水闪且附近 Switch 已关机。&#8203;:contentReference[oaicite:7]{index=7}
  • 仍提示 “No default controller” — 检查蓝牙适配器/驱动 & dmesg。&#8203;:contentReference[oaicite:8]{index=8}
  • 蓝牙偶发 core dump — 避免用外部 timeout 杀进程，脚本已使用 bluetoothctl 自带 --timeout 来规避。&#8203;:contentReference[oaicite:9]{index=9}

祝游戏愉快！🎉
EOF
