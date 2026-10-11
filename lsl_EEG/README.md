# lsl_EEG：模块化脑电实时与离线检测

`lsl_EEG` 是一个独立运行的 EEG 子项目：实时模式通过 LSL 接收脑电流，离线模式上传历史文件并执行分析任务。

## 快速启动

在项目根目录执行：

```powershell
cd D:\脑电设备（zong）\MCW-main
.\.venv\Scripts\python.exe -m pip install -r lsl_EEG\requirements.txt
cd lsl_EEG
..\.venv\Scripts\python.exe lsl_backend.py
```

浏览器打开：

- 实时监测：<http://127.0.0.1:8081/>
- 离线检测：<http://127.0.0.1:8081/offline>
- 健康检查：<http://127.0.0.1:8081/health>

也可以直接从 `lsl_EEG` 目录启动自己的虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe lsl_backend.py
```

## 目录结构

```text
lsl_EEG/
├─ app/
│  ├─ main.py              FastAPI 应用、实时 WebSocket、页面路由
│  └─ config.py            运行配置
├─ api/
│  └─ offline_api.py       离线上传和任务查询接口
├─ acquisition/
│  └─ lsl_receiver.py      LSL 数据发现、连接、拉取和缓冲
├─ data/
│  ├─ schemas.py           EEGChunk、AnalysisResult 等统一数据结构
│  └─ loaders.py           CSV 文件读取，预留 EDF/BDF 入口
├─ models/
│  ├─ base.py              模型统一接口
│  └─ rule_based.py        当前使用的确定性基线模型
├─ services/
│  └─ offline_service.py   离线任务生命周期和模型调用
├─ web/
│  ├─ frontend.html         实时监测页面
│  └─ offline-detection.html 离线检测页面
├─ lsl_backend.py          兼容启动入口
├─ uploads/                临时上传目录，不提交 Git
├─ results/                结果目录，不提交 Git
├─ requirements.txt
├─ ARCHITECTURE.md         详细架构规划
└─ README.md
```

## 运行方式

正式入口是 `app/main.py`，推荐使用模块方式启动：

```powershell
python -m app.main --host 127.0.0.1 --port 8081
```

`lsl_backend.py` 是兼容入口，旧的启动习惯仍然有效：

```powershell
python lsl_backend.py --host 127.0.0.1 --port 8081
```

指定 LSL 流：

```powershell
python lsl_backend.py --stream-name MyEEG --stream-type EEG
```

也可以设置环境变量：

```text
LSL_STREAM_NAME=MyEEG
LSL_STREAM_TYPE=EEG
```

如果使用 `.env`，请复制 `.env.example` 为 `.env`，不要把 `.env` 提交到 Git。

## 实时数据流

```text
LSL 数据源
   ↓
acquisition/lsl_receiver.py
   ↓
app/main.py 的 /ws
   ↓
web/frontend.html
```

实时 WebSocket 的原有消息格式保持不变，因此页面仍然可以接收 `state`、`data`、`connect_result` 等消息。

`LSLReceiver` 只负责接入和缓冲，不负责疲劳评分、模型推理或页面逻辑。

## 离线检测接口

### 创建任务

```text
POST /api/offline/jobs
Content-Type: multipart/form-data
字段：file
可选查询参数：profile、window
```

返回：

```json
{"job_id":"abc123", "status":"queued"}
```

### 查询任务

```text
GET /api/offline/jobs/{job_id}
```

任务状态：

- `queued`：任务已创建
- `running`：正在读取和分析
- `completed`：可以读取 `result`
- `failed`：可以读取 `error`

当前支持：

- CSV：已实现基础数值读取
- EDF/BDF：接口已预留，当前会返回清晰的依赖提示

## 模型接入方式

当前使用 `models/rule_based.py` 作为基线模型，目的是先验证数据、接口和页面链路。以后接入 PyTorch、ONNX 或 scikit-learn 模型时，实现同一个接口即可：

```python
from data.schemas import AnalysisResult, EEGChunk

class MyModel:
    name = "my-model-v1"

    def predict(self, chunk: EEGChunk, metadata: dict | None = None) -> AnalysisResult:
        # 这里调用训练好的模型并返回统一结果
        ...
```

模型代码不要直接：

- 读取上传目录
- 操作 WebSocket
- 修改 HTML
- 拼接 API 响应

模型只接收标准化 EEG 数据，返回 `AnalysisResult`。实时和离线服务可以分别调用同一个模型适配器。

## 编辑规则

- `app/`：应用入口和路由组合
- `api/`：校验 HTTP/WebSocket 参数，不写算法
- `acquisition/`：硬件和 LSL 接入
- `data/`：文件读取和统一数据结构
- `processing/`：滤波、分段、特征计算（后续加入）
- `models/`：模型推理
- `services/`：业务流程和任务生命周期
- `web/`：前端页面结构与交互展示。当前 CSS 和 JavaScript 仍内嵌在页面中，后续可以在不改变 API 的前提下继续拆成 `web/assets/` 和 `web/js/`。

页面只展示后端返回的结果，不在浏览器里实现最终疲劳评分。

## 安全和版本控制

以下内容不应提交到 GitHub：

- `.env`
- EEG 原始文件
- 模型权重
- `uploads/` 中的上传文件
- `results/` 中的分析结果

这些路径已经写入 `lsl_EEG/.gitignore`。

## 后续开发顺序

1. 在 `data/loaders.py` 完善 CSV 的采样率、通道名和时间戳读取。
2. 添加 EDF/BDF 依赖和读取器。
3. 新建 `processing/preprocessing.py` 与 `processing/features.py`。
4. 用真实特征替换基线模型。
5. 让实时服务按窗口调用同一个模型接口。
6. 增加 `tests/`，覆盖文件读取、特征计算、模型输出和 API 状态。

详细边界和迁移建议见 [ARCHITECTURE.md](ARCHITECTURE.md)。
