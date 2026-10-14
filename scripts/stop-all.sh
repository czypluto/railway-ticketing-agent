#!/usr/bin/env bash
# 停止全部服务：前端/Java(Windows) + WSL Python/嵌入；保留 Redis/MySQL 容器。
# 用法：bash scripts/stop-all.sh
# 环境守卫：cmd 里的 `bash` 常解析到 WSL 而非 Git Bash；非 MSYS bash 下误跑会静默失败，此处自动转交 Git Bash。
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*) ;;  # 已在 Git Bash
  *)
    _gb=""
    for _g in "/mnt/c/Program Files/Git/bin/bash.exe" "/mnt/c/Program Files/Git/usr/bin/bash.exe"; do
      [ -x "$_g" ] && { _gb="$_g"; break; }
    done
    if [ -n "$_gb" ]; then
      _abs=$(readlink -f "$0" 2>/dev/null || echo "$0")
      _scr=$(wslpath -w "$_abs" 2>/dev/null || echo "$_abs")
      exec "$_gb" "$_scr"
    fi
    echo "[stop-all] 未找到 Git Bash，请直接打开 Git Bash 后运行本脚本。"
    exit 1
    ;;
esac
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

stop_windows_port() {
  local pid
  pid=$(netstat -ano 2>/dev/null | grep ":$1 " | grep LISTENING | awk '{print $5}' | head -1)
  if [ -n "$pid" ]; then
    taskkill //PID "$pid" //F >/dev/null 2>&1 && echo "[stop] Windows :$1 已停 (pid $pid)"
  fi
}

# Windows 侧：前端 5173、Java 8080
stop_windows_port 5173
stop_windows_port 8080

# WSL 侧：Python 主服务 + 嵌入服务（按特征 pgrep 后 kill，排除自身 PID，避免 pkill 模式漏杀/自杀）
wsl -e bash -lc '
  for pat in "app.main:app" "app.embed.server:app"; do
    pids=$(pgrep -f "$pat" | grep -vw "$$" || true)
    if [ -n "$pids" ]; then kill $pids 2>/dev/null && echo "[stop] WSL $pat 已停"; fi
  done
' 2>/dev/null || true

echo "Redis/MySQL 容器保留运行；如需一并停：cd infra && docker compose stop"
