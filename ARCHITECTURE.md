# MCW 项目解析

> 本文档由代码通读 + 运行时验证生成（2026-10-11）。**所有"已确认问题"章节的结论都经过实际执行验证**，不是推测。

---

## 1. 这是什么

一个 **脑电（EEG）驱动的轮椅控制系统**。核心链路：

```
OpenBCI Cyton+Daisy 脑电帽 (16 通道, 125 Hz)
   │ 串口/USB dongle
   ▼
backend.py  (Python + FastAPI)          ← 采集 + 指令识别 + 硬件控制
   │ WebSocket
   ▼
frontend.html  (浏览器)                  ← 实时波形 + 控制面板
   │ WebSocket 回传指令
   ▼
backend.py → arduino_control.py → Arduino → 电机
```

用户通过**咬牙**和**眼球转动**产生脑电/肌电信号，系统把它翻译成轮椅的**前进 / 右转 / 停止**。

---

## 2. 文件地图

| 路径 | 行数 | 一句话职责 |
|---|---|---|
| [eeg-dashboard/backend.py](eeg-dashboard/backend.py) | 1414 | **主后端**：BrainFlow 采集 + 指令识别 + Arduino 控制 + 摄像头/视觉 + CSV 录制 + WebSocket 服务 |
| [eeg-dashboard/lsl_backend.py](eeg-dashboard/lsl_backend.py) | 467 | **替代后端**：从 LSL 网络流接收脑电，只转发给前端。**无轮椅控制** |
| [eeg-dashboard/frontend.html](eeg-dashboard/frontend.html) | 2239 | 单文件前端：16 通道 Canvas 波形 + 全套控制面板。**两个后端共用它** |
| [eeg-dashboard/arduino_control.py](eeg-dashboard/arduino_control.py) | 121 | Arduino 串口桥接，只实现 F/R/S 三个动作 |
| [arduino/ar.ino](arduino/ar.ino) | 155 | **当前生效的 Arduino 固件**：接收 F/R/S，驱动电机，带 500ms 保持超时 |
| [eeg-dashboard/wheelchair_motor_control/wheelchair_motor_control.ino](eeg-dashboard/wheelchair_motor_control/wheelchair_motor_control.ino) | 127 | **旁路/旧版固件**，全项目无人引用。波特率和语义都与 ar.ino 不同 |
| [eeg-dashboard/eeg_wheelchair_control/](eeg-dashboard/eeg_wheelchair_control/) | 8 个 .py | **独立的离线控制程序**（CLI 入口），一套平行的旧方案 |
| [eeg-dashboard/detection_multipliers.json](eeg-dashboard/detection_multipliers.json) | 8 | ⚠️ **指令识别的实际灵敏度配置**（被两个消费者用不同 schema 读取） |
| [eeg-dashboard/.env](eeg-dashboard/.env) | 1 | 只有 `OVERSHOOT_API_KEY` |
| [eeg-dashboard/requirements.txt](eeg-dashboard/requirements.txt) | 8 | 依赖清单（**漏了 opencv-python 和 anthropic**） |
| [README.md](README.md) | 313 | 项目说明。**大量内容已过时**（见第 7 节） |

### 关于三个"wheelchair"命名的坑

仓库里有**三个名字相近但完全不同的东西**，极易混淆：

1. `eeg-dashboard/eeg_wheelchair_control/` —— 真正的 Python 包，离线 CLI 方案
2. `eeg-dashboard/wheelchair_motor_control/` —— 只有一个 `.ino` 文件，**不是包**，无人引用
3. `eeg-dashboard/arduino_control.py` —— 后端实际用的 Arduino 桥接

---

## 3. 三条并行的实现路径

这是理解本项目最关键的一点：**同一件事有三套互不调用的实现**。

| 路径 | 入口 | 是否在用 | 检测算法 | 硬件输出 |
|---|---|---|---|---|
| **A. Web 仪表盘** | `python backend.py` | ✅ **实际在用** | `CommandDetector.get_command()` 百分比变化 | `arduino_control.ArduinoController` |
| **B. LSL 转发** | `python lsl_backend.py` | ✅ 可用（备用） | ❌ 无 | ❌ 无 |
| **C. 离线 CLI** | `python -m eeg_wheelchair_control.main` | ❌ **已被调试代码禁用** | 同上（但被短路） | `wheelchair_interface.SerialWheelchairInterface` |

