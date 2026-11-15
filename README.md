# IPTV Manager

本地 IPTV 频道管理（Docker）：导入自己的 M3U、分组搜索、在线预览、导出订阅给 Kodi / TVDesk / TiviMate。

**仓库里只有程序，没有你的频道清单。** 频道数据放在宿主机 `./data` 目录，容器怎么重建都不会丢，也不会进 git。

## 快速开始

```bash
git clone https://github.com/DTQEight/iptv-manager.git
cd iptv-manager

# 起服务（首次会构建镜像）
docker compose up -d --build

# 打开
#   http://<NAS-IP>:8080
```

或者直接跑现成镜像（GitHub Actions 自动构建好的）：

```bash
docker run -d --name iptv-manager \
  -p 8080:8080 \
  -v $(pwd)/data:/data \
  --restart unless-stopped \
  ghcr.io/dtqeight/iptv-manager:latest
```

## 第一次用

1. 打开页面 → 右上角「导入 M3U」
2. 选你自己的 `.m3u` 文件，或直接把内容粘进去
3. 「合并到现有清单」勾上就是追加，不勾就是覆盖
4. 之后可以在页面上增删改、按分组筛选、搜索、点「播放」预览

### 给播放器用的订阅地址

```
http://<NAS-IP>:8080/list.m3u
```

Kodi / TVDesk / TiviMate / IPTV Pro 里填这个地址就行，页面上「复制订阅地址」一键复制。

## 功能

- **导入**：M3U 文件上传或粘贴，支持覆盖 / 合并
- **管理**：新增、编辑（名称/分组/频道号/台标/地址）、删除
- **浏览**：按 `group-title` 自动分组，顶部胶囊切换；名称/频道号/地址实时搜索
- **预览**：内置 mpegts.js，HTTP-TS / udpxy 转发地址可直接在浏览器里播
- **导出**：整包 M3U 下载；`/list.m3u` 作为订阅源给 TV 端
- **自动备份**：每次保存前把旧清单备份到 `data/backup/list-<时间>.m3u`

## 数据

```
data/
├── list.m3u          # 当前频道清单（M3U 标准格式）
└── backup/           # 每次保存前的备份
```

- `data/` 已在 `.gitignore` 里，不会被提交
- 想搬机器：把整个 `data/` 拷走即可
- 也可以直接编辑 `data/list.m3u`，刷新页面就生效

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/channels` | 返回 `{channels, count, groups, updated}` |
| PUT | `/api/channels` | 整包保存，body `{"channels":[...]}` |
| POST | `/api/import?mode=replace\|merge` | body 为 M3U 原文 |
| GET | `/list.m3u` | 原始订阅地址（给播放器） |
| GET | `/api/export.m3u` | 下载 M3U |

## 关于播放

页面的「播放」走浏览器 `<video>` + `mpegts.js`，播的是 **HTTP 形式的 TS 流**：

- 运营商组播（`rtp://239.x.x.x:port`）需要先用 udpxy / msd_lite 转成 `http://<udpxy>:4022/rtp/239.x.x.x:port`
- 浏览器播不了 `rtmp` / `rtsp`，这类地址只能复制出去给播放器用

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `IPTV_DATA` | `/data` | 数据目录 |
| `PORT` | `8080` | 监听端口 |
| `TZ` | `Asia/Shanghai` | 时区 |

## License

MIT。`mpegts.js` 按其自身 Apache-2.0 授权。
