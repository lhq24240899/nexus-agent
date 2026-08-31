"""
秘书核心 —— 对应视频中的"秘书"
职责:
1. 管理四库 (工具/知识/经验/记忆)
2. 预判决策核心需要什么, 检索并整理上下文 ("递到面前")
3. 任务完成后沉淀经验 + 反思复盘
"""
from openai import OpenAI
from config import SECRETARY_CONFIG
from libraries.four_libraries import FourLibraries
from utils.logger import logger
from utils.cost_tracker import cost_tracker


class SecretaryCore:
    """秘书核心: 四库管理 + 预判检索 + 上下文整理 + 经验反思"""

    def __init__(self):
        api_key = SECRETARY_CONFIG["api_key"] or "sk-not-configured"
        self.client = OpenAI(
            base_url=SECRETARY_CONFIG["base_url"],
            api_key=api_key,
            timeout=60.0,
        )
        self.model = SECRETARY_CONFIG["model"]
        self.temperature = SECRETARY_CONFIG["temperature"]
        self.configured = bool(SECRETARY_CONFIG["api_key"])
        self.libs = FourLibraries()
        self.tool_manager = None  # 由 dual_agent 注入

    SYSTEM_PROMPT = """你是 Nexus 双核 Agent 的秘书。

【你的职责】
1. 根据用户任务和历史对话, 从四库检索相关信息
2. 筛选出对决策最有用的内容, 用简洁语言整理成上下文摘要
3. 只基于检索结果和历史对话, 不要编造
4. 如果检索结果无关, 直接说"无相关上下文"

【输出格式】
## 相关上下文
(整理后的关键信息, 按重要性排序)

## 检索来源
(来自哪个库, 匹配度如何)
"""

    REFLECT_PROMPT = """你是 Nexus 双核 Agent 的秘书, 负责经验复盘。

【你的任务】
根据刚完成的任务、决策结果和上下文, 进行反思:
1. 这次决策哪些地方做得好?
2. 哪些地方可以改进?
3. 提炼一条可复用的经验模式, 存入经验库

要求: 简洁, 具体, 可操作。不要泛泛而谈。
如果任务很简单没什么可反思的, 就说"无特别需要反思的内容"。
"""

    def anticipate(self, task: str, history_text: str = "") -> str:
        """
        核心功能: 预判决策核心需要什么
        1. 从四库向量检索
        2. 结合历史对话, 用 LLM 筛选整理
        """
        logger.log("secretary", "预判检索开始", f"任务: {task[:50]}")

        # Step 1: 四库向量检索 ("标点定位")
        results = self.libs.search_all(task, top_k=3)
        raw_parts = []
        for lib_name, items in results.items():
            if items:
                raw_parts.append(f"【{lib_name}】")
                for it in items:
                    raw_parts.append(f"  [匹配度 {it.get('score', 0)}] {it['content']}")
        raw_context = "\n".join(raw_parts) if raw_parts else "(四库无相关内容)"

        total_found = sum(len(v) for v in results.values())
        logger.log("secretary", "向量检索完成",
                   f"命中 {total_found} 条, 模式: {self.libs.vector_store.mode}")

        # Step 2: LLM 筛选整理 (含历史对话)
        history_section = f"\n\n【历史对话】\n{history_text}" if history_text else ""
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"【用户任务】\n{task}"
                    f"{history_section}\n\n"
                    f"【检索到的原始信息】\n{raw_context}\n\n"
                    f"请整理决策核心需要的上下文:"
                )},
            ],
        )
        filtered = resp.choices[0].message.content.strip()
        usage = resp.usage
        cost_tracker.record(
            model=self.model,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            task=f"秘书整理: {task[:30]}",
        )
        logger.log("secretary", "上下文整理完成", f"递达 {len(filtered)} 字")

        # 工具筛选: 从全部工具中选出任务相关的子集, 减少决策核心的 token 消耗
        selected_tools = self._select_tools(task)
        logger.log("secretary", "工具筛选完成",
                   f"{len(selected_tools) if selected_tools else '全部'} 个工具")

        return filtered, selected_tools

    def _select_tools(self, task: str) -> list[str] | None:
        """
        工具筛选: 用关键词匹配快速选出相关工具, 减少上下文 token
        返回工具名列表, None 表示不筛选(全部传入)
        """
        if not self.tool_manager:
            return None
        all_tools = self.tool_manager.list_tools()
        task_lower = task.lower()

        # 简单任务: 只传最少工具
        simple_keywords = ["你好", "hello", "谢谢", "1+1", "是什么", "定义", "解释",
                          "what is", "define", "hi"]
        if any(kw in task_lower for kw in simple_keywords):
            return ["current_time"]  # 简单问答几乎不需要工具

        # 关键词 -> 工具映射
        tool_keywords = {
            "web_search": ["搜索", "搜一下", "最新", "新闻", "查一下", "网上", "search", "最新消息", "今天"],
            "news_search": ["新闻", "头条", "热点", "今日要闻"],
            "code_exec": ["代码", "python", "脚本", "运行", "执行", "算", "计算", "写个", "print", "code", "run"],
            "linux_terminal": ["linux", "shell", "命令", "终端", "bash", "grep", "find", "ls", "wsl"],
            "file_read": ["读取", "读文件", "看一下", "打开", "内容", "read"],
            "file_write": ["写入", "写文件", "保存", "创建文件", "write"],
            "file_list": ["目录", "列出", "有什么文件", "ls", "结构"],
            "code_search": ["搜索代码", "找函数", "grep", "代码搜索", "查找"],
            "code_edit": ["修改", "编辑", "改代码", "替换", "删除", "插入", "edit"],
            "project_analyze": ["项目", "分析", "结构", "技术栈", "依赖"],
            "git": ["git", "提交", "分支", "diff", "commit", "log", "状态"],
            "http_request": ["请求", "api", "接口", "curl", "http", "抓取网页"],
            "parallel_execute": ["同时", "并行", "一起", "分别", "多个任务"],
            "use_skill": ["技能", "工作流", "skill"],
        }

        selected = set()
        for tool_name, keywords in tool_keywords.items():
            if any(kw in task_lower for kw in keywords):
                selected.add(tool_name)

        # 编码任务默认带上核心编码工具
        coding_keywords = ["代码", "python", "脚本", "修改", "编辑", "函数", "bug", "报错",
                          "重构", "功能", "实现", "项目", "文件", "code", "debug"]
        if any(kw in task_lower for kw in coding_keywords):
            selected.update(["file_read", "code_search", "code_edit", "code_exec",
                           "project_analyze", "file_list", "file_write"])

        # MCP 文件系统工具: 文件相关任务时带上
        if any(kw in task_lower for kw in ["文件", "目录", "项目", "代码", "读取", "修改"]):
            for t in all_tools:
                if t["name"].startswith("mcp_") and ("file" in t["name"] or "directory" in t["name"]):
                    selected.add(t["name"])

        # 至少保留 3 个工具, 避免筛选过严
        if len(selected) < 3:
            selected.update(["code_exec", "file_read", "current_time"])

        # 最多 12 个工具, 避免筛选失效
        result = list(selected)[:12]
        return result if result else None

    def reflect(self, task: str, result: str, context: str) -> str:
        """
        经验反思: 任务完成后复盘, 提炼可复用经验
        """
        if not self.configured:
            return "(未配置API, 跳过反思)"
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": self.REFLECT_PROMPT},
                    {"role": "user", "content": (
                        f"【任务】{task[:200]}\n\n"
                        f"【秘书递达的上下文】{context[:300]}\n\n"
                        f"【决策结果】{result[:400]}\n\n"
                        f"请进行反思复盘:"
                    )},
                ],
            )
            reflection = resp.choices[0].message.content.strip()
            usage = resp.usage
            cost_tracker.record(
                model=self.model,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                task="秘书反思",
            )
            # 把有价值的反思存入经验库
            if reflection and "无特别需要反思" not in reflection:
                self.libs.experience.add(
                    f"[反思] 任务: {task[:60]}\n经验: {reflection[:300]}",
                    meta={"type": "reflection"},
                )
                logger.log("secretary", "反思完成", "已存入经验库")
            else:
                logger.log("secretary", "反思完成", "无特别需要反思的内容")
            return reflection
        except Exception as e:
            logger.log("secretary", "反思失败", str(e))
            return f"(反思失败: {e})"

    # 经验库压缩阈值: 超过则自动提炼合并
    EXPERIENCE_COMPACT_THRESHOLD = 30
    # 压缩后保留的精炼经验条数
    EXPERIENCE_COMPACT_KEEP = 10

    COMPACT_PROMPT = """你是 Nexus 双核 Agent 的经验管理员。

【任务】
将以下经验库中的所有条目进行去重、合并、提炼, 生成 {keep} 条精炼经验。

【规则】
1. 相似经验合并为一条, 去掉重复内容
2. 去掉无价值的流水账(如单纯记录"做了什么任务")
3. 保留真正可复用的模式、技巧、教训
4. 每条经验简洁具体, 不超过80字
5. 只输出精炼后的经验列表, 每条一行, 不要编号, 不要解释

【原始经验】
{raw_experiences}
"""

    def compact_experience(self) -> dict:
        """经验库压缩: 超过阈值时调用 LLM 提炼合并"""
        count = len(self.libs.experience)
        if count < self.EXPERIENCE_COMPACT_THRESHOLD:
            return {"ok": False, "reason": f"经验库仅 {count} 条, 未达阈值 {self.EXPERIENCE_COMPACT_THRESHOLD}"}

        if not self.configured:
            return {"ok": False, "reason": "未配置 API, 无法压缩"}

        try:
            raw = "\n".join(
                f"[{i+1}] {item['content'][:200]}"
                for i, item in enumerate(self.libs.experience.all())
            )
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                messages=[{
                    "role": "user",
                    "content": self.COMPACT_PROMPT.format(
                        keep=self.EXPERIENCE_COMPACT_KEEP,
                        raw_experiences=raw[:6000],
                    ),
                }],
            )
            refined = resp.choices[0].message.content.strip()
            usage = resp.usage
            cost_tracker.record(
                model=self.model,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                task="经验库压缩",
            )

            # 清空旧经验, 写入精炼后的
            old_count = len(self.libs.experience)
            self.libs.experience.clear()

            added = 0
            for line in refined.splitlines():
                line = line.strip().lstrip("-•*0123456789.) ")
                if line and len(line) > 5:
                    self.libs.experience.add(line, meta={"type": "compacted"})
                    added += 1

            logger.log("secretary", "经验库压缩完成",
                       f"{old_count} → {added} 条")
            return {"ok": True, "old": old_count, "new": added}
        except Exception as e:
            logger.log("secretary", "经验库压缩失败", str(e))
            return {"ok": False, "error": str(e)}

    def clean_memory(self) -> dict:
        """记忆库清理: 删掉'完成过任务'类流水账, 只保留用户事实/偏好"""
        before = len(self.libs.memory)
        kept = []
        removed = 0
        for item in self.libs.memory.all():
            content = item.get("content", "")
            # 过滤掉任务流水账
            if content.startswith("完成过任务") or "task_memory" in str(item.get("meta", {})):
                self.libs.memory.delete(item["id"])
                removed += 1
            else:
                kept.append(item)
        logger.log("secretary", "记忆库清理", f"{before} → {len(kept)} 条 (删 {removed} 条流水账)")
        return {"ok": True, "old": before, "new": len(kept), "removed": removed}

    def record_result(self, task: str, result: str, tools_used: list = None,
                      success: bool = True):
        """任务完成后沉淀到经验库 (结构化: 成功/失败/工具/可复用模式)"""
        tools_str = ", ".join(tools_used) if tools_used else "无"
        status = "成功" if success else "失败"
        self.libs.experience.add(
            f"[{status}] 任务: {task[:80]}\n"
            f"使用工具: {tools_str}\n"
            f"结果摘要: {result[:200]}",
            meta={"type": "task_result", "success": success,
                  "tools_used": tools_used or []},
        )
        logger.log("secretary", "沉淀完成",
                   f"经验库+1 ({status}, 工具: {tools_str})")
        # 自动检查是否需要压缩
        if len(self.libs.experience) >= self.EXPERIENCE_COMPACT_THRESHOLD:
            logger.log("secretary", "触发自动压缩",
                       f"经验库 {len(self.libs.experience)} 条达阈值")
            self.compact_experience()

    def seed_demo_data(self):
        """预置实用示例数据"""
        # ========== 知识库：真正有用的技术知识 ==========
        knowledge_items = [
            # API 与成本
            "DeepSeek API 配置: base_url=https://api.deepseek.com/v1, 模型 deepseek-chat(V3) / deepseek-reasoner(R1)。"
            "输入 2元/百万token, 输出 8元/百万token, 缓存命中输入 0.2元/百万token。"
            "上下文 64K, 最大输出 8K。兼容 OpenAI SDK, 只需改 base_url 和 api_key。",
            "低成本策略: 简单问答用 flash 级模型, 复杂推理才用 pro。"
            "开启 prompt 缓存(系统提示词不变时自动命中), 输入成本降 90%。"
            "秘书核心用便宜模型做检索预判, 决策核心用好模型做最终回答, 总成本降 50%+。",
            # WSL2
            "WSL2 常用操作: wsl --list --verbose 查看发行版, wsl --shutdown 重启, "
            "Windows 路径在 WSL 中为 /mnt/c/..., WSL 文件在 Windows 中为 \\\\wsl$\\Ubuntu\\。"
            "端口自动转发, localhost 可直接访问 WSL 内服务。",
            # 向量检索
            "向量检索调优: chunk 大小 200-500 字效果最好, 重叠 50 字防截断。"
            "embedding 用 text-embedding-3-small(性价比高) 或 bge-large-zh(中文好)。"
            "Top-K 取 3-5 条, 太多会引入噪音, 太少会遗漏关键信息。"
            "混合检索=向量检索+关键词检索, 用 RRF 融合, 准确率比单路高 20%。",
            # Agent 调试
            "Agent 调试流程: 1.看日志面板确认每步执行了什么 2.看成本面板确认 token 消耗 "
            "3.简单问题走快速通道(不调秘书) 4.复杂问题手动开启秘书模式对比效果 "
            "5.工具调用失败时检查 API key 和网络。",
            # Function Calling
            "Function Calling 原理: 把工具描述成 JSON Schema 传给 LLM, "
            "LLM 返回要调用的函数名和参数, 程序执行后把结果回传给 LLM 生成最终回答。"
            "关键: 工具描述要写清楚用途和参数含义, 描述越准调用越准。",
        ]
        for item in knowledge_items:
            self.libs.knowledge.add(item)

        # ========== 工具库：每个工具的使用技巧 ==========
        tools_items = [
            "web_search 使用技巧: 关键词要具体(不要搜'怎么学Python', 搜'Python 装饰器 用法 示例')。"
            "返回结果包含标题、摘要、链接, 决策核心会引用摘要内容。"
            "适用: 实时信息、新闻、技术文档查询。不适用: 纯数学计算、代码逻辑推理。",
            "code_exec 使用技巧: 在隔离环境执行 Python 代码, 支持 pip 安装包。"
            "可以做: 数据处理、算法验证、API 调用测试、文件操作。"
            "注意: 无状态(每次执行环境重置), 长时间运行会超时(10秒), 不要写死循环。"
            "打印结果用 print(), 返回 stdout 前 2000 字符。",
            "linux_terminal 使用技巧: 在 WSL2 Ubuntu 中真实执行 shell 命令。"
            "可以做: 文件管理(grep/find/awk)、网络测试(curl/ping)、系统信息、Git 操作。"
            "注意: 命令超时 10 秒, 有黑名单(rm -rf /, mkfs, dd 等危险命令被拦截)。"
            "Windows 文件在 /mnt/c/ 和 /mnt/d/ 下可直接访问。",
            "current_time: 获取当前时间和日期, 用于需要时间上下文的任务"
            "(如'今天周几'、'计算3天后是几号')。简单工具, 调用成本极低。",
        ]
        for item in tools_items:
            self.libs.tools.add(item)

        # ========== 经验库：可复用的做事模式 ==========
        experience_items = [
            "复杂任务处理模式(PDCA): Plan(拆解子任务) → Do(逐个执行, 调用工具) → "
            "Check(验证结果) → Act(调整方案)。任务越复杂, 拆解越细。"
            "例: '做一个网站' → 拆成 需求分析→技术选型→搭建框架→实现功能→测试部署。",
            "代码调试经验: 1.先复现问题 2.看错误信息(最后一行是关键) "
            "3.用 code_exec 写最小测试用例验证 4.修完后回归测试 "
            "5.把踩坑记录进经验库, 下次遇到直接用。",
            "检索预判经验: 用户问'是什么/为什么/定义' → 不需要联网, 直接答。"
            "用户问'最新/今天/最近/价格' → 必须 web_search。"
            "用户问'帮我算/写代码/执行' → 直接调对应工具, 不检索。"
            "模糊问题('这个怎么弄') → 先检索背景再回答。",
            "成本控制经验: 连续对话超过6轮时, 秘书会自动总结旧对话压缩上下文。"
            "简单问题(1+1、定义类)走快速通道, 不消耗秘书 token。"
            "工具结果超过 2000 字时自动截断, 避免撑爆上下文。",
        ]
        for item in experience_items:
            self.libs.experience.add(item)

        # ========== 记忆库：用户事实 + 偏好 ==========
        memory_items = [
            "用户环境: Windows 系统, 已安装 WSL2 (Ubuntu), 项目目录 D:\\nexus_agent。"
            "Python 开发, 使用 PyCharm。C 盘空间紧张, 大文件放 D 盘。",
            "用户技术背景: 正在学习 AI Agent 开发, 关注 LangChain、RAG、Function Calling。"
            "喜欢从底层原理理解技术, 会追问实现细节。",
            "用户偏好: 中文交流, 喜欢直接给可运行代码而非纯理论。关注成本, 倾向便宜快速的模型。"
            "回复要简洁先给结论, 复杂概念用极简小例子解释。UI 参考豆包风格。",
        ]
        for item in memory_items:
            self.libs.memory.add(item)

        total = (len(self.libs.knowledge) + len(self.libs.tools) +
                 len(self.libs.experience) + len(self.libs.memory))
        logger.log("system", "预置示例数据", f"四库已填充 {total} 条实用内容")
