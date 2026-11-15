"""交换机镜像口被动抓取：从镜像流量里找出 IPTV 组播频道，并从 TS 的 SDT 表里读出频道名。

原理
----
机顶盒换台时会发 IGMP 加入某组播组，随后该组播流就出现在交换机镜像口上：

    IGMP join  →  组播地址（239.x.x.x）
    UDP 流     →  端口（通常是 1025/…），负载是 MPEG-TS

MPEG-TS 里 PID 0x11 的 SDT 表带 service_name / service_provider，
解出来就是「翡翠台」「CCTV-1」这类频道名，于是可以自动拼出

    http://<udpxy>:4022/rtp/239.x.x.x:1025

不需要机顶盒配合，也不需要它的频道表。
"""

from __future__ import annotations

import ipaddress
import threading
import time

TS_PACKET = 188
PID_PAT = 0x0000
PID_SDT = 0x0011


def _is_mcast(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_multicast
    except Exception:
        return False


def _decode(raw: bytes) -> str:
    """DVB 里的中文多半是 GBK；依次尝试。"""
    for enc in ("utf-8", "gbk", "big5", "latin-1"):
        try:
            s = raw.decode(enc)
            if s and not s.isspace():
                return s.strip("\x00 \t\r\n")
        except Exception:
            continue
    return ""


class TsProbe:
    """按 188 字节对齐解析 TS，只关心 PAT/SDT。"""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.bytes_seen = 0
        self.sdt_done = False
        self.service_id = None
        self.name = ""
        self.provider = ""

    def feed(self, payload: bytes) -> None:
        if self.sdt_done:
            return
        self.bytes_seen += len(payload)
        self.buf.extend(payload)
        while len(self.buf) >= TS_PACKET:
            if self.buf[0] != 0x47:
                idx = self.buf.find(0x47)
                if idx < 0:
                    self.buf.clear()
                    return
                del self.buf[:idx]
                if len(self.buf) < TS_PACKET:
                    return
            pkt = bytes(self.buf[:TS_PACKET])
            del self.buf[:TS_PACKET]
            self._packet(pkt)
            if self.sdt_done:
                self.buf.clear()
                return

    def _packet(self, pkt: bytes) -> None:
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        pusi = bool(pkt[1] & 0x40)
        if not pusi:
            return
        afc = (pkt[3] >> 4) & 0x03
        off = 4
        if afc & 0x02:
            off = 5 + pkt[4]
        if off >= TS_PACKET:
            return
        ptr = pkt[off]
        start = off + 1 + ptr
        if start >= TS_PACKET:
            return
        section = pkt[start:]
        if pid == PID_SDT:
            self._sdt(section)

    def _sdt(self, sec: bytes) -> None:
        if len(sec) < 12:
            return
        if sec[0] not in (0x42, 0x46):      # 0x42 当前 TS / 0x46 其他 TS
            return
        sec_len = ((sec[1] & 0x0F) << 8) | sec[2]
        end = min(len(sec), 3 + sec_len - 4)  # 去掉 CRC32
        p = 11                                 # 跳过表头到 original_network_id
        while p + 6 <= end:
            # service_id(2) + flags(1) + running/free(1) + descriptors_loop_length(2)
            sid = (sec[p] << 8) | sec[p + 1]
            loop_len = ((sec[p + 4] & 0x0F) << 8) | sec[p + 5]
            d = p + 6
            d_end = min(d + loop_len, end)
            while d + 2 <= d_end:
                tag = sec[d]
                ln = sec[d + 1]
                body = sec[d + 2:d + 2 + ln]
                if tag == 0x48 and len(body) >= 3:      # service_descriptor
                    q = 1
                    plen = body[q]
                    q += 1
                    provider = body[q:q + plen]
                    q += plen
                    if q < len(body):
                        slen = body[q]
                        q += 1
                        sname = body[q:q + slen]
                        name = _decode(sname)
                        if name:
                            self.service_id = sid
                            self.name = name
                            self.provider = _decode(provider)
                            self.sdt_done = True
                            return
                d += 2 + ln
            p = d_end


class _Chan:
    __slots__ = ("group", "port", "packets", "bytes", "first_seen", "last_seen", "probe")

    def __init__(self, group: str, port: int) -> None:
        self.group = group
        self.port = port
        self.packets = 0
        self.bytes = 0
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.probe = TsProbe()

    @property
    def name(self) -> str:
        return self.probe.name or ""

    def as_dict(self) -> dict:
        return {
            "group": self.group,
            "port": self.port,
            "name": self.name,
            "provider": self.probe.provider,
            "serviceId": self.probe.service_id,
            "packets": self.packets,
            "bytes": self.bytes,
            "seen": round(self.last_seen - self.first_seen, 1),
            "hasSdt": bool(self.name),
        }


class CaptureTask:
    """一个抓取会话：起一个 scapy 嗅探线程，收集 (group, port) 与频道名。"""

    def __init__(self, iface: str, udpxy: str = "", bpf: str = "") -> None:
        self.iface = iface
        self.udpxy = udpxy.rstrip("/")
        self.bpf = bpf or "(igmp) or (udp and ip multicast)"
        self.started = time.time()
        self.running = False
        self.error = ""
        self.igmp_groups: dict[str, float] = {}
        self.chans: dict[tuple[str, int], _Chan] = {}
        self.packets = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._run, name="iptv-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False

    def _run(self) -> None:
        try:
            from scapy.all import sniff  # 延迟导入，没装 scapy 时不影响别的接口
        except Exception as exc:                               # pragma: no cover
            self.error = "scapy 不可用：%s" % exc
            self.running = False
            return
        try:
            sniff(
                iface=self.iface or None,
                filter=self.bpf,
                prn=self._handle,
                store=False,
                stop_filter=lambda _p: not self.running,
            )
        except Exception as exc:                               # pragma: no cover
            self.error = "抓包失败（需要 NET_RAW/NET_ADMIN 权限和混杂模式）：%s" % exc
        finally:
            self.running = False

    def _handle(self, pkt) -> None:                            # pragma: no cover
        try:
            from scapy.all import IGMP, IP, UDP
        except Exception:
            return
        if IP not in pkt:
            return
        ip = pkt[IP]
        self.packets += 1

        if IGMP in pkt:
            gaddr = getattr(pkt[IGMP], "gaddr", None)
            if gaddr and _is_mcast(gaddr):
                with self._lock:
                    self.igmp_groups[gaddr] = time.time()
            return

        if UDP in pkt and _is_mcast(ip.dst):
            port = int(pkt[UDP].dport)
            payload = bytes(pkt[UDP].payload)
            with self._lock:
                key = (ip.dst, port)
                ch = self.chans.get(key)
                if ch is None:
                    ch = _Chan(ip.dst, port)
                    ch.probe.feed(payload)
                    self.chans[key] = ch
                ch.packets += 1
                ch.bytes += len(payload)
                ch.last_seen = time.time()
                if not ch.probe.sdt_done and payload:
                    ch.probe.feed(payload)

    # ---------------------------------------------------------------- 输出

    def channels(self) -> list[dict]:
        with self._lock:
            items = [c.as_dict() for c in self.chans.values()]
        items.sort(key=lambda c: (not c["hasSdt"], c["name"] or c["group"]))
        return items

    def status(self) -> dict:
        with self._lock:
            chans = len(self.chans)
            named = sum(1 for c in self.chans.values() if c.name)
            igmp = len(self.igmp_groups)
        return {
            "running": self.running,
            "iface": self.iface,
            "udpxy": self.udpxy,
            "elapsed": round(time.time() - self.started, 1),
            "packets": self.packets,
            "igmpGroups": igmp,
            "channels": chans,
            "named": named,
            "error": self.error,
        }

    def as_m3u_channels(self) -> list[dict]:
        """转成 /api/channels 能直接吃的结构。"""
        out = []
        for c in self.channels():
            if not c["hasSdt"]:
                continue
            url = "%s/rtp/%s:%d" % (self.udpxy, c["group"], c["port"]) if self.udpxy \
                else "rtp://%s:%d" % (c["group"], c["port"])
            out.append({
                "name": c["name"],
                "tvgName": c["name"],
                "group": c["provider"] or "抓取",
                "chno": "",
                "logo": "",
                "url": url,
            })
        return out
