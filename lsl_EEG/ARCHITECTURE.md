# lsl_EEG 工程化规划

## 1. 总原则

`lsl_backend.py` 只负责实时数据接入和服务启动，不负责具体的疲劳算法、离线文件解析或模型推理。

实时监测和离线检测应该共用同一套“数据格式”和“模型接口”，但使用不同的数据入口：

```text
实时 LSL 流 ──> 接收层 ──> 实时处理流水线 ──> 模型 ──> WebSocket ──> web/frontend.html
离线文件 ────> 文件解析层 ──> 离线处理流水线 ──> 模型 ──> HTTP API ──> web/offline-detection.html
```

这样，未来更换模型时，不需要同时修改两个页面，也不会把硬件接收代码和算法代码混在一起。

## 2. 推荐目录

建议逐步调整为下面的结构。第一阶段可以保留当前文件，按功能慢慢迁移。

```text
lsl_EEG/
├─ app/
│  ├─ __init__.py
│  ├─ main.py                    # FastAPI 应用入口、挂载路由和静态页面
│  ├─ config.py                  # 环境变量、端口、LSL 配置
│  └─ dependencies.py            # 共享的 receiver、模型和服务实例
│
├─ api/
│  ├─ __init__.py
│  ├─ realtime_ws.py             # 实时 WebSocket：连接、开始、停止、状态
│  └─ offline_api.py             # 离线文件上传、任务状态、结果查询
│
├─ acquisition/
│  ├─ __init__.py
│  ├─ lsl_receiver.py            # 只负责发现 LSL、拉取样本、断开连接
│  └─ buffer.py                  # 实时环形缓冲区
│
├─ data/
│  ├─ __init__.py
│  ├─ schemas.py                 # EEGChunk、ChannelInfo、AnalysisResult 等数据结构
│  ├─ loaders.py                 # CSV / EDF / BDF 文件读取
│  └─ validation.py              # 通道数、采样率、缺失值和时间轴检查
│
├─ processing/
│  ├─ __init__.py
│  ├─ realtime_pipeline.py       # 实时窗口切分、滤波、特征计算
│  ├─ offline_pipeline.py        # 离线整段分析、分段和汇总
│  ├─ preprocessing.py           # 去直流、滤波、重采样、伪迹处理
│  └─ features.py                # Alpha、Beta、Theta、RMS、EOG 等特征
│
├─ models/
│  ├─ __init__.py
│  ├─ base.py                    # 模型统一接口
│  ├─ registry.py                # 按配置选择模型
│  ├─ realtime_model.py          # 实时模型适配器
│  └─ offline_model.py           # 离线模型适配器
│
├─ services/
│  ├─ __init__.py
│  ├─ realtime_service.py        # 连接 receiver、pipeline、模型并广播结果
│  └─ offline_service.py         # 管理离线任务、进度和结果
│
├─ web/
│  ├─ frontend.html              # 实时监测页面
│  ├─ offline-detection.html    # 离线检测页面
│  ├─ assets/
│  │  ├─ realtime.css
│  │  ├─ offline.css
│  │  └─ common.css
│  └─ js/
│     ├─ realtime.js             # 实时页面交互
│     └─ offline.js              # 离线页面交互
│
├─ tests/
│  ├─ test_loaders.py
│  ├─ test_preprocessing.py
│  ├─ test_features.py
│  ├─ test_models.py
│  └─ test_api.py
│
├─ uploads/                      # 临时上传文件，加入 .gitignore
├─ results/                      # 分析结果，加入 .gitignore
├─ lsl_backend.py                # 兼容入口，最终只调用 app.main
├─ requirements.txt
├─ .env.example
└─ README.md
```

## 3. 每个模块应该负责什么

### `acquisition/lsl_receiver.py`

只处理 LSL 生命周期和原始样本：发现流、连接、拉取、断开、报告采样率和通道信息。它不应该知道“疲劳”“Alpha”或任何模型名称。

### `data/`

统一实时和离线数据格式。例如每一段数据都包含：

```python
EEGChunk(
    samples,          # shape: (channels, samples)
    timestamps,
    sample_rate,
    channel_labels,
    source,
)
```

实时 LSL 和离线 CSV / EDF 最终都转换成这个格式，后面的处理代码就不再关心数据来自哪里。

### `processing/`

放确定性的信号处理和特征计算：滤波、分段、伪迹剔除、频段功率、Alpha 衰减、EOG 事件等。这里尽量写成纯函数，输入数据，返回数据，方便单元测试。

### `models/`

模型只接收已经准备好的窗口或特征，并返回统一结果，不直接读文件、不直接操作 WebSocket。

