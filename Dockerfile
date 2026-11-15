FROM python:3.12-alpine

LABEL org.opencontainers.image.source="https://github.com/DTQEight/iptv-manager" \
      org.opencontainers.image.description="本地 IPTV 频道管理（M3U 导入导出 / 搜索 / 播放 / 交换机镜像自动抓取）" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IPTV_DATA=/data \
    PORT=8080 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache libpcap tcpdump \
 && pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data/backup

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "app.server"]
