from __future__ import annotations

import io
import os
import re
import shutil
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from .capture import CaptureTask

DATA_DIR = Path(os.environ.get("IPTV_DATA", "/data"))
PLAYLIST = DATA_DIR / "list.m3u"
BACKUP_DIR = DATA_DIR / "backup"

STATIC_DIR = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/s")

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')

# 当前抓取会话（同一时间只跑一个）
_capture: CaptureTask | None = None
_capture_lock = __import__("threading").Lock()


# ---------------------------------------------------------------- M3U 解析/生成


def parse_m3u(text: str) -> list[dict]:
    channels: list[dict] = []
    pending: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            pending = {
                "name": name or attrs.get("tvg-name", ""),
                "tvgName": attrs.get("tvg-name", ""),
                "chno": attrs.get("tvg-chno", ""),
                "logo": attrs.get("tvg-logo", ""),
                "group": attrs.get("group-title", "") or "未分组",
            }
        elif not line.startswith("#") and pending is not None:
            pending["url"] = line
            if pending.get("name"):
                channels.append(pending)
            pending = None
    return channels


def build_m3u(channels: list[dict]) -> str:
    out = ["#EXTM3U"]
    for c in channels:
        name = (c.get("name") or "").strip()
        if not name or not c.get("url"):
            continue
        tvg = (c.get("tvgName") or name).replace('"', "")
        attrs = [
            'tvg-name="%s"' % tvg,
            'tvg-chno="%s"' % (c.get("chno") or ""),
            'tvg-logo="%s"' % (c.get("logo") or ""),
            'group-title="%s"' % ((c.get("group") or "未分组").replace('"', "")),
        ]
        out.append("#EXTINF:-1 %s,%s" % (" ".join(attrs), name))
        out.append(c["url"].strip())
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 存储


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def read_channels() -> list[dict]:
    ensure_dirs()
    if not PLAYLIST.exists():
        return []
    return parse_m3u(PLAYLIST.read_text(encoding="utf-8", errors="replace"))


def write_channels(channels: list[dict]) -> None:
    ensure_dirs()
    if PLAYLIST.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(PLAYLIST, BACKUP_DIR / ("list-%s.m3u" % stamp))
    PLAYLIST.write_text(build_m3u(channels), encoding="utf-8")


# ---------------------------------------------------------------- API


@app.get("/api/config")
def api_config():
    """给前端预填用的运行参数。"""
    host = request.host.split(":")[0] if request.host else ""
    port = os.environ.get("UDPXY_PORT", "4022")
    return jsonify(
        udpxyPort=int(port),
        udpxyBase="http://%s:%s" % (host, port) if host else "",
        udpxyEnabled=os.environ.get("UDPXY_ENABLE", "1") != "0",
        port=int(os.environ.get("PORT", "8080")),
    )


@app.get("/api/health")
def health():
    return jsonify(ok=True, playlist=str(PLAYLIST), exists=PLAYLIST.exists())


@app.get("/api/channels")
def api_channels():
    chans = read_channels()
    groups: dict[str, int] = {}
    for c in chans:
        g = c.get("group") or "未分组"
        groups[g] = groups.get(g, 0) + 1
    mtime = PLAYLIST.stat().st_mtime if PLAYLIST.exists() else None
    return jsonify(
        channels=chans,
        count=len(chans),
        groups=[{"name": k, "count": v} for k, v in sorted(groups.items(), key=lambda x: -x[1])],
        updated=mtime,
    )


@app.put("/api/channels")
def api_save():
    body = request.get_json(silent=True) or {}
    chans = body.get("channels")
    if not isinstance(chans, list):
        return jsonify(error="channels 必须是数组"), 400
    write_channels(chans)
    return jsonify(ok=True, count=len(chans))


@app.post("/api/import")
def api_import():
    """导入 M3U：body 为原始文本；?mode=replace|merge（默认 replace）。"""
    text = request.get_data(as_text=True) or ""
    if not text.strip():
        return jsonify(error="空的 M3U"), 400
    incoming = parse_m3u(text)
    if not incoming:
        return jsonify(error="没解析出任何频道，检查 #EXTINF 格式"), 400
    mode = (request.args.get("mode") or "replace").lower()
    if mode == "merge":
        existing = read_channels()
        seen = {c["url"] for c in existing}
        for c in incoming:
            if c["url"] not in seen:
                existing.append(c)
                seen.add(c["url"])
        write_channels(existing)
        return jsonify(ok=True, mode="merge", count=len(existing), added=len(existing) - len(seen - {c["url"] for c in incoming}))
    write_channels(incoming)
    return jsonify(ok=True, mode="replace", count=len(incoming))


