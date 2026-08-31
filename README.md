# Nexus 双核 Agent

> 复现抖音博主「机械师Next_God」视频中的 Agent 技术体系
> 双核架构 + 四库管理 + 向量检索 + Linux 系统嵌入 + 可视化控制台

---

## 零、AI 协作说明

本项目的代码开发过程中，大量功能由 **Doubao（豆包）AI 编程助手** 协助完成，包括但不限于：

- 双核架构（决策核心 + 秘书核心）
- 四库管理 + 向量检索
- 嵌入 Linux（Docker/WSL2/Mock）
- Web 控制台（对话/日志/终端/四库/成本/Stats）
- 工具系统（20+ 工具 + MCP + 插件）
- 技能系统（自我改进 + 置信度 + 版本回滚）
- 上下文压缩（80% 阈值 + LLM 摘要）
- 临时工作目录 + 自动清理
- SSE 流式输出（心跳 + 断线续传）
- 任务统计 + 成本监控
- 并行执行 + 工具自动重试
- 需求定义、架构决策、代码审查、功能验证、Git 管理（人类开发者）

---

## 一、项目概述

本项目复现了视频中描述的 **Nexus（Nixxes）双核 Agent 系统**，核心思路是：

- **决策核心（cd D:\nexus_agent
python desktop.py）**：只做推理决策，不查资料、不调工具，保持"决策纯度"
- **秘书核心**：管理四库、预判检索、主动递达上下文，让决策核心始终看到高纯度信息
- **嵌入 Linux**：在 Agent 内部运行一个完整 Linux 系统，提供真实操作环境
- **向量检索**：用 embedding 做"标点定位"式检索，解决知识膨胀问题

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    用户任务输入                           │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│                   秘书核心 (独立 API)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │  工具库   │ │  知识库   │ │  经验库   │ │  记忆库   │   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘   │
│       └─────────────┴─────┬─────┴─────────────┘         │
│                           ▼                              │
│              向量检索 ("标点定位")                         │
│                           ▼                              │
│              LLM 筛选 + 上下文整理                         │
└──────────────────────────┬──────────────────────────────┘
                           │  高纯度上下文 (递达)
                           ▼
┌─────────────────────────────────────────────────────────┐
│                 决策核心 Nexus (独立 API)                  │
│              只看任务 + 上下文 → 输出决策                   │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│              秘书沉淀结果 → 经验库 + 记忆库                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              嵌入 Linux 系统 (Docker / WSL2)              │
│         浏览器 / 终端 / 文件系统 / Agent 半系统权限          │
└─────────────────────────────────────────────────────────┘
```

---

## 三、与视频的技术对应

| 视频中的概念 | 本项目实现 | 文件位置 |
|---|---|---|
| 双核（Nixxes + 秘书） | `DecisionCore` + `SecretaryCore`，两个独立 OpenAI 客户端 | `core/` |
| 各自一条 API | `DECISION_CONFIG` / `SECRETARY_CONFIG` 分开配置 | `config.py` |
| 四库（工具/知识/经验/记忆） | `FourLibraries`，JSON 持久化 + 向量索引 | `libraries/four_libraries.py` |
| 秘书预判、递达上下文 | `SecretaryCore.anticipate()`：检索 → LLM 筛选 → 递达 | `core/secretary_core.py` |
| 决策纯度高、不模糊 | 决策核心的 system prompt 限定只做决策，上下文由秘书提供 | `core/decision_core.py` |
| 高维向量搜索 | `VectorStore`，支持 embedding API + TF-IDF 回退 | `libraries/vector_store.py` |
| 标点定位（非逐页翻） | 向量余弦相似度直接命中，不遍历 | `libraries/vector_store.py` |
| Nixxes 嵌入秘书 + 半系统权限 | 决策核心通过秘书访问四库和 Linux，不直接接触底层 | `core/dual_agent.py` |
| 嵌入 Linux 系统 | `LinuxEmbed`，支持 Docker / WSL2 / Mock 三种模式 | `system/linux_embed.py` |
| 日志可视化 | `AgentLogger` + Web UI 日志面板 | `utils/logger.py` |
| 成本监控 | `CostTracker`，按 token 估算花费 | `utils/cost_tracker.py` |
| UI 界面 | Flask Web 控制台（对话/日志/终端/四库/成本） | `ui/` |
| 二进制层重构 | **规划中**，视频作者也尚未实现（见第八节） | — |

---

## 四、安装

### 4.1 环境要求

- Python 3.10+
- （可选）Docker Desktop —— 获得真实嵌入 Linux 环境
- （可选）WSL2 —— Windows 下的替代方案

### 4.2 安装步骤

```bash
# 1. 进入项目目录
cd D:\nexus_agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key (二选一)
# 方式 A: 环境变量
set DS_API_KEY=你的DeepSeek_API_KEY

