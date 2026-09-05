

## 一、功能说明


- 双核架构（决策核心 + 秘书核心）
- 四库管理 + 向量检索 + 差异化权重
- 嵌入 Linux（Docker/WSL2/Mock）
- Web 控制台（对话/日志/终端/四库/成本/Stats）
- 工具系统（35 工具 + MCP + 插件）
- 技能系统（自我改进 + 置信度 + 版本回滚）
- 上下文压缩（80% 阈值 + 工具结果截断 + LLM 摘要）
- 失败拦截（检测到工具失败强制修复，禁止虚假成功）
- 工具效率优化（禁止无意义探索/诊断内联/新文件不先读）
- code_exec 超时杀进程树（防 Playwright 子进程挂起）
- 临时工作目录 + 任务结束自动清理
- SSE 流式输出（心跳 + 断线续传 + 中途输出拦截）
- 任务统计 + 成本监控 + 账单校准
- 并行工具调用 + 依赖检查 + 错误自修复闭环
- 经验→技能自动转化
- IMA 腾讯笔记云端备份（追加模式滚动笔记）
- 多工作空间隔离（每个项目独立对话/四库/代码索引/MCP）
- 代码符号索引（tree-sitter + AST，定义跳转/引用追踪/项目大纲，增量更新）
- 精确代码编辑（code_edit_symbol 按函数/类名替换，AST 定位 + 引用影响分析）
- 编码闭环（跑→错→修→再跑，结构化错误解析 + 自动定位 + 前端可视化）
- code_exec 写文件强制拦截（引导用 file_write，防止反复试写）
- code_search 优先走 AST 索引（区分定义和引用，比 grep 精确）
- 前端工具调用可视化（运行中/成功/失败/修复状态 + 错误卡片）
- 27 个单元测试（编码闭环 + AST 索引 + 工具管理 + MCP）
- MCP 超时保护 + 健康检查 + 自动重连
- 任务成功率基于最终结果判定（中间工具失败不影响）
- 自定义弹窗（删除确认/文件夹目录树浏览）
- 流式输出悬浮球 + 秘书开关位置优化
- **语音交互**（Web Speech API 语音输入 + edge-tts 语音播报，5种中文语音）
- **唤醒词模式**（说"豆包"唤醒，持续监听+自动重连，唤醒后指令自动发送）
- **系统控制**（打开应用/音量控制/锁屏/截图/系统信息/安全命令执行，危险操作拦截）
- **浏览器自动化**（Playwright 连接系统 Chrome，打开网页/点击/输入/抓数据/截图，窗口可见可交互）
- **KV cache 稳定性优化**（移除动态规则注入/秘书LLM二次整理/截断长度数字，提升前缀命中率降低费用）
- RRF 混合检索（语义+关键词双路融合，k=60）
- 项目档案沉淀（自动分析项目结构，work模式自动召回注入上下文）
- 模型分级路由（work用决策模型，chat用轻量模型）

---

## 二、项目概述


- **决策核心**：只做推理决策，不查资料、不调工具，保持"决策纯度"
- **秘书核心**：管理四库、预判检索、主动递达上下文，让决策核心始终看到高纯度信息
- **嵌入 Linux**：在 Agent 内部运行完整 Linux（WSL2），提供真实操作环境
- **向量检索**：用 embedding 做"标点定位"式检索，解决知识膨胀
- **三模式**：Work（编码助手）/ Chat（闲聊）/ Brainstorm（头脑风暴），差异化 temperature 和沉淀策略

---

## 三、架构总览

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
│            结构化检索结果直接递达 (省LLM调用)                  │
└──────────────────────────┬──────────────────────────────┘
                           │  高纯度上下文 (递达)
                           ▼
┌─────────────────────────────────────────────────────────┐
│                 决策核心 Nexus (独立 API)                  │
│              只看任务 + 上下文 → 输出决策                   │
│         (Function Calling → 35工具 + MCP + 插件)           │
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
- **工作空间**：多项目隔离，顶部下拉切换，每个空间独立数据

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

### 6.6 上下文压缩与性能优化

**三层防护防止 token 爆炸：**

1. **工具结果截断**：单条工具结果超过 2000 字符自动保留头尾截断，防止大段错误输出（如 Playwright 堆栈）撑爆上下文
2. **实时 token 计数**：每次 API 调用前估算 messages 总 token
3. **超阈值自动压缩**：达到上下文窗口 60% 时，秘书核心用 LLM 摘要早期对话，保留最近 2 轮工具调用

**失败拦截机制：**
- 最终回答前检查最后一个工具结果
- 如果包含 `EXIT: 1`/错误/失败/超时，注入【失败拦截】提醒
- 强制模型：分析错误 → code_edit 修复 → code_exec 验证，禁止直接报"已完成"