@app.get("/list.m3u")
def serve_playlist():
    """给播放器/TV 订阅用的原始地址。"""
    if not PLAYLIST.exists():
        return Response("#EXTM3U\n", mimetype="audio/x-mpegurl")
    return Response(PLAYLIST.read_text(encoding="utf-8"), mimetype="audio/x-mpegurl")


@app.get("/api/export.m3u")
def export_m3u():
    text = PLAYLIST.read_text(encoding="utf-8") if PLAYLIST.exists() else "#EXTM3U\n"
    return Response(
        text,
        mimetype="audio/x-mpegurl",
        headers={"Content-Disposition": "attachment; filename=list.m3u"},
    )


# ---------------------------------------------------------------- 抓取（交换机镜像）


@app.get("/api/capture/ifaces")
def api_ifaces():
    import socket as _s
    names = []
    try:
        from scapy.all import get_if_list
        names = get_if_list()
    except Exception:
        try:
            names = [n for _i, n in _s.if_nameindex()]
        except Exception:
            names = []
    names = [n for n in names if n and n != "lo"]
    return jsonify(ifaces=sorted(set(names)), default=(names[0] if names else ""))


@app.post("/api/capture/start")
def api_capture_start():
    global _capture
    body = request.get_json(silent=True) or {}
    iface = (body.get("iface") or "").strip()
    udpxy = (body.get("udpxy") or "").strip()
    bpf = (body.get("bpf") or "").strip()
    with _capture_lock:
        if _capture is not None and _capture.running:
            return jsonify(error="已经在抓了，先停止"), 409
        _capture = CaptureTask(iface=iface, udpxy=udpxy, bpf=bpf)
        _capture.start()
    return jsonify(ok=True, status=_capture.status())


@app.post("/api/capture/stop")
def api_capture_stop():
    global _capture
    with _capture_lock:
        if _capture is None:
            return jsonify(ok=True, status=None)
        _capture.stop()
        st = _capture.status()
    return jsonify(ok=True, status=st)


@app.get("/api/capture/status")
def api_capture_status():
    with _capture_lock:
        if _capture is None:
            return jsonify(running=False, channels=0, named=0, igmpGroups=0, packets=0, error="")
        return jsonify(_capture.status())


@app.get("/api/capture/channels")
def api_capture_channels():
    with _capture_lock:
        if _capture is None:
            return jsonify(channels=[], status={"running": False})
        return jsonify(channels=_capture.channels(), status=_capture.status())


@app.post("/api/capture/apply")
def api_capture_apply():
    """把抓到的频道并入现有清单（?mode=merge|replace）。"""
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "merge").lower()
    udpxy = (body.get("udpxy") or "").strip()
    with _capture_lock:
        if _capture is None:
            return jsonify(error="还没有抓取记录"), 400
        if udpxy:
            _capture.udpxy = udpxy.rstrip("/")
        found = _capture.as_m3u_channels()
    if not found:
        return jsonify(error="还没识别出频道名（镜像口上要先有机顶盒在换台）"), 400
    if mode == "replace":
        write_channels(found)
        return jsonify(ok=True, mode="replace", count=len(found))
    existing = read_channels()
    seen_url = {c["url"] for c in existing}
    seen_name = {(c.get("name") or "") for c in existing}
    added = 0
    for c in found:
        if c["url"] in seen_url or c["name"] in seen_name:
            continue
        existing.append(c)
        seen_url.add(c["url"])
        seen_name.add(c["name"])
        added += 1
    write_channels(existing)
    return jsonify(ok=True, mode="merge", added=added, count=len(existing))


@app.get("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.get("/mpegts.js")
def mpegts():
    return send_from_directory(str(STATIC_DIR), "mpegts.js")


if __name__ == "__main__":
    ensure_dirs()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True)