- 路径 A 和 C **共用** `eeg_wheelchair_control` 包里的检测/校准类，但入口、缓冲、输出全不一样。
- 路径 A 绕开 `main.py` 直接用类；路径 C 的检测逻辑被 [main.py:328](eeg-dashboard/eeg_wheelchair_control/main.py#L328) 的 `return None  # Skip normal processing` 短路了。
- 路径 C 的串口字节表（`F/B/L/R/S`，9600）和 ar.ino 固件（`F/R/S`，115200）**对不上**。

---

## 4. 两个后端对比

| | `backend.py` | `lsl_backend.py` |
|---|---|---|
| 数据来源 | BrainFlow 直连 OpenBCI 串口 | LSL 网络流（`pylsl`） |
| 默认端口 | **8080** | **8081** |
| 采集方式 | `board.get_board_data()` 拉取环形缓冲 | 后台线程 `pull_chunk` |
| 推送节奏 | 固定 ~200ms（`WS_UPDATE_INTERVAL`） | 每块数据一条消息（由 LSL 送达节奏决定） |
| 降采样 | ✅ `data[:, ::2]`（**导致时间轴差 2 倍**） | ❌ 不降采样 |
| 指令识别 | ✅ | ❌ |
| Arduino 控制 | ✅ | ❌ |
| 摄像头 / 视觉避障 | ✅（两种实现，都没接通） | ❌ |
| CSV 录制 | ✅（前端无按钮，不可达） | ❌ |
| 校准 | ✅（**保存必然失败**，见问题 #1） | ❌ |
| 通道标签 | 前端硬编码 FP1…O2 | 从 LSL 流元数据读取，失败则回退默认 |
| 前端兼容性 | 完整 | ⚠️ 多数控制按钮无响应 |

**共用同一份 `frontend.html`** —— 两个后端都通过 `FileResponse` 返回它。前端靠 `state` 消息里的 `source` 字段区分，但**前端代码里没有任何地方读取这个字段**，所以它不知道自己连的是哪个后端。

---

## 5. 逐文件详解

### 5.1 `backend.py`（主后端，1414 行）

**全局状态**：单例 `state = EEGState()`（[backend.py:173](eeg-dashboard/backend.py#L173)），持有板卡句柄、WebSocket 列表、指令缓冲、Arduino 状态、校准状态、摄像头状态。**没有锁保护**，靠 Python GIL + 单线程 asyncio 事件循环。

**关键函数：**

| 函数 | 行 | 作用 |
|---|---|---|
| `detect_serial_ports()` | [180](eeg-dashboard/backend.py#L180) | 按平台扫描串口（macOS 强制 `/dev/cu.*`，Windows 用 pyserial） |
| `connect_board()` | [215](eeg-dashboard/backend.py#L215) | 连接 Cyton+Daisy。**会逐个通道发 `x{ch}010110X` 把 PGA 增益降到 1x**（默认 24x 太大） |
| `start_streaming()` | [326](eeg-dashboard/backend.py#L326) | `start_stream(45000)` 开 45 秒环形缓冲 |
| `get_board_data()` | [378](eeg-dashboard/backend.py#L378) | 取数据；用采样序号跳变统计丢包（[:395-399](eeg-dashboard/backend.py#L395-L399)）；把 FP1/FP2 塞进识别缓冲 |
| `detect_command()` | [421](eeg-dashboard/backend.py#L421) | **核心**：跑识别 → 决定 Arduino 动作 → 处理闭锁/保活/避障 |
| `run_quick_calibration()` | [598](eeg-dashboard/backend.py#L598) | 10 秒静息基线校准 |
| `websocket_endpoint()` | [1017](eeg-dashboard/backend.py#L1017) | 每 200ms 推一帧数据 |
| `handle_command()` | [1141](eeg-dashboard/backend.py#L1141) | 处理前端所有命令 |

**`detect_command()` 的动作决策链**（[backend.py:451-531](eeg-dashboard/backend.py#L451-L531)）：这是全项目最复杂的逻辑。

```
result.command
   ├─ 'forward' 或 'both' → 检查视觉避障 → arduino_action = forward / stop
   ├─ 'right'             → arduino_action = right
   └─ 其他（None）        → arduino_action = stop

然后两层迟滞（防抖）：
  1. 闭锁（latch）：只要看到有效指令就记住 _active_action，
     指令消失后仍保持 RELEASE_TOL = 0.5 秒才真正停（容忍手势抖动）
  2. 保活（keepalive）：动作持续期间每 KEEPALIVE_INTERVAL = 0.08 秒重发一次
     → 为了喂饱 ar.ino 的 500ms 保持超时
  3. 避障覆盖：一旦检测到障碍且正在前进 → 立即强制 stop，绕过迟滞

transition 时才打印日志（避免刷屏），stop 只在状态跳变时发一次
```

**视觉避障有两套实现，都没接通**：

- [analyze_frame_with_claude()](eeg-dashboard/backend.py#L835)（后端）—— 把帧发给 Claude Vision API。但**前端从不发 `camera_start`/`vision_enable`**，所以 `state.vision_enabled` 永远是 `False`。
- 前端自己的 Overshoot SDK —— 跑在浏览器里，只看 UI 不变，**不影响后端停车判断**。

结果：`state.vision_enabled and state.object_detected` 这个条件**永远为假**，后端避障拦截是死代码。

**注意**：这里调用的是 `model="claude-sonnet-4-20250514"`（[backend.py:858](eeg-dashboard/backend.py#L858)），是个较旧的模型 ID。

---

### 5.2 `lsl_backend.py`（LSL 替代后端，467 行）★

**用途**：当脑电数据不是来自本地 OpenBCI 串口，而是来自 **LSL（Lab Streaming Layer）网络流**时用这个后端。比如：

- 另一台机器跑 OpenBCI GUI / 采集软件，通过 LSL 广播到网络
- 配套其他 LSL 兼容的脑电设备
- 不需要轮椅控制，只想看波形

它**只做一件事：把 LSL 流里的 EEG 转发给 `frontend.html`**。没有指令识别、没有 Arduino、没有摄像头、没有 CSV、没有校准。

#### 5.2.1 配置常量（[lsl_backend.py:22-33](eeg-dashboard/lsl_backend.py#L22-L33)）

```python
DEFAULT_CHANNEL_LABELS  # Fp1, Fp2, F4, Fz, F3, T7, C3, Cz, C4, T8, P4, Pz, P3, O1, Oz, O2
MAX_CHANNELS = 16                       # 硬上限，超出的通道被截断
MAX_SAMPLES_PER_PULL = 250              # 单次 pull_chunk 上限
PULL_TIMEOUT_SECONDS = 0.05
BACKEND_WINDOW_SECONDS = 2.0            # 服务端只保留最近 2 秒
CLIENT_BACKLOG_SECONDS = 0.25           # 客户端落后时只补最新 0.25 秒
LSL_RESOLVE_TIMEOUT_SECONDS = 0.25
DISCOVERY_INTERVAL_SECONDS = 0.25       # 找不到流时的重试间隔
```

#### 5.2.2 核心类：`LSLReceiver`（[:36](eeg-dashboard/lsl_backend.py#L36)）

一个 **后台线程 + 有界环形缓冲** 的封装。

**线程生命周期**（[:239-304](eeg-dashboard/lsl_backend.py#L239-L304)）：

```
_run(stop_event) 循环：
  ├─ 没有 inlet  → _connect() 发现并连接 LSL 流；失败则等 0.25s 重试
  ├─ 没有 streaming → 等 0.05s（已连接但用户没点开始）
  └─ 正常         → inlet.pull_chunk(timeout=0.05, max_samples=250)
                     → 转置 → 入队 → 修剪窗口
```

**连接逻辑 `_connect()`**（[:201-237](eeg-dashboard/lsl_backend.py#L201-L237)）：

1. `_select_stream()`（[:167](eeg-dashboard/lsl_backend.py#L167)）挑流，优先级：
   - 环境变量 `LSL_STREAM_NAME` 指定的名字（精确匹配）
   - `type` 匹配 `LSL_STREAM_TYPE`（默认 `"EEG"`，大小写不敏感）
   - 都没有 → **拿搜索到的第一个流**（兜底，可能拿错流）
2. `StreamInlet(info, max_buflen=60, recover=True)` —— `recover=True` 允许断流后自动恢复
3. 通道数取 `min(流的通道数, 16)`
4. 采样率取 `nominal_srate()`；若为 0 或负数（不规则采样流）则**回退 500.0**

**数据方向转换**（[:269-275](eeg-dashboard/lsl_backend.py#L269-L275)）—— 这是个容易搞错的点：

```
LSL 给出:   (n_samples, n_channels)
前端期望:   (n_channels, n_samples)   ← 和 backend.py 的格式一致
所以:       channel_major = sample_major.T
```

**时间戳兜底**：如果 LSL 的时间戳数量对不上样本数，就用 `arange(n)/sample_rate + time.time()` 现场造（[:278-280](eeg-dashboard/lsl_backend.py#L278-L280)）。

**缓冲区结构**（[:55-57](eeg-dashboard/lsl_backend.py#L55-L57)）：

```python
self._chunks = deque()
# 每项: (sequence, channel_major_data, timestamps, first_sample_index)
```

**滑动窗口修剪**（[:293-296](eeg-dashboard/lsl_backend.py#L293-L296)）：只保留最近 `2.0 秒 × 采样率` 个样本，从队头弹出。**这是显示缓冲，不是存档**。

#### 5.2.3 防延迟累积：`chunks_after()`（[:149-165](eeg-dashboard/lsl_backend.py#L149-L165)）

这是整个文件里最聪明的设计。前端上报自己收到的最后一个 `sequence`，服务端返回之后的未读块。

**但**如果浏览器卡了一下、落后太多，就把所有积压数据全推过去 —— 那样延迟会越积越大，波形永远"慢半拍"。所以代码做了取舍：

```python
sample_budget = sample_rate * 0.25   # 只补最新 0.25 秒
# 从最新的块往回数，凑够预算就停 → 丢掉中间的老数据，保证低延迟
```

**代价**：主动丢数据，波形会出现跳变。这是刻意的权衡（宁可跳，不要延迟）。

#### 5.2.4 WebSocket 端点（[:398-448](eeg-dashboard/lsl_backend.py#L398-L448)）

```
循环（每 0.02 秒）：
  1. 收前端命令（超时 0.02s 就跳过）
  2. 检查状态签名（connected, streaming, name, sample_rate, num_channels, error）
     → 只要有一个变了才推 state 消息（避免每帧刷 state）
  3. 若已连接且已开始 → chunks_after() → 每块推一条 data 消息
```

**sequence 归零处理**（[:416-417](eeg-dashboard/lsl_backend.py#L416-L417)）—— 一个容易踩的坑：

```python
if not status["connected"] or status["sequence"] < last_sequence:
    last_sequence = 0
```

新的 inlet 会把 `sequence` 重置为 0。如果客户端的游标还停在旧的大数值上，它会一直等新流"追上"旧序号 —— 永远等不到，画面就卡死。所以检测到这种情况要重置游标。

#### 5.2.5 支持的命令（只有子集，[:349-395](eeg-dashboard/lsl_backend.py#L349-L395)）

| 命令 | 行为 |
|---|---|
| `get_state` | 推 `state_message()` |
| `detect_ports` | 返回 `["LSL 数据流（自动发现）"]` 占位（没有真串口） |
| `connect` | `receiver.start()` 然后**最多等 2 秒**轮询连接结果 |
| `start` | `resume_streaming()`（会 `flush()` 掉暂停期间的数据） |
| `stop` | `stop_streaming()`（只置标志位，线程还在跑） |
| `disconnect` | 真正断开，关 inlet，join 线程 |
| `arduino_list_ports` | 返回空数组 `[]` |
| `wheelchair_enable/disable` | **一律回 `{"enabled": False}`** |

**未实现**（前端会发但后端无响应）：`calibrate`、`arduino_connect`、`arduino_disconnect`、`arduino_test`。

#### 5.2.6 启动参数

```bash
python lsl_backend.py --port 8081 --stream-name MyStream --stream-type EEG
```

环境变量：`LSL_STREAM_NAME`、`LSL_STREAM_TYPE`（默认 `EEG`）、`OVERSHOOT_API_KEY`。

#### 5.2.7 `lsl_backend.py` 的问题

1. **`dropped_samples` 永远是 0** —— 已核实：全文件只在 [:52](eeg-dashboard/lsl_backend.py#L52) 和 [:227](eeg-dashboard/lsl_backend.py#L227) 被赋值为 0，**没有任何自增**。前端的"丢包"统计在 LSL 后端下必然恒为 0。（对比：`backend.py` 用采样序号跳变真的在统计，[:395-399](eeg-dashboard/backend.py#L395-L399)）
2. **`resume_streaming()` 直接改属性不加锁**（[:100](eeg-dashboard/lsl_backend.py#L100)），和后台线程的 `_lock` 临界区存在轻微竞态。
3. **`_connect()` 里 `self.error` 赋值没加锁**（[:204](eeg-dashboard/lsl_backend.py#L204)）。
4. **兜底拿第一个流**（[:182](eeg-dashboard/lsl_backend.py#L182)）—— 如果网络上有多个 LSL 流且没有名字匹配，可能订阅到错误的流，且不会有任何警告。
5. **切到此后端时前端多个按钮无响应** —— 见第 7 节问题 #10。

---

### 5.3 `frontend.html`（前端，2239 行）

单文件：HTML + 内联 CSS + 内联 `<script type="module">`。**前端不感知自己连的是哪个后端**。

**布局**：三列 Grid（`280px 1fr 340px`）

- **左列**：串口连接、启停、统计信息（通道/采样率/样本数/丢包）、运行日志
- **中列**：16 通道 Canvas 波形 + 增益/量程/时间窗口控制 + 摄像头画面区（默认隐藏）
- **右列**：运动状态、Arduino 控制（串口 + 手动测试方向键）、Overshoot 视觉开关、轮椅启用/校准、指令显示、信号特征卡、指令统计、变化量日志、指令历史

**图表**：**没有任何图表库**（不是 Plotly，尽管 [README.md:246](README.md#L246) 声称是）。全部是原生 **Canvas 2D 手绘**：

- `initChart()`（[:1344](eeg-dashboard/frontend.html#L1344)）为 16 个通道各建一个 `<canvas>`，按 2x retina 缩放
- `drawChannel(ch)`（[:1415](eeg-dashboard/frontend.html#L1415)）：减去窗口均值去直流 → 统计超量程比例和 RMS → 画中心线 + 折线
- **不是 `requestAnimationFrame` 循环**，而是**每收到一条 `data` 消息重绘一次**

唯一从 CDN 引入的是 **Overshoot 视觉 SDK**（`esm.run/@overshoot/sdk`），与图表无关。

**前端硬编码参数**：`numChannels: 16`、`sampleRate: 125`（会被后端 state 覆盖）、`timeWindowSeconds: 5`、`vertScale: 100uV`、`gain: 1`。

**前端已知问题**：

- `case 'calibration_progress'` 里的 `const percent`（[:1995](eeg-dashboard/frontend.html#L1995)）**没有块级花括号**，所有 case 共享作用域（脆弱写法）。`msg.progress` 缺失会得到 `NaN%`。
- `timeBuffer` / `streamStartTime` 收集了但**从不用于渲染** —— x 轴完全按采样索引画，底部时间轴是静态的。`timestamps` 数据被浪费。
- `csvRecording` 变量声明后从未使用。
- `start_result` 后**自动启用轮椅**（[:1961-1963](eeg-dashboard/frontend.html#L1961-L1963)），不检查是否已校准。

---

### 5.4 `arduino_control.py`（Arduino 桥接，121 行）

`ArduinoController` 类 + 模块级单例 `arduino`（[:121](eeg-dashboard/arduino_control.py#L121)）。

**只实现了三个动作**：

| 方法 | 行 | 发出的字节 |
|---|---|---|
| `forward()` | [:95](eeg-dashboard/arduino_control.py#L95) | `"F\n"` |
| `right()` | [:101](eeg-dashboard/arduino_control.py#L101) | `"R\n"` |
| `stop()` | [:107](eeg-dashboard/arduino_control.py#L107) | `"S\n"` |

**没有 `left()`，也没有 `backward()`。**

- `connect(port, baudrate=115200)`：`timeout=1`，连上后 **`sleep(2)` 等 Arduino 自动复位**，然后主动发一次 `stop()` 确保上电即停（[:49](eeg-dashboard/arduino_control.py#L49)）
- `_send()` 用 `threading.Lock` 保护写操作，末尾补 `\n` 并 `flush()`
- 有 `_last_logged_cmd` 去重，避免保活重发时刷屏

---

### 5.5 `arduino/ar.ino`（当前生效固件，155 行）

- `Serial.begin(115200)` —— 和 Python 侧默认一致 ✅
- 上电打印 `READY: send F, R (repeat to hold), S`
- **只认三个字符**：`F` → 前进、`R` → 右转、`S` → 停止。其他字符**静默忽略**（没有 else 分支）
- 会跳过 `\n` / `\r` / 空格 → Python 补的 `\n` 不会误触 ✅

**运动参数**：

| 动作 | 左轮 | 右轮 | 说明 |
|---|---|---|---|
| `F` | +170 | +170 | 直行 |
| `R` | **+170** | **−170** | **原地旋转**（不是单轮枢转） |
| `S` | 0 | 0 | 停止 |

- 引脚：`LEFT_REV=5, LEFT_FWD=6, RIGHT_FWD=9, RIGHT_REV=10`
- `PWM_MAX=170, PWM_MIN=45`
- **软启动/软停斜坡**：加速 140 PWM/秒，减速 80 PWM/秒（`:ACCEL_PWM_PER_S` / `DECEL_PWM_PER_S`）
- 控制循环 10ms

**关键安全机制 —— 保持运行超时（hold-to-run）**：

```cpp
if (mode != STOPPED && (millis() - lastCmdTime) > 500) mode = STOPPED;
```

**每 500ms 必须重发 `F` 或 `R`，否则自动停车。** 这正好对应 `backend.py` 里 0.08 秒的保活间隔（`KEEPALIVE_INTERVAL`）—— 两者是配套设计的。

⚠️ **没有硬件看门狗**（无 `wdt_enable`），保护完全靠这个软件超时。

---

### 5.6 `wheelchair_motor_control.ino`（旁路固件，127 行）

**全项目没有任何代码引用它。** 但如果你误刷这块板子，会双重失配：

| | `ar.ino`（生效） | `wheelchair_motor_control.ino` |
|---|---|---|
| 波特率 | **115200** | **9600** ❌ 与 Python 不匹配 |
| 命令语义 | **保持式**（必须重发） | **锁存式**（只在变化时执行，不重发也保持） |
| `R` 的动作 | 原地反向旋转 | 单轮枢转（左 120 / 右 0） |
| PWM | 170 / 45 | 150（直行）/ 120（转向） |

**同一个 `R` 字符，两份固件机械行为不同。**

---

### 5.7 `eeg_wheelchair_control/` 包（8 个 .py，约 2100 行）

路径 C 的实现，被 `backend.py` 部分复用（只用 `SignalProcessor`、`CommandDetector`、`CalibrationManager`）。

| 文件 | 行数 | 职责 |
|---|---|---|
| `config.py` | 166 | 全部常量 |
| `signal_processor.py` | 427 | scipy 滤波 + 特征提取 |
| `command_detector.py` | 270 | **指令识别核心** |
| `calibration.py` | 558 | 校准与阈值计算 |
| `data_handler.py` | 490 | 环形缓冲 + 多种数据源 |
| `wheelchair_interface.py` | 330 | 轮椅接口抽象（Mock/Serial/Callback） |
| `main.py` | 590 | CLI 入口（**已被调试代码禁用**） |
| `__init__.py` | 77 | 包导出 |

**`config.py` 关键值**：`FP1_CHANNEL=1`、`FP2_CHANNEL=3`、`SAMPLING_RATE=125`、`WINDOW_SIZE_SAMPLES=125`、`NOTCH_FREQ=60`、EOG 带通 0.5–15 Hz、EMG 带通 20–55 Hz。

#### 5.7.1 实际生效的检测算法（`command_detector.py`）

**这是全项目真正的"大脑"，而且和 README 描述的完全不是一回事。**

它**不做任何滤波**，直接在**原始信号**上算：

```python
fp1_current = np.mean(np.abs(fp1_raw))        # 当前 1 秒窗口的绝对均值
# 维护长度 50 的历史 deque
fp1_baseline = np.mean(历史[:-1])              # 基线 = 历史均值（排除当前帧）
fp1_pct_change = abs(current - baseline) / (baseline + 1e-6)   # 相对变化百分比
```

判定流程（[:113-239](eeg-dashboard/eeg_wheelchair_control/command_detector.py#L113-L239)）：

1. 历史 < 10 帧 → 返回 `warming_up`
2. 比较 `fp1_pct_change >= ch1_threshold`、`fp2_pct_change >= ch3_threshold`
3. **耦合激活（coupled activation）**：若只有一边超阈值，把另一边的阈值降到 `阈值 × (1 - coupled_reduction)` 再判一次
4. **持续时间要求**：通道必须连续超阈值 `change_duration_sec` 秒才算触发
5. **冷却**：距上次指令不足 `cooldown_sec` 秒则忽略
6. 输出：

| 条件 | 命令 |
|---|---|
| FP1 和 FP2 都触发 | `"both"` |
| 只有 FP1 触发 | `"forward"` |
| 只有 FP2 触发 | `"right"` |

**`forward` 和 `right` 就是全部。`left` 和 `backward` 没有任何代码分支。**

阈值来自 [detection_multipliers.json](eeg-dashboard/detection_multipliers.json)（当前：`ch1_threshold=0.15`、`ch3_threshold=0.15`、`coupled_reduction=0.5`、`change_duration_sec=0.1`、`cooldown_sec=0.5`）。**这个文件是真的生效的** —— 已运行时验证，`CommandDetector()` 初始化时确实读到了 0.15。

#### 5.7.2 `signal_processor.py`

只在**校准**路径被用到（`extract_features`），**检测路径完全不经过它**（`CommandDetector` 持有 `self.signal_processor` 但从不调用）。

滤波链（全部 scipy `filtfilt`）：

```
原始 → notch(60Hz, Q=30) → ┬→ EOG 带通 0.5–15 Hz  → fp1_max/fp2_max/blink_peaks
                            └→ EMG 带通 20–55 Hz   → jaw_power = mean(fp1_emg²)
```

⚠️ EMG 上限实际是 **55 Hz**（受 125Hz 采样的 Nyquist=62.5 限制），但注释和 README 都写 100 Hz。

#### 5.7.3 `main.py` 与 `wheelchair_interface.py`

- [main.py:328](eeg-dashboard/eeg_wheelchair_control/main.py#L328) `return None  # Skip normal processing` —— **检测和发指令的逻辑被整段注释掉了**（实际逻辑在 330-347 行被注释）
- `wheelchair_interface.py` 的 `SerialWheelchairInterface` 用 `F/B/L/R/S` 字节表、默认 **9600** 波特率 —— 与 ar.ino（`F/R/S`、115200）**不兼容**。若启用它，`B` 和 `L` 会被固件静默忽略。

---

## 6. WebSocket 协议参考

前端连接 `ws://<host>/ws`，断线 2 秒自动重连。

### 6.1 前端 → 后端（`{command: "..."}`）

| command | 附带字段 | `backend.py` | `lsl_backend.py` |
|---|---|---|---|
| `get_state` | — | ✅ | ✅ |
| `detect_ports` | — | ✅ | ✅（返回占位） |
| `connect` | `port` | ✅ | ✅ |
| `disconnect` | — | ✅ | ✅ |
| `start` | — | ✅ | ✅ |
| `stop` | — | ✅ | ✅ |
| `wheelchair_enable` | — | ✅ | ⚠️ 强制回 False |
| `wheelchair_disable` | — | ✅ | ⚠️ 强制回 False |
| `calibrate` | — | ✅ | ❌ 无响应 |
| `arduino_list_ports` | — | ✅ | ✅（返回 `[]`） |
| `arduino_connect` | `port` | ✅ | ❌ 无响应 |
| `arduino_disconnect` | — | ✅ | ❌ 无响应 |
| `arduino_test` | `test_command` | ✅ | ❌ 无响应 |
| `start_csv` / `stop_csv` | `filename` | ✅（前端从不发） | ❌ |

### 6.2 后端 → 前端（`{type: "..."}`）

| type | 说明 |
|---|---|
| `state` | 连接状态、采样率、通道数/标签、串口、Arduino 状态 |
| `data` | EEG 数据帧。含 `eeg`、`accel`、`timestamps`、`sample_indices`、`stats`，以及可选的 `wheelchair` 和 `vision` |
| `ports` | 串口列表 |
| `connect_result` / `disconnect_result` | 连接结果 |
| `start_result` / `stop_result` | 启停结果 |
| `wheelchair_status` | 轮椅启用状态 |
| `calibration_started` / `calibration_progress` | 校准进度 |
| `arduino_ports` / `arduino_status` / `arduino_test` | Arduino 相关 |
| `csv_result` / `camera_status` / `vision_status` | **前端不处理**（对应命令从不发送） |
| `error` | 错误消息 |

---

## 7. 已确认的问题清单

### 🔴 严重（影响核心功能）

**#1 —— 校准保存必然失败，加载必然静默失败**

[command_detector.py:99-104](eeg-dashboard/eeg_wheelchair_control/command_detector.py#L99-L104)：

```python
def update_thresholds(self, calibration_data: Dict):
    if 'spike_threshold' in calibration_data:
        self.spike_threshold = calibration_data['spike_threshold']
    logger.info(f"Spike threshold: {self.spike_threshold}")   # ← 在 if 外面
```

`self.spike_threshold` **从未在任何地方初始化**，而日志行在 `if` **外面**，所以**无论传什么都会抛 `AttributeError`**。

**运行时已验证**：传入空 dict `{}` 和真实校准产物 `{'blink_threshold': 50.0, ...}` 都抛
`AttributeError: 'CommandDetector' object has no attribute 'spike_threshold'`。

两处后果：

- [backend.py:275-281](eeg-dashboard/backend.py#L275-L281)：连接时自动加载校准，被裸 `except: pass` 吞掉 → **加载校准永远静默失败**
- [backend.py:699](eeg-dashboard/backend.py#L699)：`run_quick_calibration` 里这行在**写盘之前**，异常被外层捕获 → **整个校准被标记 failed，`calibration_profile.json` 根本不会生成**

**修复**：在 `__init__` 里加 `self.spike_threshold = 0.0`，或把日志行移进 `if`，或直接删掉整个方法。

---

**#2 —— 校准阈值对检测完全无效**

`get_command()` **只读** `self.ch1_threshold` / `self.ch3_threshold`（来自 `detection_multipliers.json`），**从不读** `blink_threshold` / `jaw_threshold` / `eye_move_threshold`。

而 `calibration.py`、`quick_calibration()`、`backend.run_quick_calibration()` 产出的**正是后三者**。

**结论：整套 CalibrationManager 和 backend 的校准流程，对实际检测结果是死代码。** 点"校准"按钮的唯一效果是让 UI 显示完成。

**修复方向**：要么让 `get_command()` 消费校准阈值，要么明确废弃校准 UI。

---

**#3 —— `left()` / `backward()` 不存在，但被调用**

`arduino_control.py` **只有** `forward()` / `right()` / `stop()`（已核实）。

但 [backend.py:1254](eeg-dashboard/backend.py#L1254) 和 [backend.py:1256](eeg-dashboard/backend.py#L1256) 调用了：

```python
elif test_cmd == "left":     arduino.left()      # AttributeError
elif test_cmd == "backward": arduino.backward()  # AttributeError
```

前端确实有这两个按钮（[:991](eeg-dashboard/frontend.html#L991) ◄ 左转、[:995](eeg-dashboard/frontend.html#L995) ▼ 后退），会发 `arduino_test` + `test_command`。

**点这两个按钮 → 后端抛 `AttributeError`，该分支处理失败。**

而且即使补上 Python 方法也没用：**ar.ino 固件也只认 F/R/S**，收到 `B`/`L` 会静默忽略。

---

**#4 —— 视觉避障拦不住轮椅（双系统割裂）**

- 前端 Overshoot 避障跑在**浏览器端**，**从不发 `camera_start` / `vision_enable`**
- 所以 `state.vision_enabled` 永远是 `False`
- 而拦截逻辑要求 `state.vision_enabled and state.object_detected`（[backend.py:458-463](eeg-dashboard/backend.py#L458-L463)）→ **永远不成立**

**结果：障碍物只会让右侧 UI 变红 + 蜂鸣，电机不会停。** 前端指令卡里的"已阻止"状态来自 `msg.wheelchair.is_blocked`，在该配置下恒为 `False`。

后端那套 Claude Vision 避障（`analyze_frame_with_claude`）是另一条完全没接上的线，还需要 `ANTHROPIC_API_KEY`（`.env` 里**没有**，只有 `OVERSHOOT_API_KEY`）。

---

### 🟡 中等（影响数据正确性或可用性）

**#5 —— 时间轴差 2 倍**

后端对展示数据隔点降采样 `display_data = data[:, ::2]`（[backend.py:1062](eeg-dashboard/backend.py#L1062)），但上报的 `sampling_rate` 仍是原始值（125）。

前端按 `5 秒 × 125 Hz = 625 点` 当作"5 秒窗口"绘制，实际收到的是 62.5 Hz 的数据 → **屏幕上的 5 秒实际是 10 秒真实数据**。

（`lsl_backend.py` 不降采样，所以没这个问题。）

---

**#6 —— 前端从不发 CSV 命令**

`start_csv` / `stop_csv` 后端实现了（[backend.py:1169-1176](eeg-dashboard/backend.py#L1169-L1176)），UI 里也没有录制按钮 → **CSV 录制功能在 Web 界面下不可达**。前端 `csvRecording` 变量声明后从未使用。

（注：目录里那个 `eeg_recording_1768677797029.csv`，586 行，说明这功能曾经从别处被调用过。）

---

**#7 —— `main.py` 检测逻辑被调试代码禁用**

[main.py:328](eeg-dashboard/eeg_wheelchair_control/main.py#L328) `return None  # Skip normal processing`，真正的检测 + 发指令逻辑被注释在 330-347 行。**CLI 路径永远不会发出任何轮椅指令。**

附带问题：`_display_status`（[:422-445](eeg-dashboard/eeg_wheelchair_control/main.py#L422-L445)）读取 `features['fp1_max']` / `['jaw_power']` / `['signal_quality']`，但 `get_command()` 产出的 features 里**没有这些键** → 如果恢复 `process_window`，会立刻 `KeyError`。

---

**#8 —— `'R'` 字符在检测链路里语义混乱**

- 检测层：FP2 峰值 → `right` → `arduino.right()` → 发 `'R'`
- 固件层：`'R'` → **原地旋转**（左轮 +170 / 右轮 −170），**不是**轮椅通常期望的弧线转向
- 而 `code comments` 里 [command_detector.py:97](eeg-dashboard/eeg_wheelchair_control/command_detector.py#L97) 打印 `Ch1=15% (LEFT)`，[:202-203](eeg-dashboard/eeg_wheelchair_control/command_detector.py#L202-L203) 注释 `ch1_threshold_pct # RIGHT threshold` —— **三处说法互相矛盾**。唯一的事实是代码：Ch1 → `forward`，Ch3 → `right`。

---

**#9 —— 两套不兼容的 JSON schema 共用一个文件**

`detection_multipliers.json` 被两个消费者读取，**键集完全不同**：

| 消费者 | 期望的键 |
|---|---|
| `command_detector.load_detection_settings()` | `ch1_threshold` / `ch3_threshold` / `coupled_reduction` / `change_duration_sec` / `cooldown_sec` |
| `backend.load_multipliers()`（[:567](eeg-dashboard/backend.py#L567)） | `blink_multiplier` / `jaw_multiplier` / `eye_move_multiplier` / `lateral_ratio` |

当前文件是**前者**的 schema。`backend.load_multipliers()` 读不到自己的键，靠"缺哪个补哪个默认值"才没崩 —— **它算出的阈值本来就是死代码**（见 #2）。

---

**#10 —— 切换 LSL 后端后多个控件静默失效**

`lsl_backend.py` 未实现 `calibrate` / `arduino_connect` / `arduino_disconnect` / `arduino_test`，前端点这些按钮**没有任何响应或回执**（不会报错，就是没反应，比报错更难排查）。

`wheelchair_enable` 被强制回 `{"enabled": False}`（[:394-395](eeg-dashboard/lsl_backend.py#L394-L395)）→ 开关点一下立刻弹回。

---

**#11 —— `lsl_backend.py` 的丢包统计恒为 0**

已核实：`dropped_samples` 全文件只在 [:52](eeg-dashboard/lsl_backend.py#L52) 和 [:227](eeg-dashboard/lsl_backend.py#L227) 被赋值为 0，**没有任何自增**。前端的"丢包"永远显示 0。

---

### 🟢 轻微（文档/死代码）

**#12 —— 文档与代码大面积脱节**（详见第 8 节）

**#13 —— 通道号说法三处矛盾**

- `config.py:16-17`：`FP1=1`、`FP2=3`（**唯一生效的**）
- `README.md:100-101, 141-142`："Channel 9 / Channel 10"
- `backend.py:91-92` 注释：`# 9` / `# 10`；[:404-405](eeg-dashboard/backend.py#L404-L405) 注释 "Channel 9 -> index 8"

代码实际用 `eeg_channels[FP1_CHANNEL-1]` = `eeg_channels[0]`。

**#14 —— Notch 滤波器频率可能不适合中国**

`NOTCH_FREQ = 60`（[config.py:27](eeg-dashboard/eeg_wheelchair_control/config.py#L27)）是为北美/日本工频设计的。**中国是 50 Hz** —— 用 60 Hz notch 会漏掉 50 Hz 工频干扰。（代码注释自己也提到了这点。）不过注意：检测路径**不做滤波**，所以这条只影响校准时的特征提取。

**#15 —— 其他死代码**

- `CommandDetector` 持有 `self.signal_processor`（[:73](eeg-dashboard/eeg_wheelchair_control/command_detector.py#L73)）但从不使用
- `config.DETECTION_PRIORITY` 定义了但无人引用
- 大量 config 常量未被检测路径使用：`MIN_CONFIDENCE`、`MIN_SIGNAL_QUALITY`、`BLINK_*` 系列、`SAFETY_TIMEOUT_SEC`、`COMMAND_COOLDOWN_SEC`（检测用的是 json 里的 `cooldown_sec`）
- `get_cooldown_remaining()` 内部重复 `import time`（[:108](eeg-dashboard/eeg_wheelchair_control/command_detector.py#L108)）
- `calibration.py` 保存的 JSON 结构（含 `baseline_stats` / `command_stats`）与 `backend.py` 自己写的（只有 `timestamp`/`thresholds`/`baseline`/`multipliers_used`）**不一致**，两份代码写同一个文件

**#16 —— confidence 语义偏弱**

`confidence = min(1.0, pct_change)` —— 百分比变化轻易就 > 1，所以几乎恒为 `1.0`，`MIN_CONFIDENCE=0.8` 的过滤形同虚设。

---

## 8. 文档与代码的脱节（重要）

**不要相信 README 里关于检测算法的描述。** 具体对照：

| 文档说 | 代码实际 |
|---|---|
| 用 Plotly.js（[README.md:246](README.md#L246)） | **原生 Canvas 手绘**，无任何图表库 |
| FP1=Channel 9, FP2=Channel 10 | `FP1=1, FP2=3` |
| 咬牙 → 前进、双眨眼 → 后退、左看 → 左转、右看 → 右转 | 只有 **前进 / 右转 / 停止**，无后退无左转 |
| 双眨眼检测（200–800ms 内两个峰） | **不存在**，无 blink 检测分支 |
| 幅度不对称判左右（`FP2_max > 1.3 × FP1_max`） | **不存在**，实际是**相对基线的百分比变化** |
| EMG 频段 20–100 Hz | 实际 **20–55 Hz**（Nyquist 限制） |
| cooldown 1.0 秒 | 实际 **0.5 秒**（来自 json） |
| 安全超时 3 秒自动停 | 固件层是 **500ms 保持超时** |
| 前端有频谱面板 | **没有任何频谱/FFT，也没有加速度计面板** |
| 校准 5 个阶段（基线 + 4 个命令各 10 次） | `backend.py` 只跑 **10 秒基线**，且**保存必然失败**（#1） |

**唯一还算准确的**是 [README.md](README.md) 里的硬件规格（Board ID=2、16 通道、125 Hz、Cyton+Daisy 的 Daisy 模块自动检测）和 macOS 串口注意事项（必须用 `/dev/cu.*`）。

---

## 9. 环境与运行

**解释器**：项目 venv `MCW-main\.venv`（基于 Python 3.13.2）。详见 `.venv\pyvenv.cfg`。

> ⚠️ 机器上另有 conda base（`D:\aiagent`）和全局 Python313，**三个解释器版本号都是 3.13**，极易装错环境。

```powershell
cd D:\脑电设备（zong）\MCW-main\eeg-dashboard
..\.venv\Scripts\python.exe backend.py          # 主后端，默认 8080
..\.venv\Scripts\python.exe lsl_backend.py      # LSL 后端，默认 8081
```

然后浏览器打开 `http://127.0.0.1:8080`。

**注意 `requirements.txt` 漏了两个依赖**：`opencv-python`（摄像头，[backend.py:56](eeg-dashboard/backend.py#L56)）和 `anthropic`（视觉避障，[backend.py:63](eeg-dashboard/backend.py#L63)）。两者都有 try/except 兜底，缺了不崩但功能静默禁用。

---

## 10. 建议的修复优先级

| 优先级 | 问题 | 修复量 |
|---|---|---|
| 1 | **#1** `spike_threshold` AttributeError | 1 行（初始化属性） |
| 2 | **#3** `left()`/`backward()` 缺失 | 加两个方法 + **同步改固件** |
| 3 | **#4** 视觉避障不生效 | 需确定方案：前端发 `vision_enable`，还是移除误导性 UI |
| 4 | **#2** 校准与检测脱节 | 需产品决策：接上，还是删掉校准 UI |
| 5 | **#5** 时间轴差 2 倍 | 降采样时同步上报有效采样率 |
| 6 | **#10** LSL 后端的静默失效 | 前端按 `source` 字段禁用不支持的控件 |
| 7 | **#12/#13** 文档与注释 | 批量修正，或直接删掉过时描述 |
| 8 | **#14** 60Hz → 50Hz notch | 一行（仅影响校准） |
