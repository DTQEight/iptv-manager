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

## 自动抓取（交换机端口镜像）

不用手工整理频道表：让交换机把**机顶盒那口**镜像到 NAS 网口，容器自己在镜像流量里认频道。

**原理**（全部被动，不接触机顶盒）：

```
机顶盒换台 → IGMP 加入 239.x.x.x
交换机镜像 → 容器用 scapy 抓到该组播组的 UDP 流（组播地址:端口）
流里是 MPEG-TS → 解析 PID 0x11 的 SDT 表 → 读出 service_name
→ 自动拼成 http://<udpxy>:4022/rtp/239.x.x.x:1025
```

频道名直接来自码流的 SDT 表（GBK/UTF-8 自动识别），所以「翡翠台」「CCTV-1 综合」这类名字是原生的，不是编号。

### 前置条件

1. **交换机配镜像**：源口 = 机顶盒网口，目的口 = NAS 网口；镜像要包含 IGMP 与组播数据（部分交换机叫 "mirroring / port mirroring / SPAN"）
2. **容器拿到网卡**：`docker-compose.yml` 里已经给了 `network_mode: host` + `NET_RAW` + `NET_ADMIN`
3. **一次抓一个网卡**：host 网络下通常是 `eth0` / `enp3s0`，页面下拉里会列出来

### 用法

1. 打开页面 → 右上角「自动抓取」
2. 选网卡，填 udpxy 地址（如 `http://192.168.1.10:4022`）
3. 点「开始抓取」
4. **在机顶盒上逐个换台**，每个台停 3~5 秒（等 SDT 出现）
5. 面板里会实时出现「频道名 + 组播地址 + 状态 ✓」
6. 抓齐后点「加入清单」（默认合并去重，也可以取消勾选改成覆盖）

### 说明与限制

- 只能抓到**你实际换到过的台**，没换过的台不会出现在镜像口上
- 加密/加扰的频道可能没有可读 SDT → 显示「未识别」，这类不会被加入清单
- 组播流量不小（单台 4~8 Mbps，整 VLAN 可能上百 Mbps）。程序只在每个流的前若干包上解析 SDT，识别到名字就不再解析负载，但 Python 抓包仍可能丢包——不影响识别结果
- 没抓到任何东西时先确认：`tcpdump -i eth0 -n igmp` 在 NAS 上能看到 IGMP，说明镜像配对了