# ---------- 1. 先编译 udpxy（Alpine 没有打包，只能自己编） ----------
FROM alpine:3.20 AS udpxy-build

RUN apk add --no-cache build-base git make
RUN git clone --depth 1 https://github.com/pcherenkov/udpxy.git /src \
 && cd /src \
 && make \
 && install -m 0755 udpxy /usr/local/bin/udpxy \
 && /usr/local/bin/udpxy -h 2>&1 | head -3 || true

# ---------- 2. 运行镜像 ----------
FROM python:3.12-alpine

LABEL org.opencontainers.image.source="https://github.com/DTQEight/iptv-manager" \
      org.opencontainers.image.description="本地 IPTV 频道管理（M3U 导入导出 / 搜索 / 播放 / 交换机镜像自动抓取 / 内置 udpxy）" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IPTV_DATA=/data \
    PORT=8080 \
    TZ=Asia/Shanghai \
    UDPXY_ENABLE=1 \
    UDPXY_PORT=4022 \
    UDPXY_CLIENTS=10 \
    UDPXY_BUFSIZE=65536 \
    UDPXY_BUFFERS=32 \
    UDPXY_IFACE= \
    UDPXY_BIND=

WORKDIR /app

# libpcap/tcpdump 给 scapy 抓镜像流量用
RUN apk add --no-cache libpcap tcpdump

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
 && mkdir -p /data/backup

# udpxy 二进制从编译阶段拿
COPY --from=udpxy-build /usr/local/bin/udpxy /usr/local/bin/udpxy

VOLUME ["/data"]

# 8080 = 管理页, 4022 = udpxy
EXPOSE 8080 4022

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]