推荐先定义统一接口：

```python
class EEGModel:
    name: str

    def predict(self, window, metadata) -> dict:
        """返回 score、label、confidence 和可选解释信息。"""
```

以后无论是规则模型、scikit-learn、PyTorch 还是 ONNX，都通过这个接口接入。

### `services/`

服务层负责业务流程：

- `realtime_service`：持续取实时窗口，调用处理和模型，把结果广播给浏览器。
- `offline_service`：接收文件，创建任务，报告进度，运行整段分析，保存结果。

### `api/`

API 层只做协议转换和参数校验，不写算法。

建议的离线接口：

```text
POST /api/offline/jobs          上传文件并创建任务
GET  /api/offline/jobs/{id}     查询进度和状态
GET  /api/offline/jobs/{id}/result  获取最终结果
```

实时页面继续使用 `/ws`，这样两个页面之间不会互相污染。

## 4. 页面文件怎么编辑更合理

目前两个 HTML 都把结构、样式和 JavaScript 写在一个文件里，作为原型没问题，但后续建议拆成：

```text
web/frontend.html       页面结构
web/offline-detection.html
web/assets/common.css   颜色、字体、按钮、面板等共享样式
web/assets/realtime.css 实时页面专属样式
web/assets/offline.css  离线页面专属样式
web/js/realtime.js      WebSocket 和波形逻辑
web/js/offline.js       上传、任务进度和结果逻辑
```

编辑原则：

1. HTML 只保留页面结构和文案。
2. CSS 只负责视觉，不写业务状态判断。
3. JavaScript 只负责页面交互和调用 API。
4. Python 负责数据、算法、模型和任务状态。
5. 页面不要直接实现疲劳评分；页面只展示后端返回的结果。

## 5. 实时模型和离线模型的规划

实时模型和离线模型可以共享同一个模型文件和特征定义，但推理策略不同：

| 项目 | 实时监测 | 离线检测 |
|---|---|---|
| 输入 | 持续到达的小窗口 | 已保存的完整文件 |
| 目标 | 低延迟 | 完整分析和可复查 |
| 输出 | 当前状态、置信度 | 总结、趋势、分段结果 |
| 任务方式 | 常驻后台 | 有进度的任务 |
| 页面协议 | WebSocket | HTTP + 任务查询 |

建议先做一个 `RuleBasedModel` 作为基线，再接入真实模型。这样模型接口、页面字段和任务流程可以先稳定下来，不必等训练模型完成才联调。

## 6. 推荐实施顺序

### 阶段 1：整理边界

1. 保留 `web/frontend.html` 和 `web/offline-detection.html`，先保证页面能打开。
2. 把离线页面中的演示检测逻辑改成调用 `/api/offline/jobs`。
3. 从 `lsl_backend.py` 抽出 `lsl_receiver.py`，行为保持不变。

### 阶段 2：统一数据和处理

1. 建立 `data/schemas.py`。
2. 实现 CSV 读取，随后再加入 EDF / BDF。
3. 把滤波、分段、特征计算放进 `processing/`。
4. 为每个处理函数准备小型测试数据。

### 阶段 3：接入模型

1. 建立 `models/base.py` 和 `models/registry.py`。
2. 先接规则模型，验证端到端流程。
3. 再接入训练模型，模型文件通过配置指定，不写死路径。
4. 实时和离线分别调用同一模型接口。

### 阶段 4：完善任务和结果

1. 离线任务增加 `queued / running / completed / failed` 状态。
2. 结果保存为 JSON，原始文件和结果文件不提交 Git。
3. 页面支持查看每个时间段的结果，而不是只显示一个总分。

## 7. 现在最应该避免的做法

- 不要把离线分析代码直接加进 `lsl_backend.py` 的 WebSocket 命令分支。
- 不要让 HTML 页面自己计算最终疲劳分数。
- 不要让模型代码直接读取上传目录或操作浏览器连接。
- 不要把模型权重、原始 EEG 文件和分析结果提交到 GitHub。
- 不要同时重构所有文件；每次只迁移一个边界，并保持旧入口可运行。

## 8. 适合当前项目的第一步

下一步最值得做的是：

1. 新建 `data/schemas.py`，定义统一 EEG 数据结构。
2. 新建 `data/loaders.py`，先支持 CSV。
3. 新建 `processing/offline_pipeline.py`，先返回演示结果。
4. 新建 `api/offline_api.py`，把离线页面从本地模拟进度改成真实任务进度。

这四步完成后，再接入模型会比较稳，因为实时和离线都已经有清晰的数据入口和结果出口。