# 方式 B: 直接修改 config.py 中的 DECISION_CONFIG / SECRETARY_CONFIG
```

### 4.3 支持的 API 服务商

任何 OpenAI 兼容 API 都可以，修改 `config.py` 中的 `base_url` 和 `model`：

| 服务商 | base_url | 推荐模型 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 智谱 AI | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

> 建议：秘书用便宜的 flash 类模型，决策用好一点的模型。

---

## 五、使用方法

### 5.1 启动 Web UI（推荐）

```bash
python run.py
```

浏览器访问 `http://127.0.0.1:7860`，界面包含四个面板：

- **对话区**：与 Nexus 对话，每次任务会显示秘书递达的上下文
- **协作日志**：实时查看秘书、Nexus、Linux 的协作过程
- **成本监控**：今日 token 消耗和估算花费
- **Linux 终端**：在嵌入的 Linux 系统中执行命令
- **四库管理**：浏览工具库/知识库/经验库/记忆库的内容

### 5.2 命令行模式

```bash
python run.py --cli
```

支持的特殊命令：
- `linux <命令>` —— 在嵌入 Linux 中执行命令
- `stats` —— 查看系统状态
- `quit` —— 退出

### 5.3 演示模式

```bash
python run.py --demo
```

自动运行两个示例任务，展示完整流程。

---

## 六、核心技术详解

### 6.1 双核架构为什么有效

传统单 Agent 的问题：
```
任务 → LLM(思考+查工具+查资料+决策) → 结果
         ↑ 上下文越来越臃肿 → 答案失真 → 甚至超 token 限制
```

双核架构：
```
任务 → 秘书(检索+筛选+整理) → 高纯度上下文 → Nexus(纯决策) → 结果
         ↑ 独立 API, 上下文不挤占决策核心
```

关键收益：
1. **上下文隔离**：两个核心的对话历史完全独立
2. **决策纯度**：Nexus 不需要在"查资料"和"做决策"之间切换注意力
3. **可扩展性**：秘书可以升级检索能力，不影响决策核心

### 6.2 向量检索（"标点定位"）

视频中说"别人是一页一页翻，它直接做标点定位"，本质是：

| 方式 | 原理 | 复杂度 | 语义理解 |
|---|---|---|---|
| 一页一页翻 | 遍历所有文档，逐条关键词匹配 | O(n) | 差（只能匹配字面） |
| 标点定位（向量检索） | 文本 → embedding 向量 → 余弦相似度命中 | O(log n) | 好（理解语义） |

本项目的 `VectorStore`：
- 优先调用 embedding API（DeepSeek `text-embedding-v3`，1024 维）
- API 不可用时自动回退到 TF-IDF 余弦相似度
- 所有四库条目共享一个向量空间，检索时按库名过滤

### 6.3 四库的作用

| 库 | 存什么 | 对应人类记忆 |
|---|---|---|
| 工具库 | 可用工具的描述和用法 | 技能记忆 |
| 知识库 | 领域知识、事实、文档 | 语义记忆 |
| 经验库 | 过往任务的结果摘要 | 程序性记忆 |
| 记忆库 | 做过什么、用户偏好 | 情景记忆 |

秘书在每次任务后自动把结果写入经验库和记忆库，实现"学习和升级"。

### 6.4 嵌入 Linux 系统

三种模式自动检测：

1. **Docker 模式**（推荐）：
   - 自动拉取 `ubuntu:22.04` 镜像
   - 创建后台容器 `nexus-linux`
   - 安装 curl、wget、vim、python3 等基础工具
   - Agent 通过 `docker exec` 执行命令

2. **WSL2 模式**：
   - Windows 自带的 Linux 子系统
   - 通过 `wsl bash -c` 执行命令
   - 无需额外安装

3. **Mock 模式**：
   - 无 Docker/WSL 时的演示模式
   - 模拟 `ls`、`pwd`、`whoami`、`uname` 等基本命令
   - 用于体验界面，不提供真实环境

