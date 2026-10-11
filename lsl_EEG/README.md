# lsl_EEG —— LSL 脑电波形后端

一个**独立可跑**的最小后端：从 **LSL（Lab Streaming Layer）** 网络流接收脑电数据，实时转发给浏览器里的波形界面。

## 这个子项目是干什么的

和主项目 `eeg-dashboard/backend.py` 不同，它**不接 OpenBCI 硬件、不做指令识别、不控制轮椅**。它只做一件事：

> 发现一个 LSL 数据流 → 持续拉取样本 → 通过 WebSocket 转发给 `frontend.html` 画波形。

**适用场景**：脑电数据来自另一台机器或另一个程序（OpenBCI GUI、采集软件、LSL 兼容设备），你只想在浏览器里看实时波形。

## 目录内容

| 文件 | 说明 |
|---|---|
| `lsl_backend.py` | 后端本体（FastAPI + LSL 接收线程） |
| `frontend.html` | 波形界面（与 `eeg-dashboard/frontend.html` **字节一致**的副本） |
| `requirements.txt` | 依赖清单 |
| `.env.example` | 环境变量模板，复制为 `.env` 后填写 |

---

## 快速开始

### 1. 安装依赖

复用主项目的 venv（里已装好全部依赖）：

```powershell
cd D:\脑电设备（zong）\MCW-main
.\.venv\Scripts\python.exe -m pip install -r lsl_EEG\requirements.txt
```

如果你想让它完全独立，也可以建自己的 venv：

```powershell
cd D:\脑电设备（zong）\MCW-main\lsl_EEG
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 配置（可选）

```powershell
Copy-Item .env.example .env
```

不配也能跑 —— 默认会自动搜索第一个 `type=EEG` 的 LSL 流。

### 3. 启动

```powershell
# 在 lsl_EEG 目录下，用主项目的 venv
..\.venv\Scripts\python.exe lsl_backend.py
```

然后浏览器打开 **http://127.0.0.1:8081**

> 端口是 **8081**（`backend.py` 用的是 8080），所以两个后端可以同时跑。

### 4. 连接

页面上点「连接」→ 再点「开始」。连接时后端会最多等待 2 秒去发现 LSL 流。

---

## 数据源：LSL 流从哪来

**这是这个后端唯一的输入，它不在本仓库里**，需要你自己提供。常见来源：

- **OpenBCI GUI** —— 在 GUI 里开启 LSL 输出
- **LabRecorder** —— 转发已有的流
- **任意 LSL 兼容设备**的配套软件
- **自己写的脚本**，用 `pylsl.StreamOutlet` 广播

没有数据源时，后端会持续打印 `未发现 LSL 数据流，正在继续搜索`，界面停在「未连接」。

> 提示：可以用 `python -m pylsl` 里自带的工具，或 LabRecorder 的界面，确认流是否已经在网络上广播。

---

## 配置项

### 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 绑定地址 |
| `--port` | `8081` | 绑定端口 |
| `--stream-name` | 环境变量 `LSL_STREAM_NAME` | 按名字精确匹配要订阅的流 |
| `--stream-type` | 环境变量 `LSL_STREAM_TYPE`（默认 `EEG`） | 按类型匹配，大小写不敏感 |

```powershell
..\.venv\Scripts\python.exe lsl_backend.py --port 9000 --stream-name MyEEG
```

### 环境变量

通过 `.env` 文件或系统环境变量提供。**本子项目已支持读取 `.env`**（见下方「与主项目版本的差异」）。

| 变量 | 用途 |
|---|---|
| `OVERSHOOT_API_KEY` | 前端视觉面板的 API Key，留空则该输入框不自动填充 |
| `LSL_STREAM_NAME` | 同 `--stream-name` |
| `LSL_STREAM_TYPE` | 同 `--stream-type` |

### 选流优先级

代码里的顺序（[lsl_backend.py](lsl_backend.py) 的 `_select_stream()`）：

1. 名字精确匹配 `stream_name`
2. `type` 匹配 `stream_type`
3. **兜底：拿搜索到的第一个流**

> ⚠️ 第 3 条没有警告。如果网络上有多个 LSL 流且前两条都没匹配上，它可能悄悄订阅到**错误的流**。建议显式指定 `LSL_STREAM_NAME`。

---

## 内部实现

### 数据流

```
[LSL 数据源]  ──LSL 网络流──▶  LSLReceiver 后台线程
                                    │  pull_chunk(timeout=0.05, max_samples=250)
                                    │  转置：(n_samples, n_channels) → (n_channels, n_samples)
                                    │  存入 deque，只保留最近 2 秒
                                    ▼
                              WebSocket 端点（每 0.02s 一轮）
                                    │  chunks_after(客户端游标)
                                    ▼
                              frontend.html（Canvas 手绘波形）
