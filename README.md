# Nexus 双核 Agent

> 复现抖音博主「机械师Next_God」视频中的 Agent 技术体系
> 双核架构 + 四库管理 + 向量检索 + Linux 嵌入 + 三模式 + 云端备份

---

## AI 协作说明

本项目的代码开发过程中，由 Doubao（豆包）AI 编程助手协助完成：

- 双核架构（决策核心 + 秘书核心）
- 四库管理 + 向量检索 + 差异化权重
- 嵌入 Linux（Docker/WSL2/Mock）
- Web 控制台（对话/日志/终端/四库/成本/Stats）
- 工具系统（32 工具 + MCP + 插件）
- 技能系统（自我改进 + 置信度 + 版本回滚）
- 上下文压缩（80% 阈值 + LLM 摘要）
- 临时工作目录 + 自动清理
- SSE 流式输出（心跳 + 断线续传）
- 任务统计 + 成本监控 + 账单校准
- 并行执行 + 工具自动重试
- IMA 腾讯笔记云端备份

---

## 一、项目概述

本项目复现了视频中描述的 **Nexus 双核 Agent 系统**，核心思路：

- **决策核心**：只做推理决策，不查资料、不调工具，保持"决策纯度"
- **秘书核心**：管理四库、预判检索、主动递达上下文，让决策核心始终看到高纯度信息
- **嵌入 Linux**：在 Agent 内部运行完整 Linux（WSL2），提供真实操作环境
- **向量检索**：用 embedding 做"标点定位"式检索，解决知识膨胀
- **三模式**：Work（编码助手）/ Chat（闲聊）/ Brainstorm（头脑风暴），差异化 temperature 和沉淀策略

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
│              向量检索 + 差异化权重 ("标点定位")             │
│                           ▼                              │
│              LLM 筛选 + 上下文整理                         │
└──────────────────────────┬──────────────────────────────┘
                           │  高纯度上下文 (递达)
                           ▼
┌─────────────────────────────────────────────────────────┐
│                 决策核心 Nexus (独立 API)                  │
│              只看任务 + 上下文 → 输出决策                   │
│         (Function Calling → 32工具 + MCP + 插件)           │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│         秘书沉淀 → 四库 + IMA 云端备份 (异步)               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              嵌入 Linux 系统 (WSL2 Ubuntu)                │
│         终端 / 文件系统 / 真实命令执行 / 安全沙箱            │
└─────────────────────────────────────────────────────────┘
```

---

## 三、与视频的技术对应

| 视频中的概念 | 本项目实现 | 文件位置 |
|---|---|---|
| 双核（Nixxes + 秘书） | `DecisionCore` + `SecretaryCore`，两个独立 OpenAI 客户端 | `core/` |
| 各自一条 API | `.env` 中 `DECISION_*` / `SECRETARY_*` 分开配置 | `.env` |
| 四库（工具/知识/经验/记忆） | `FourLibraries`，SQLite 持久化 + 向量索引 + 差异化权重 | `libraries/four_libraries.py` |
| 秘书预判、递达上下文 | `SecretaryCore.anticipate()`：检索 → LLM 筛选 → 递达 | `core/secretary_core.py` |
| 决策纯度高、不模糊 | 决策核心 system prompt 限定只做决策，上下文由秘书提供 | `core/decision_core.py` |
| 高维向量搜索 | `VectorStore`，支持 embedding API + TF-IDF 回退 | `libraries/vector_store.py` |
| 标点定位（非逐页翻） | 向量余弦相似度直接命中，不遍历 | `libraries/vector_store.py` |
| 嵌入 Linux 系统 | `LinuxEmbed`，WSL2 Ubuntu（D盘），真实命令执行 | `system/linux_embed.py` |
| 日志可视化 | `AgentLogger` + Web UI 日志面板 | `utils/logger.py` |
| 成本监控 | `CostTracker`，真实 token + 账单校准 | `utils/cost_tracker.py` |
| UI 界面 | Flask Web 控制台 + pywebview 桌面端 | `ui/`、`desktop.py` |
| 工具调用 | Function Calling，32 工具 + MCP + 插件系统 | `tools/`、`mcp/`、`plugins/` |
| 学习记忆升级 | 四库自动沉淀 + 经验反思 + 压缩合并 | `core/secretary_core.py` |
| 二进制层重构 | **未实现**，视频作者也尚未实现（见第九节） | — |

---

## 四、安装

### 4.1 环境要求

- Python 3.10+
- WSL2 + Ubuntu（Windows，推荐安装在 D 盘）
- 或 Docker Desktop（Linux/macOS）

### 4.2 安装步骤

```bash
# 1. 进入项目目录
cd D:\nexus_agent

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. 配置 .env（复制模板并填入你的 Key）
# 见下方 4.3 配置说明
```

### 4.3 .env 配置

```bash
# 决策核心
DECISION_BASE_URL=https://api.deepseek.com
DECISION_API_KEY=你的key
DECISION_MODEL=deepseek-v4-flash-vision-exp