嵌入 Linux 的意义：
- Agent 有了真正的"身体"——可以执行命令、读写文件、运行程序
- 对应视频中说的"半系统权限"
- 是后续"二进制层重构"的基础

### 6.5 成本监控

每次 API 调用记录：
- 输入/输出 token 数
- 按模型单价估算费用（元/百万 token）
- 今日累计花费

单价在 `config.py` 的 `COST_CONFIG` 中配置，可根据实际调价更新。

---

## 七、目录结构

```
D:\nexus_agent\
├── run.py                  # 入口 (Web / CLI / Demo)
├── config.py               # 全局配置 (API、模型、路径)
├── requirements.txt        # Python 依赖
├── README.md               # 本文档
├── core/                   # 双核核心
│   ├── dual_agent.py       # 总控: 秘书 → 决策 → 沉淀
│   ├── decision_core.py    # 决策核心 Nexus
│   └── secretary_core.py   # 秘书核心 (四库+预判)
├── libraries/              # 四库 + 向量检索
│   ├── four_libraries.py   # 四库管理
│   └── vector_store.py     # 向量存储 (embedding/TF-IDF)
├── system/                 # 系统层
│   └── linux_embed.py      # 嵌入 Linux (Docker/WSL/Mock)
├── ui/                     # Web 界面
│   ├── web_ui.py           # Flask 后端 API
│   └── templates/
│       └── index.html      # 前端控制台
├── utils/                  # 工具
│   ├── logger.py           # 结构化日志
│   └── cost_tracker.py     # 成本监控
└── data/                   # 运行时数据 (自动生成)
    ├── tools.json          # 工具库
    ├── knowledge.json      # 知识库
    ├── experience.json     # 经验库
    ├── memory.json         # 记忆库
    ├── vectors.json        # 向量索引
    ├── agent_log.jsonl     # 日志
    └── cost_log.json       # 成本记录
```

---

## 八、关于"二进制层重构"

视频中提到的终极目标——**用二进制代码在 Linux 底层重构 Nexus，让 Linux 成为系统本身**——本项目**未实现**，原因如下：

1. 视频作者本人也说"还没做，不确定能不能成功"
2. 这涉及内核模块开发、系统调用劫持、二进制插桩等底层技术
3. 可行性存疑：在 Linux 内核里跑 LLM 推理目前不现实，更可能的是用 eBPF 或内核模块做系统调用级别的 Agent 控制

如果未来要探索这个方向，可能的路径：
- **eBPF**：在内核中挂载钩子，监控/拦截系统调用
- **LD_PRELOAD**：用户态动态库注入，拦截 libc 调用
- **WASI / WebAssembly**：在沙箱中运行 Agent 逻辑，接近系统级
- **Rust 内核模块**：Linux 6.1+ 支持 Rust 写内核模块

---

## 九、与视频原版的差距

| 功能 | 视频原版 | 本项目 |
|---|---|---|
| 双核 Agent | ✅ | ✅ |
| 四库管理 | ✅ | ✅ |
| 向量检索 | ✅ | ✅ |
| 独立 API | ✅ | ✅ |
| 嵌入 Linux（桌面级） | ✅ 有浏览器/文件管理器 GUI | ⚠️ 命令行级（Docker/WSL） |
| UI 界面 | ✅ Aily Blockly 搭建 | ✅ Flask Web 控制台 |
| 远程嵌入 | ✅ | ❌（可扩展） |
| 工具调用执行 | ✅ | ⚠️ 仅 Linux 命令执行，未接 function calling |
| 二进制层重构 | 🔄 规划中 | ❌ 未实现 |
| 成本展示 | ✅ | ✅ |

---

## 十、扩展方向

1. **接入 Function Calling**：让 Nexus 可以真正调用工具库中的工具
2. **多秘书并行**：一个决策核心配多个专业秘书（代码秘书、搜索秘书等）
3. **远程嵌入**：把 Agent 界面嵌入到其他网页/应用
4. **本地模型**：用 Ollama 跑本地模型，完全离线、零 API 费用
5. **知识库自动更新**：秘书定期从互联网抓取新信息入库
6. **经验反思**：任务完成后让秘书复盘，提炼可复用的经验模式

---

## 十一、常见问题

**Q: 没有 API Key 能跑吗？**
A: 能启动 Web UI 和浏览界面，但对话功能需要 API Key。可以用 Mock 模式体验 Linux 终端。

