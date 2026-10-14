#!/usr/bin/env bash
# 一键启动全部服务（Windows Git Bash 里运行）：
#   Redis/MySQL(容器) -> Java(8080) -> WSL 嵌入服务(8001) -> WSL Python 主服务(8000) -> 前端(5173)
# 自动处理：Docker 引擎未运行则拉起 Docker Desktop；终端缺 JAVA_HOME 则自动探测 JDK；MySQL 就绪后再启 Java。
# 用法：bash scripts/start-all.sh
# 环境守卫：脚本以 Git Bash(MSYS) 为前提（依赖 /c/ 路径、cygpath、tasklist、powershell、wsl）。
# cmd 里的 `bash` 常解析到 C:\Windows\System32\bash.exe(WSL) 而非 Git Bash；在 Linux/WSL bash 下误跑
# 会因路径体系不符而静默失败。此处检测到非 MSYS bash 时，自动转交本机 Git Bash 重跑本脚本。
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
    echo "[start-all] 未找到 Git Bash，请直接打开 Git Bash 后运行本脚本。"
    exit 1
    ;;
esac
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info() { printf '[start-all] %s\n' "$*"; }
is_up()  { curl -s -o /dev/null "$1"; }
wsl_up() { wsl -e bash -lc "curl -s -o /dev/null http://127.0.0.1:$1$2" >/dev/null 2>&1; }
wait_up() { # wait_up <秒> <命令...>
  local n=$1; shift
  for _ in $(seq 1 "$n"); do "$@" && return 0; sleep 2; done
  return 1
}