# 秘书核心
SECRETARY_BASE_URL=https://api.deepseek.com
SECRETARY_API_KEY=你的key
SECRETARY_MODEL=deepseek-v4-flash

# Embedding 向量检索
USE_EMBEDDING=1
EMBED_BASE_URL=https://api.ephone.ai/v1
EMBED_API_KEY=你的key
EMBED_MODEL=text-embedding-3-small
EMBED_DIM=1536

# Linux 嵌入
LINUX_MODE=wsl
WSL_DISTRO=Ubuntu

# 搜索 API（三选一或全配）
TAVILY_API_KEY=你的key
SERPER_API_KEY=你的key
BAIDU_API_KEY=你的key

# IMA 腾讯笔记（经验库云端备份，可选）
IMA_SYNC_ENABLED=true
IMA_CLIENT_ID=你的client_id
IMA_API_KEY=你的api_key
IMA_SYNC_FOLDER=nexus

# 成本单价（元/百万token）
COST_DECISION_INPUT=1.5
COST_DECISION_OUTPUT=4.5
COST_SECRETARY_INPUT=1.0
COST_SECRETARY_OUTPUT=2.0
```

### 4.4 支持的 API 服务商

任何 OpenAI 兼容 API 都可以，修改 `.env` 中的 `base_url` 和 `model`：

| 服务商 | base_url | 推荐模型 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` / `deepseek-v4-flash` |
| 智谱 AI | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

> 建议：秘书用便宜的 flash 类模型，决策用好一点的模型。

---

## 五、使用方法

### 5.1 桌面端（推荐）

```bash
python desktop.py
```

pywebview 打包的桌面窗口，包含完整控制台。

### 5.2 Web UI

```bash
python run.py
```

浏览器访问 `http://127.0.0.1:7860`，界面包含：

- **对话区**：三模式切换（Work/Chat/Brainstorm），SSE 流式输出，秘书上下文展示
- **协作日志**：实时查看秘书、Nexus、Linux 的协作过程
- **成本监控**：今日 token 消耗、估算花费、账单校准
- **Linux 终端**：在嵌入的 WSL2 Ubuntu 中执行命令（安全沙箱）
- **四库管理**：浏览/搜索/删除工具库、知识库、经验库、记忆库

### 5.3 命令行模式

```bash
python run.py --cli
```

支持的特殊命令：
- `linux <命令>` —— 在嵌入 Linux 中执行命令
- `stats` —— 查看系统状态
- `quit` —— 退出

---

## 六、核心技术详解

### 6.1 双核架构为什么有效