**Q: Docker 容器会一直运行吗？**
A: 会，设置了 `--restart unless-stopped`。不需要时执行 `docker stop nexus-linux`。

**Q: 数据存在哪里？**
A: 全部在 `data/` 目录下，删除该目录即可重置所有数据。

**Q: 怎么换模型？**
A: 修改 `config.py` 中的 `DECISION_CONFIG` 和 `SECRETARY_CONFIG`，支持任何 OpenAI 兼容 API。

---

*本项目仅用于技术学习和研究。*

---

## 十二、新增功能详解 (v2)

### 12.1 Function Calling 工具调用

Nexus 决策核心支持自动调用工具，流程：思考 → 判断需要工具 → 调用 → 看结果 → 再思考 → 最终回答。

已实现 4 个工具：
- `web_search`：DuckDuckGo 联网搜索（无需 API key）
- `code_exec`：Python 代码执行（子进程，15秒超时）
- `linux_terminal`：在嵌入的 WSL Ubuntu 中执行 shell 命令
- `current_time`：获取当前时间

工具系统参考 YAML 注册 + 工厂模式，新增工具只需在 `tools/providers.yaml` 注册并继承 `BaseTool`。

### 12.2 多轮对话上下文

- 自动保留最近 6 轮对话历史，持久化到 `data/conversation_history.json`
- 秘书预判检索时纳入历史对话，避免重复提问
- 决策核心结合历史给出连贯回答
- 提供 `/api/history/clear` 接口清空历史

### 12.3 经验反思机制

- 每次任务完成后，秘书自动复盘：哪些做对了、哪些可改进、提炼可复用经验
- 有价值的反思自动存入经验库，下次类似任务可被检索到
- 对应视频中"学习、记忆、升级的能力"

### 12.4 知识库自动更新

- `KnowledgeUpdater` 模块：抓取网页 → 提取正文 → LLM 摘要 → 入库
- API：`POST /api/knowledge/import`，传 `{"url": "https://..."}`
- 支持批量导入，每条知识标注来源 URL

### 12.5 远程嵌入

两种方式将 Nexus 嵌入其他网页/应用：

**方式一：iframe 嵌入**
```html
<iframe src="http://127.0.0.1:7860/embed" width="400" height="600"
  style="border:1px solid #ddd;border-radius:8px;"></iframe>
```
访问 `/api/embed/snippet` 可获取自动生成的嵌入代码。

**方式二：外部 API 调用**
```bash
curl -X POST http://127.0.0.1:7860/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```
返回 `{"answer": "...", "context": "...", "cost": 0.01, "time_s": 2.5}`

已开启 CORS，支持跨域调用。

### 12.6 真实成本监控

- token 数来自 API 返回的 `usage` 字段（100% 真实）
- 单价从 `.env` 读取，随时修改
- 账单校准：输入实际花费金额，系统反算真实单价并重算历史记录
  - API：`POST /api/cost/calibrate`
  - 参数：`{"model": "deepseek-v4-flash", "actual_cost": 10.5, "period": "today"}`

### 12.7 WSL2 真实 Linux 环境

- Ubuntu 24.04.4 LTS 安装在 D 盘（`D:\WSL\Ubuntu`），不占 C 盘
- 默认用户 `nexus`，免密 sudo
- 已预装 curl、wget、vim、python3、git
- D 盘自动挂载到 `/mnt/d/`
- `linux_terminal` 工具执行真实 shell 命令

---

## 十三、完整 API 列表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 控制台主页 |
| GET | `/embed` | 精简嵌入页 |
| POST | `/api/chat` | 对话（完整记录） |
| POST | `/api/v1/ask` | 对话（简洁API，供外部调用） |
| POST | `/api/history/clear` | 清空对话历史 |
| GET | `/api/logs` | 协作日志 |
| GET | `/api/cost` | 成本统计 |
| POST | `/api/cost/calibrate` | 账单校准 |
| GET | `/api/stats` | 系统状态 |
| GET | `/api/libraries` | 四库内容 |
| POST | `/api/knowledge/import` | 导入网页到知识库 |
| POST | `/api/linux` | 执行 Linux 命令 |
| GET | `/api/linux/info` | Linux 环境信息 |
| POST | `/api/seed` | 填充示例数据 |
| GET | `/api/embed/snippet` | 获取 iframe 嵌入代码 |

---

*本项目仅用于技术学习和研究。*