**工具效率约束：**
- 系统提示词明确禁止：无意义 file_list 探索、读旧 demo 代码当参考、写诊断脚本到 temp/
- 要求：创建新文件直接 file_write，诊断用 code_exec 内联，一次 file_write + 一次 code_exec 验证 = 2次调用

**code_exec 超时保护：**
- `subprocess.Popen` + `communicate(timeout=)`，默认 15 秒最大 120 秒
- 超时时 `taskkill /T /F /PID` 杀整个进程树（Playwright 的 Chromium 子进程也会死）
- 防止浏览器挂起导致整个任务卡死

**经验库压缩：**
- 超过 30 条时自动 LLM 提炼合并，保留 10 条精华

### 6.7 编码闭环（跑→错→修→再跑）

**工作流：**
```
file_write 写代码 → code_exec 运行 → 结构化错误解析 → code_edit 精准修复 → code_exec 再跑验证 → 循环直到通过
```

**结构化错误解析：**
- `parse_python_error()` 解析 traceback，提取错误类型/文件/行号/函数
- `format_error_structured()` 格式化输出，含报错行前后 3 行上下文（`>>>` 标记报错行）
- Agent 直接根据文件:行号定位，不需要瞎猜

**强制约束：**
- 修复后必须再跑一次验证，不能改完就说"应该好了"
- 同一错误连续 2 次修不好，强制 file_read 读报错行前后 20 行分析
- 最多重试 5 次，仍失败如实告知用户
- code_exec 检测到写文件操作直接拦截，引导用 file_write（防止 8 次 code_exec 反复试写）

**前端可视化：**
- 工具调用状态实时显示：运行中（spinner）/ 成功（绿色✓）/ 失败（红色✗）/ 修复中（🔧）
- code_exec 报错时显示红色错误卡片（错误类型+位置+代码上下文）
- 最终回答保留完整工具调用时间线

### 6.8 AST 代码索引（深度集成）

**索引能力：**
- Python 用内置 `ast` 模块，JS/TS/Go/Java/Rust 用 tree-sitter
- 并发构建（ThreadPoolExecutor，4 worker），按项目路径分库（MD5 hash）
- 存储：符号定义表（name/type/file/line/end_line/signature/docstring）+ 引用表（symbol_name/file/line/context）

**增量更新：**
- `file_write` / `code_edit` 修改文件后自动调用 `index_file()` 增量更新
- 删除该文件旧符号+引用，重新解析插入，索引实时准确
- 不需要手动重建整个项目索引

**工具链：**
| 工具 | 作用 |
|---|---|
| `code_find_def` | 按函数/类名精确定义位置（文件+行号+签名+docstring） |
| `code_find_refs` | 查找所有调用处（评估改动影响范围） |
| `code_outline` | 文件结构大纲（快速了解陌生文件） |
| `code_edit_symbol` | 按符号名直接替换实现（内部 AST 定位，比 search_replace 可靠） |
| `code_search` | 符号查询优先走 AST 索引，返回 [AST 定义] 和 [AST 引用] 两类结果 |

**提示词强制：**
- 改任何函数/类前必须先 `code_find_def` + `code_find_refs`，禁止关键词瞎搜
- 陌生文件必须先 `code_outline` 看大纲
- 整个函数/类修改必须用 `code_edit_symbol`

### 6.9 IMA 云端备份（追加模式）

