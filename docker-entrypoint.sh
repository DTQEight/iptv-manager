#!/bin/sh
# 一个容器里跑两个进程：udpxy（组播转 HTTP-TS） + 管理页（Flask）
set -e

log() { echo "[iptv] $*"; }

if [ "${UDPXY_ENABLE:-1}" != "0" ]; then
  UDPXY_PORT="${UDPXY_PORT:-4022}"
  set -- -p "$UDPXY_PORT" -T -c "${UDPXY_CLIENTS:-10}" -B "${UDPXY_BUFSIZE:-65536}" -M "${UDPXY_BUFFERS:-32}"
  [ -n "${UDPXY_IFACE}" ] && set -- "$@" -m "$UDPXY_IFACE"
  [ -n "${UDPXY_BIND}" ] && set -- "$@" -a "$UDPXY_BIND"
  log "启动 udpxy: udpxy $*"
  /usr/local/bin/udpxy "$@" &
  UDPXY_PID=$!
  # 崩了就重起，别让整个容器跟着挂
  (
    while kill -0 "$UDPXY_PID" 2>/dev/null; do sleep 5; done
    log "udpxy 退出了"
  ) &
else
  log "UDPXY_ENABLE=0，跳过 udpxy"
fi

log "启动管理页: port ${PORT:-8080}"
exec python -m app.server