传统单 Agent：
```
任务 → LLM(思考+查工具+查资料+决策) → 结果
         ↑ 上下文越来越臃肿 → 答案失真 → 超 token 限制
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

### 6.2 三模式差异化

| 模式 | temperature | 秘书触发 | 四库沉淀 | 适用场景 |
|---|---|---|---|---|
| Work | 0.7 | 40+字或复杂词自动，可手动强制 | 经验+工具+知识+记忆（全量） | 编码、技术任务 |
| Chat | 0.5 | 默认关，手动开则强制 | 只提取用户偏好 | 闲聊、问答 |
| Brainstorm | 0.95 | 默认关，手动开则强制 | 只沉淀创意模式 | 头脑风暴、创意发散 |

### 6.3 四库差异化检索权重

| 库 | Work 模式 | Chat 模式 | Brainstorm 模式 |
|---|---|---|---|
| 经验库 | top5 × 1.3 | top2 × 0.8 | top4 × 1.1 |
| 知识库 | top4 × 1.0 | top2 × 0.8 | top5 × 1.2 |
| 工具库 | top3 × 1.0 | top1 × 0.5 | top2 × 0.6 |
| 记忆库 | top2 × 0.8（仅偏好） | top3 × 1.3（仅偏好） | top1 × 0.5 |

记忆库检索时强制过滤 `meta.type=task_memory` 流水账，只检索 `user_preference`。

### 6.4 向量检索（"标点定位"）

| 方式 | 原理 | 复杂度 | 语义理解 |
|---|---|---|---|
| 一页一页翻 | 遍历所有文档，逐条关键词匹配 | O(n) | 差（只能匹配字面） |
| 标点定位（向量检索） | 文本 → embedding 向量 → 余弦相似度命中 | O(log n) | 好（理解语义） |

`VectorStore`：
- 优先调用 embedding API（`text-embedding-3-small`，1536 维）
- API 不可用时自动回退到 TF-IDF 余弦相似度
- 所有四库条目共享一个向量空间，检索时按库名 + 权重过滤

### 6.5 嵌入 Linux（WSL2）

- Ubuntu 安装在 D 盘，不占 C 盘
- 默认工作目录 `/mnt/d/nexus_agent`（项目目录）
- 真实 shell 命令执行，支持 cd 链、ll alias、cwd 保持
- **安全沙箱**：16 条危险命令黑名单 + ulimit 资源限制 + timeout（10s/30s/60s 三档）
- 前端 Linux 面板和 `linux_terminal` 工具双重安全检查

### 6.6 上下文压缩

- 对话历史达到上下文窗口 80% 时自动触发
- 秘书核心用 LLM 摘要历史对话，保留关键信息
- 压缩后继续对话，防止 token 溢出
- 经验库超过 30 条时自动压缩合并（LLM 提炼 → 保留 10 条精华）

### 6.7 IMA 云端备份

- 经验库新增条目后，异步同步到 IMA 腾讯笔记
- 存入指定笔记本（默认 `nexus`），Markdown 格式
- 单向只增不删（IMA API 无删除接口）
- 同步失败不影响主流程，打日志继续
- SQLite 仍是主存储，IMA 是云端备份

---

## 七、目录结构

```
D:\nexus_agent\
├── desktop.py              # 桌面端入口 (pywebview)
├── run.py                  # Web/CLI 入口
├── config.py               # 全局配置
├── .env                    # 密钥和参数 (不提交)
├── requirements.txt        # Python 依赖
├── core/                   # 双核核心
│   ├── dual_agent.py       # 总控: 秘书 → 决策 → 沉淀
│   ├── decision_core.py    # 决策核心 Nexus (三模式temperature)
│   └── secretary_core.py   # 秘书核心 (四库+预判+反思+IMA同步)
├── libraries/              # 四库 + 向量检索
│   ├── four_libraries.py   # 四库管理 (SQLite + 差异化权重)
│   └── vector_store.py     # 向量存储 (embedding/TF-IDF)
├── system/                 # 系统层
│   └── linux_embed.py      # 嵌入 Linux (WSL2/Docker/Mock)
├── tools/                  # 工具系统
│   ├── base.py             # BaseTool 基类
│   ├── safety.py           # 危险命令黑名单 + 资源限制
│   ├── web_search.py       # 联网搜索
│   ├── code_exec.py        # Python 代码执行
│   ├── linux_terminal.py   # Linux 命令执行
│   └── providers.yaml      # 工具注册配置
├── mcp/                    # MCP 协议接入
├── plugins/                # 插件系统
├── skills/                 # 技能系统 (自我改进)
├── integrations/           # 第三方集成
│   └── ima_client.py       # IMA 腾讯笔记客户端
├── ui/                     # Web 界面
│   ├── web_ui.py           # Flask 后端 API
│   └── templates/
│       └── index.html      # 前端控制台
├── utils/                  # 工具
│   ├── logger.py           # 结构化日志
│   └── cost_tracker.py     # 成本监控 + 账单校准
├── data/                   # 运行时数据 (自动生成)
│   └── nexus.db            # SQLite 四库数据库
└── temp_workspace/         # 临时工作目录 (自动清理)
```

---

## 八、完整 API 列表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 控制台主页 |
| GET | `/embed` | 精简嵌入页 |
| POST | `/api/chat` | 对话（SSE 流式） |
| POST | `/api/v1/ask` | 对话（简洁 API，供外部调用） |
| POST | `/api/history/clear` | 清空对话历史 |
| GET | `/api/logs` | 协作日志 |
| GET | `/api/cost` | 成本统计 |
| POST | `/api/cost/calibrate` | 账单校准 |
| GET | `/api/stats` | 系统状态 |
| GET | `/api/libraries` | 四库内容 |
| POST | `/api/libraries/delete` | 删除四库条目 |
| POST | `/api/knowledge/import` | 导入网页到知识库 |
| POST | `/api/linux` | 执行 Linux 命令 |
| GET | `/api/linux/info` | Linux 环境信息 |
| POST | `/api/mode` | 切换模式 (work/chat/brainstorm) |
| GET | `/api/embed/snippet` | 获取 iframe 嵌入代码 |

---

## 九、关于"二进制层重构"

视频中提到的终极目标——**用二进制代码在 Linux 底层重构 Nexus**——本项目**未实现**，原因：

1. 视频作者本人也说"还没做，不确定能不能成功"
2. 涉及内核模块开发、系统调用劫持、二进制插桩等底层技术
3. 可行性存疑：Linux 内核不适合跑 LLM 推理（算力/内存限制），更可能用 eBPF 做系统调用级监控

可能的探索路径：
- **eBPF**：内核中挂载钩子，监控/拦截系统调用
- **LD_PRELOAD**：用户态动态库注入，拦截 libc 调用
- **WASI / WebAssembly**：沙箱中运行 Agent 逻辑
- **Rust 内核模块**：Linux 6.1+ 支持 Rust

---

## 十、与视频原版的差距

| 功能 | 视频原版 | 本项目 |
|---|---|---|
| 双核 Agent | ✅ | ✅ |
| 四库管理 | ✅ | ✅（SQLite + 差异化权重） |
| 向量检索 | ✅ | ✅ |
| 独立 API | ✅ | ✅ |
| 嵌入 Linux | ✅ 桌面级（浏览器/文件管理器 GUI） | ⚠️ 命令行级（WSL2） |
| UI 界面 | ✅ Aily Blockly | ✅ Flask + pywebview |
| 远程嵌入 | ✅ | ✅（iframe + API） |
| 工具调用 | ✅ | ✅（32 工具 + MCP + Function Calling） |
| 学习记忆升级 | ✅ | ✅（四库自动沉淀 + 反思 + 压缩） |
| 三模式 | — | ✅（Work/Chat/Brainstorm） |
| 云端备份 | — | ✅（IMA 腾讯笔记） |
| 二进制层重构 | 🔄 规划中 | ❌ 未实现 |

---

## 十一、扩展方向

1. **精确代码编辑**：当前 code_exec 偏脚本执行，需增强文件级精确编辑
2. **代码符号索引**：建立项目代码的 AST 索引，提升编码助手能力
3. **并行工具调用**：当前工具串行执行，支持并行提升效率
4. **经验→能力转化**：经验库不只是检索，能自动生成可复用技能
5. **MCP 生态扩展**：接入更多 MCP 服务器（文件系统、数据库、浏览器）
6. **本地模型**：Ollama 跑本地模型，完全离线零费用
7. **安装包分发**：PyInstaller 打包，一键安装

---

## 十二、常见问题

**Q: 没有 API Key 能跑吗？**
A: 能启动界面，但对话功能需要 API Key。Linux 终端在 WSL2 模式下可独立使用。

**Q: 数据存在哪里？**
A: 四库在 `data/nexus.db`（SQLite），删除即可重置。对话历史在 `data/conversation_history.json`。

**Q: 怎么换模型？**
A: 修改 `.env` 中的 `DECISION_MODEL` 和 `SECRETARY_MODEL`，支持任何 OpenAI 兼容 API。

**Q: WSL2 必须装吗？**
A: 不装也能跑，Linux 会降级到 Mock 模式（仅演示基本命令）。推荐装 WSL2 获得真实环境。

**Q: IMA 同步会丢数据吗？**
A: 不会。SQLite 是主存储，IMA 只是云端备份，同步失败不影响本地数据。

---

*本项目仅用于技术学习和研究。*

---

## 联系与交流

📧 邮箱：lhq242408@163.com

欢迎学习交流，共同探讨 Agent 技术！