```

### 三个关键设计

**① 数据要转置。** LSL 给出的是 `(样本数, 通道数)`，而前端期望 `(通道数, 样本数)`（和 `backend.py` 的格式一致），所以中间有一步 `.T`。

**② 主动丢数据保延迟。** 如果浏览器卡顿导致落后太多，服务端**不会**把所有积压数据推过去（那样延迟会越积越大，波形永远"慢半拍"）。它只补**最新 0.25 秒**，中间的老数据直接丢弃。代价是波形会出现跳变 —— 这是刻意的权衡：宁可跳，不要延迟。

**③ `sequence` 归零处理。** 每次重新连接，`sequence` 会从 0 重新计数。如果客户端的游标还停在旧的大数值上，它会一直等新流"追上"旧序号 —— **画面会永久卡死**。代码里检测到这种情况会把游标重置为 0，别删这段逻辑。

### 服务端缓冲参数

| 常量 | 值 | 含义 |
|---|---|---|
| `MAX_CHANNELS` | 16 | 硬上限，超出部分截断 |
| `MAX_SAMPLES_PER_PULL` | 250 | 单次拉取上限 |
| `PULL_TIMEOUT_SECONDS` | 0.05 | 拉取超时 |
| `BACKEND_WINDOW_SECONDS` | 2.0 | 服务端保留最近 2 秒（显示用，非存档） |
| `CLIENT_BACKLOG_SECONDS` | 0.25 | 客户端落后时最多补这么多 |
| `DISCOVERY_INTERVAL_SECONDS` | 0.25 | 找不到流时的重试间隔 |

### 通道标签

优先从 LSL 流的元数据（`desc().child("channels")`）读取每个通道的 label；读不到就回退到默认的 16 导联名 `Fp1, Fp2, F4, Fz, F3, T7, C3, Cz, C4, T8, P4, Pz, P3, O1, Oz, O2`。

若流是不规则采样（`nominal_srate() <= 0`），采样率回退为 **500.0**。

---

## 支持的命令

前端能发的命令里，这个后端**只实现了子集**：

| 命令 | 支持 | 行为 |
|---|---|---|
| `get_state` | ✅ | 返回连接状态 |
| `detect_ports` | ✅ | 返回占位标签（没有真串口） |
| `connect` | ✅ | 启动发现线程，最多等 2 秒 |
| `start` | ✅ | 恢复接收（**会丢弃暂停期间的数据**） |
| `stop` | ✅ | 暂停接收（线程仍在跑） |
| `disconnect` | ✅ | 真正断开并关闭 inlet |
| `arduino_list_ports` | ⚠️ | 返回空数组 `[]` |
| `wheelchair_enable` / `wheelchair_disable` | ⚠️ | **一律返回 `enabled: false`** |
| `calibrate` | ❌ | 无响应 |
| `arduino_connect` / `arduino_disconnect` / `arduino_test` | ❌ | 无响应 |

### ⚠️ 前端部分控件会「静默失效」

界面是和 `backend.py` 共用的完整版本，但 LSL 后端不支持轮椅控制 / Arduino。在 LSL 后端下：

- 点「校准」「Arduino 连接」「手动测试方向键」→ **没有任何反应，也不报错**（比报错更难排查）
- 点轮椅开关 → 会立刻弹回关闭状态
- 这是正常的，不是 bug

---

## 已知问题

| # | 问题 | 影响 |
|---|---|---|
| 1 | **`dropped_samples` 永远是 0** | 全文件只有赋值为 0，没有任何自增。前端的「丢包」统计恒为 0 |
| 2 | `resume_streaming()` 直接改属性未加锁 | 与后台线程存在轻微竞态 |
| 3 | `_connect()` 里 `self.error` 赋值未加锁 | 同上 |
| 4 | 兜底订阅「第一个流」且无警告 | 多流环境下可能订阅错流 |
| 5 | 前端不感知后端类型 | 不会隐藏不支持的控件（见上） |

这些问题在 `eeg-dashboard/lsl_backend.py` 里**同样存在**，本副本没有改动。

---

## 与 `eeg-dashboard/lsl_backend.py` 的差异

**唯一一处差异**，在文件顶部的 import 区，有 `[lsl_EEG 子项目额外添加]` 注释标记：

```python
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass
```

原版**不加载 `.env`**，导致前端页面加载时 `fetch('/overshoot_key')` 拿到的 key 是空的（那个输入框不会自动填充）。本副本补上了这个加载逻辑，行为与 `backend.py` 一致。

`dotenv` 没安装时静默跳过，不影响运行。

> 除这一段外，两个文件内容一致。将来修 bug 时记得**两处同步**。

---

## 相关文档

主项目的完整架构分析见 [../ARCHITECTURE.md](../ARCHITECTURE.md)，其中第 5.2 节是这个文件更详细的逐段解读。