# 真后台：把长驻服务放到脱离当前控制台的新隐藏进程里启动。
# Windows 下 `nohup ... &` 的子进程仍挂在本控制台，Ctrl+C 或关闭窗口会连坐误杀；
# cmd.exe 又不认单引号，直接借道 cmd 会把 `wsl -e bash -lc '...'` 拆碎。
# 故改为：命令写入临时 runner，由 PowerShell Start-Process 起一个隐藏 Git Bash 执行（bash 语义，与
# 原 nohup 一致），之后关窗/Ctrl+C 都不影响已启动的服务。
# $1=工作目录(posix)  $2=日志(posix, 覆盖写)  $3=待执行命令行（bash 语义，可含单引号）
run_bg() {
  local gb gb_win runner runner_win jh="${JAVA_HOME:-}"
  case "$jh" in /*) jh=$(cygpath -w "$jh" 2>/dev/null || echo "$jh");; esac
  gb=$(command -v bash)
  gb_win=$(cygpath -w "$gb" 2>/dev/null || echo "$gb")
  runner="/tmp/rb.$$.$RANDOM.sh"
  printf '%s\n' \
    'trap "rm -f -- \"\$0\"" EXIT' \
    'cd "$RESV_BG_WD" && eval "$RESV_BG_CMD" > "$RESV_BG_LOG" 2>&1' \
    > "$runner"
  runner_win=$(cygpath -w "$runner" 2>/dev/null || echo "$runner")
  JAVA_HOME="$jh" RESV_BG_WD="$1" RESV_BG_LOG="$2" RESV_BG_CMD="$3" \
    powershell -NoProfile -Command \
      "Start-Process -FilePath '$gb_win' -ArgumentList '$runner_win' -WindowStyle Hidden" \
    >/dev/null 2>&1
}

# 0) Docker 引擎：未运行则自动拉起 Docker Desktop
# 健壮性：能识别"进程在但引擎卡死"并强制重启；冷启动慢时单轮最多等 240s、带进度心跳；
#         启动后校验进程真起来了（PowerShell 分离启动，失败则 cmd start 兜底）。

# Docker Desktop 主进程是否存活（引擎可能未就绪，但进程在）
_docker_proc_alive() { tasklist 2>/dev/null | grep -qi "Docker Desktop.exe"; }

# 强制退出 Docker Desktop（含后端进程），最多等 20s 让进程退出
_docker_quit() {
  powershell -NoProfile -Command \
    "Stop-Process -Name 'Docker Desktop','com.docker.backend','com.docker.build' -Force -ErrorAction SilentlyContinue" \
    >/dev/null 2>&1
  local i=0
  while [ "$i" -lt 20 ] && _docker_proc_alive; do sleep 1; i=$((i + 1)); done
}

# 拉起 Docker Desktop 并确认进程出现；$1 = exe 的 Windows 路径
_docker_launch() {
  powershell -NoProfile -Command "Start-Process -FilePath '$1'" >/dev/null 2>&1
  cmd //c start "" "$1" >/dev/null 2>&1
  local i=0
  while [ "$i" -lt 20 ]; do
    _docker_proc_alive && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}

# 等待引擎就绪，最多 <秒>；进程中途退出立即返回失败（不干等）
_docker_wait_ready() {
  local budget=$1 el=0
  while [ "$el" -lt "$budget" ]; do
    docker info >/dev/null 2>&1 && return 0
    _docker_proc_alive || return 1
    sleep 5; el=$((el + 5))
    [ $((el % 30)) -eq 0 ] && info "Docker 引擎启动中…已等待 ${el}s"
  done
  return 1
}

# 定位 Docker Desktop.exe（多路径兜底：兼容无 HOME/LOCALAPPDATA 的 shell）
_docker_find_exe() {
  local exe="" c="" pf="${PROGRAMFILES:-/c/Program Files}"
  for c in \
    "${LOCALAPPDATA:-}/Programs/DockerDesktop/Docker Desktop.exe" \
    "$HOME/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe" \
    "$(cygpath -u "${USERPROFILE:-}" 2>/dev/null)/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe" \
    "$pf/Docker/Docker/Docker Desktop.exe"; do
    [ -n "$c" ] && [ -f "$c" ] && { exe="$c"; break; }
  done
  if [ -z "$exe" ]; then
    c=$(cmd //c "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')
    [ -n "$c" ] && c=$(cygpath -u "$c" 2>/dev/null || echo "$c")
    if [ -n "$c" ] && [ -f "$c/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe" ]; then
      exe="$c/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe"
    fi
  fi
  # 最后兜底：本机已确认的安装路径
  if [ -z "$exe" ] && [ -f "/c/Users/24522/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe" ]; then
    exe="/c/Users/24522/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe"
  fi
  printf '%s' "$exe"
}

ensure_docker() {
  docker info >/dev/null 2>&1 && { info "Docker 引擎就绪"; return 0; }

  local exe wpath
  exe=$(_docker_find_exe)
  [ -n "$exe" ] || return 1
  wpath=$(cygpath -w "$exe" 2>/dev/null || echo "$exe")

  # 已有进程但引擎未就绪：先给 30s 判定是在启动还是卡死，卡死则强制重启
  if _docker_proc_alive; then
    info "检测到 Docker Desktop 进程已运行但引擎未就绪，观察 30s…"
    _docker_wait_ready 30 && { info "Docker 引擎就绪"; return 0; }
    info "30s 内未就绪，判定进程卡死，强制重启 Docker Desktop…"
    _docker_quit
  fi

  # 主循环：最多两轮、单轮等 240s，轮间强制重启，规避冷启动慢或半启动卡死
  local attempt=0
  while [ "$attempt" -lt 2 ]; do
    attempt=$((attempt + 1))
    info "第 ${attempt} 次拉起 Docker Desktop…"
    if ! _docker_launch "$wpath"; then
      info "启动后未见 Docker Desktop 进程，强制清理后重试…"
      _docker_quit; sleep 3
      continue
    fi
    if _docker_wait_ready 240; then
      info "Docker 引擎就绪"
      return 0
    fi
    if [ "$attempt" -lt 2 ]; then
      info "等待超时（进程仍在），强制重启后重试…"
      _docker_quit; sleep 3
    fi
  done

  info "Docker 引擎仍不可用。请手动打开 Docker Desktop 查看报错；或先执行 wsl --shutdown 再重跑本脚本。"
  return 1
}

# 0.5) JAVA_HOME：未设置时自动探测本机 JDK
ensure_java_home() {
  if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then return 0; fi
  local d la="${LOCALAPPDATA:-}"
  for d in \
    "/c/Program Files/Java"/jdk-* \
    "/c/Program Files/Eclipse Adoptium"/jdk-* \
    "$la/Programs/Eclipse Adoptium"/jdk-*; do
    if [ -x "$d/bin/java.exe" ]; then
      export JAVA_HOME="$d"
      info "探测到 JAVA_HOME=$d"
      return 0
    fi
  done
  info "未找到 JDK，请先设置 JAVA_HOME"
  return 1
}

# MySQL 就绪等待（容器起来后仍需初始化几秒）
wait_mysql() {
  for _ in $(seq 1 40); do
    docker exec resv-mysql mysqladmin ping -uresv -presv123 --silent >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

[ -f .env ] || { info "缺少 .env，请先 cp .env.example .env 并填 DEEPSEEK_API_KEY"; exit 1; }
ensure_docker || exit 1

# 1) Redis + MySQL
(cd infra && docker compose up -d) >/dev/null 2>&1
if wait_mysql; then
  info "Redis/MySQL 就绪"
else
  info "MySQL 启动超时，见 infra 容器日志"
  exit 1
fi

# 2) Java(8080, Windows)
ensure_java_home || exit 1
if is_up http://127.0.0.1:8080/api/resources; then
  info "Java(8080) 已在运行，跳过"
else
  info "启动 Java(8080)…（首次会编译，稍等）"
  run_bg "$ROOT/backend-java" "$ROOT/backend-java/target/boot.log" 'mvn -q -DskipTests spring-boot:run'
  if wait_up 60 is_up http://127.0.0.1:8080/api/resources; then
    info "Java(8080) 就绪"
  else
    info "Java 启动失败，boot.log 末尾："
    tail -15 backend-java/target/boot.log
    exit 1
  fi
fi

# 3) WSL 嵌入服务(8001, bge-m3)
if wsl_up 8001 /health; then
  info "嵌入服务(WSL:8001) 已在运行，跳过"
else
  info "启动嵌入服务(WSL:8001, WeMM-2B int8 GPU)…"
  EMBED='cd backend-python && EMBED_MODEL=WeMM-Embedding-2B EMBED_MODEL_DIR=/home/cpluto/models/WeMM-Embedding-2B EMBED_OUTPUT_DIM=1024 exec /usr/bin/python3 -m uvicorn app.embed.server:app --host 0.0.0.0 --port 8001'
  run_bg "$ROOT" "$ROOT/backend-python/wsl-embed.log" "wsl -e bash -lc '$EMBED'"
  wait_up 60 wsl_up 8001 /health \
    && info "嵌入服务(WSL:8001) 就绪" \
    || { info "嵌入服务启动失败，见 backend-python/wsl-embed.log"; exit 1; }
fi

# 4) WSL Python 主服务(8000)
if wsl_up 8000 /api/health; then
  info "Python 主服务(WSL:8000) 已在运行，跳过"
else
  info "启动 Python 主服务(WSL:8000)…"
  PY='gw=$(ip route | awk "/default/{print \$3}"); cd backend-python && JAVA_BASE="http://$gw:8080" MEMORY_REDIS_HOST="$gw" exec /usr/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000'
  run_bg "$ROOT" "$ROOT/backend-python/wsl-uvicorn.log" "wsl -e bash -lc '$PY'"
  wait_up 60 wsl_up 8000 /api/health \
    && info "Python 主服务(WSL:8000) 就绪" \
    || { info "Python 主服务启动失败，见 backend-python/wsl-uvicorn.log"; exit 1; }
fi

# 5) 前端(5173)
if is_up http://localhost:5173/; then
  info "前端(5173) 已在运行，跳过"
else
  info "启动前端(5173)…"
  run_bg "$ROOT/frontend-vue" "$ROOT/frontend-vue/target-vite.log" 'npm run dev'
  wait_up 30 is_up http://localhost:5173/ \
    && info "前端(5173) 就绪" \
    || { info "前端启动失败，见 frontend-vue/target-vite.log"; exit 1; }
fi

info "全部就绪：打开 http://localhost:5173  （演示账号 zhanggong / wangzong，密码 123456）"
info "停止所有：bash scripts/stop-all.sh"