- 经验库新增条目后，异步同步到 IMA 腾讯笔记
- **追加模式**：所有经验追加到同一个滚动笔记「Nexus经验日志」，不再每次创建新笔记
- 首次同步自动创建笔记，note_id 缓存到 `data/ima_rolling_note.json`
- 每条经验格式：`## [时间] 经验#ID → 任务摘要 → 正文 → ---`
- 存入指定笔记本（默认 `nexus`），Markdown 格式
- 追加失败兜底：退化为创建独立笔记，不丢数据
- 单向只增不删，SQLite 仍是主存储，IMA 是云端备份

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
│   ├── decision_core.py    # 决策核心 Nexus (三模式temperature + 失败拦截 + 并行工具)
│   ├── secretary_core.py   # 秘书核心 (四库+预判+反思+IMA同步)
│   └── context_manager.py  # 上下文压缩 (工具截断+60%阈值+LLM摘要)
├── libraries/              # 四库 + 向量检索
│   ├── four_libraries.py   # 四库管理 (SQLite + 差异化权重)
│   └── vector_store.py     # 向量存储 (embedding/TF-IDF)
├── system/                 # 系统层
│   └── linux_embed.py      # 嵌入 Linux (WSL2/Docker/Mock)
├── tools/                  # 工具系统
│   ├── base_tool.py        # BaseTool 基类
│   ├── code_exec.py        # Python代码执行 (超时杀进程树+写文件拦截)
│   ├── code_edit.py        # 精确代码编辑 (search_replace+diff+备份回滚)
│   ├── code_edit_symbol.py # 按符号名编辑 (AST定位+引用影响分析)
│   ├── code_ast_tools.py   # AST工具 (定义跳转/引用追踪/文件大纲)
│   ├── code_index.py       # 代码符号索引 (tree-sitter+AST+增量更新)
│   ├── code_search.py      # 代码搜索 (AST索引优先+grep回退)
│   ├── file_ops.py         # 文件读写 (写入后自动更新索引)
│   ├── linux_terminal.py   # Linux命令执行 (黑名单+ulimit+timeout)
│   ├── cleanup.py          # 临时文件清理
│   ├── project_analyze.py  # 项目结构分析
│   ├── system_control.py   # 系统控制 (打开应用/音量/锁屏/截图/命令)
│   ├── browser_automation.py # 浏览器自动化 (Playwright, 打开/点击/输入/抓数据)
│   └── providers.yaml      # 工具注册配置
├── mcp/                    # MCP 协议接入
├── plugins/                # 插件系统
├── skills/                 # 技能系统 (自我改进, YAML格式)
├── integrations/           # 第三方集成
│   └── ima_client.py       # IMA 腾讯笔记客户端 (追加模式)
├── ui/                     # Web 界面
│   ├── web_ui.py           # Flask 后端 API
│   └── templates/
│       └── index.html      # 前端控制台
├── utils/                  # 工具
│   ├── logger.py           # 结构化日志
│   ├── cost_tracker.py     # 成本监控 + 账单校准
│   ├── temp_workspace.py   # 临时工作目录管理
│   └── tts.py              # 语音合成 (edge-tts, 5种中文语音)
├── tests/                  # 单元测试 (27个)
│   ├── test_code_edit_loop.py   # 编码闭环+精确编辑 (17个)
│   ├── test_ast_index.py        # AST索引+增量更新 (10个)
│   ├── test_tool_manager.py     # 工具管理
│   ├── test_mcp_client.py       # MCP客户端
│   └── test_task_stats.py       # 任务统计
├── data/                   # 运行时数据 (自动生成)
│   ├── nexus.db            # SQLite 四库数据库
│   └── conversation_history.json  # 对话历史 (按模式分)
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
| POST | `/api/tts` | 文本转语音 (返回 mp3) |
| GET | `/api/tts/voices` | 可用语音列表 |

---

## 九、关于"二进制层重构"


可能的探索路径：
- **eBPF**：内核中挂载钩子，监控/拦截系统调用
- **LD_PRELOAD**：用户态动态库注入，拦截 libc 调用
- **WASI / WebAssembly**：沙箱中运行 Agent 逻辑
- **Rust 内核模块**：Linux 6.1+ 支持 Rust

---


## 十一、扩展方向

1. ~~**精确代码编辑**~~ ✅ 已实现（code_edit_symbol 按符号名替换 + AST 定位 + diff 预览）
2. ~~**代码符号索引**~~ ✅ 已实现（tree-sitter + AST，增量更新，定义/引用/大纲）
3. ~~**并行工具调用**~~ ✅ 已实现（ThreadPoolExecutor + 依赖检查 + 结果按序组装）
4. ~~**经验→能力转化**~~ ✅ 已实现（复杂任务后自动创建技能 YAML）
5. ~~**编码闭环深化**~~ ✅ 已实现（测试自动生成 + lint 集成 + 结构化错误解析）
6. **沙箱隔离**：当前 code_exec 在 Windows 子进程，后续换 Docker/WSL2 容器隔离
6. **MCP 生态扩展**：接入更多 MCP 服务器（文件系统、数据库、浏览器）
7. **本地模型**：Ollama 跑本地模型，完全离线零费用
8. **安装包分发**：PyInstaller 打包，一键安装
9. **桌面级 Linux GUI**：当前为命令行级 WSL2，原版为浏览器/文件管理器 GUI
10. ~~**语音交互**~~ ✅ 已实现（Web Speech API + edge-tts + 唤醒词"豆包"）
11. ~~**设备控制**~~ ✅ 已实现（系统控制6工具 + 浏览器自动化6工具）
12. **日程管理**：日历集成 + 语音创建提醒 + 定时任务 + 到期播报
13. **本地唤醒词引擎**：升级 openWakeWord，离线高精度唤醒，替代 Web Speech API

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
