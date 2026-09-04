"""
秘书核心 —— 对应视频中的"秘书"
职责:
1. 管理四库 (工具/知识/经验/记忆)
2. 预判决策核心需要什么, 检索并整理上下文 ("递到面前")
3. 任务完成后沉淀经验 + 反思复盘
"""
import threading
import time
from openai import OpenAI
from config import SECRETARY_CONFIG, DATA_DIR
from libraries.four_libraries import FourLibraries
from utils.logger import logger
from utils.cost_tracker import cost_tracker

try:
    from integrations.ima_client import get_ima_client
    _IMA_AVAILABLE = True
except ImportError:
    _IMA_AVAILABLE = False


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
        # 经验压缩异步状态
        self._compact_lock = threading.Lock()
        self._compact_running = False
        self._last_compact_result: dict | None = None
        self.last_test_result = None  # 最近一次自动测试结果, 供前端轮询

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

    FAIL_REFLECT_PROMPT = """你是 Nexus 双核 Agent 的秘书, 负责失败任务复盘。

【你的任务】
这个任务失败了, 请深入分析:
1. 失败的根本原因是什么? (工具误用? 理解偏差? 环境问题? 能力不足?)
2. 哪一步开始走错了?
3. 如果重来一次, 正确的做法是什么?
4. 提炼一条可复用的教训, 避免下次犯同样的错误

【输出格式】
## 失败原因
(根本原因, 不要表面现象)

## 关键错误步骤
(哪一步开始错的, 为什么)

## 正确做法
(如果重来一次应该怎么做)

## 可复用教训
(一句话, 下次遇到类似情况时记住)
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

    def anticipate(self, task: str, history_text: str = "", mode: str = "work") -> str:
        """
        核心功能: 预判决策核心需要什么
        1. 从四库向量检索 (按模式差异化权重)
        2. 结合历史对话, 用 LLM 筛选整理
        """
        logger.log("secretary", "预判检索开始", f"任务: {task[:50]}, 模式: {mode}")

        # Step 1: 四库向量检索 ("标点定位"), 按模式差异化 top_k 和权重
        results = self.libs.search_all(task, mode=mode)
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

        # 硬规则: 检测到修复/调试/顽固问题类任务, 自动注入自我验证工作流
        debug_keywords = ['修复', '报错', '错误', 'bug', '没修好', '还是不行', '不生效',
                          '没变化', '反复修改', '调试', 'debug', '修不好', '异常']
        if any(kw in task.lower() for kw in debug_keywords):
            raw_context += (
                "\n\n【秘书硬规则 —— 自我验证工作流】"
                "\n检测到这是一个修复/调试类任务, 决策核心必须遵守:"
                "\n1. 改完代码自己启动验证, 不要让用户当测试员"
                "\n2. 同一问题修改2次未生效, 停止猜测, 用curl/日志验证假设, 分层排查根因"
                "\n3. UI不生效优先怀疑缓存(WebView2/浏览器), 用时间戳URL解决"
                "\n4. 检查运行时报错(函数是否存在/API可达), 验证关联功能"
                "\n5. 用完清理临时文件, git diff确认改动"
            )
            logger.log("secretary", "硬规则触发", "修复类任务, 已注入自我验证工作流")

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

        # 四库主动注入: 把高相关沉淀以结构化格式追加到上下文, 确保决策核心能看到
        library_injection = self._build_library_injection(results, task)
        if library_injection:
            filtered = filtered + "\n\n==========\n【四库沉淀 — 自动注入】\n" + library_injection
            logger.log("secretary", "四库注入完成", f"追加 {len(library_injection)} 字")

        # 工具筛选: 从全部工具中选出任务相关的子集, 减少决策核心的 token 消耗
        selected_tools = self._select_tools(task)
        logger.log("secretary", "工具筛选完成",
                   f"{len(selected_tools) if selected_tools else '全部'} 个工具")

        return filtered, selected_tools

    def _build_library_injection(self, results: dict, task: str,
                                  max_chars: int = 1000) -> str:
        """
        构建四库主动注入文本 —— 严格控制 token 预算
        预算分配:
        - 经验库: 最多3条, 每条<=200字
        - 记忆库(项目事实): 最多1条, <=150字
        - 知识库: 最多2条, 每条<=150字
        - 工具库: 最多3条, 每条<=80字
        总注入 <= max_chars 字 (约1500 token)
        """
        sections = []
        used_chars = 0

        def _truncate(text: str, limit: int) -> str:
            text = text.strip()
            if len(text) > limit:
                return text[:limit] + "..."
            return text

        def _add_section(title: str, items: list, per_item_limit: int,
                         max_items: int, min_score: float = 0.0):
            nonlocal used_chars
            if not items:
                return
            # 按匹配度排序, 只取高分的
            sorted_items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)
            taken = []
            for it in sorted_items[:max_items]:
                if it.get("score", 0) < min_score:
                    continue
                content = _truncate(it.get("content", ""), per_item_limit)
                if content:
                    taken.append(content)
            if not taken:
                return
            section_text = f"【{title}】\n" + "\n".join(f"  - {t}" for t in taken)
            # 检查预算
            if used_chars + len(section_text) > max_chars:
                remaining = max_chars - used_chars
                if remaining > 50:
                    section_text = section_text[:remaining] + "..."
                else:
                    return
            sections.append(section_text)
            used_chars += len(section_text)

        # 经验库 (权重最高, 最有价值)
        _add_section("经验库", results.get("experience", []),
                     per_item_limit=200, max_items=3, min_score=0.3)
        # 记忆库 (项目事实, 每次都有用)
        _add_section("记忆库", results.get("memory", []),
                     per_item_limit=150, max_items=1, min_score=0.2)
        # 知识库
        _add_section("知识库", results.get("knowledge", []),
                     per_item_limit=150, max_items=2, min_score=0.3)
        # 工具库 (使用技巧)
        _add_section("工具技巧", results.get("tools", []),
                     per_item_limit=80, max_items=3, min_score=0.3)

        if not sections:
            return ""

        injection = "\n\n".join(sections)
        logger.log("secretary", "四库主动注入",
                   f"{len(sections)}个库, {used_chars}字, 预算{max_chars}字")
        return injection

    def _select_tools(self, task: str) -> list[str] | None:
        """
        工具筛选: 用关键词匹配快速选出相关工具, 减少上下文 token
        返回工具名列表, None 表示不筛选(全部传入)
        """
        if not self.tool_manager:
            return None
        all_tools = self.tool_manager.list_tools()
        task_lower = task.lower()

        # 简单任务: 短文本 + 纯问候/极简问题才走轻量工具集
        # 注意: 不能只看关键词, "核心功能是什么" 包含"是什么"但是复杂任务
        simple_keywords = ["你好", "hello", "谢谢", "1+1", "hi", "hey",
                          "在吗", "在不在", "你是谁"]
        is_short = len(task.strip()) <= 15
        if is_short and any(kw in task_lower for kw in simple_keywords):
            # 简单问答保留基础工具, 不砍到只剩 current_time
            return ["current_time", "web_search"]

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
            "code_lint": ["lint", "格式化", "格式", "代码规范", "ruff", "eslint", "检查语法"],
            "code_find_def": ["函数定义", "类定义", "定义在哪", "find_def"],
            "code_find_refs": ["引用", "哪里调用", "调用处", "find_refs"],
            "code_outline": ["大纲", "结构", "文件结构", "outline"],
            "code_edit_symbol": ["修改函数", "修改类", "替换函数", "edit_symbol"],
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
                           "project_analyze", "file_list", "file_write",
                           "code_lint", "code_find_def", "code_find_refs",
                           "code_outline", "code_edit_symbol"])

        # MCP 文件系统工具: 文件相关任务时带上
        if any(kw in task_lower for kw in ["文件", "目录", "项目", "代码", "读取", "修改"]):
            for t in all_tools:
                if t["name"].startswith("mcp_") and ("file" in t["name"] or "directory" in t["name"]):
                    selected.add(t["name"])

        # 至少保留 3 个工具, 避免筛选过严
        if len(selected) < 3:
            selected.update(["code_exec", "file_read", "current_time"])

        # 排除高失败率工具 (失败率>50% 且调用>=3次)
        if self.tool_manager:
            unreliable = self.tool_manager.get_unreliable_tools(threshold=0.5)
            if unreliable:
                before = len(selected)
                selected -= unreliable
                if len(selected) < 3:
                    # 过滤后太少, 保留成功率最高的几个
                    remaining = sorted(unreliable, key=lambda n: self.tool_manager.get_tool_failure_rate(n))
                    selected.update(remaining[:3 - len(selected)])
                if before != len(selected):
                    logger.log("secretary", "工具降权", f"排除高失败率工具: {unreliable}")

        # 最多 12 个工具, 避免筛选失效
        result = list(selected)[:12]
        return result if result else None

    def reflect(self, task: str, result: str, context: str, mode: str = "work", append_to_id: int = None, custom_prompt: str = None) -> str:
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
                    {"role": "system", "content": custom_prompt or self.REFLECT_PROMPT},
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
                reflection_text = f"\n\n---\n\n[反思复盘]\n{reflection[:500]}"
                if append_to_id:
                    # 合并到 task_result 条目, 不新建
                    ok = self.libs.experience.append_content(append_to_id, reflection_text)
                    if ok:
                        logger.log("secretary", "反思完成", f"已合并到经验库#{append_to_id}")
                    else:
                        # 追加失败, 降级为新建
                        item = self.libs.experience.add(
                            f"[反思] 任务: {task[:60]}\n经验: {reflection[:300]}",
                            meta={"type": "reflection"}, mode=mode,
                        )
                        self._sync_to_ima(item, task)
                else:
                    item = self.libs.experience.add(
                        f"[反思] 任务: {task[:60]}\n经验: {reflection[:300]}",
                        meta={"type": "reflection"}, mode=mode,
                    )
                    logger.log("secretary", "反思完成", "已存入经验库")
                    self._sync_to_ima(item, task)
            else:
                logger.log("secretary", "反思完成", "无特别需要反思的内容")
            return reflection
        except Exception as e:
            logger.log("secretary", "反思失败", str(e))
            return f"(反思失败: {e})"

    # ========== 三模式差异化沉淀 ==========

    BRAINSTORM_REFLECT_PROMPT = """你是创意复盘助手。
【任务】从这次头脑风暴中提炼有价值的创意模式、思维框架或洞察。
【要求】
1. 哪些思维角度或方法产生了好创意?
2. 提炼可复用的创意模式, 简洁具体
3. 如果没有特别有价值的, 回复"无特别需要沉淀"
"""

    TOOL_TIP_PROMPT = """你是工具使用教练。
【任务】从这次工具使用中提炼可复用的技巧。
【要求】
1. 哪个工具用得好, 为什么?
2. 有没有更高效的用法或参数组合?
3. 踩了什么坑, 怎么避免?
每条技巧不超过50字, 只输出技巧内容, 不要编号。
如果没有值得沉淀的, 回复"无特别技巧"。
"""

    PREF_WORK_PROMPT = """你是用户偏好提取器。
【任务】从这次技术对话中提取用户明确表达的技术偏好、工作习惯或重要事实。
【规则】
1. 只提取用户明确说过的, 不要猜测
2. 格式: "用户偏好: xxx" 或 "项目事实: xxx"
3. 例如: "用户偏好: 喜欢简洁代码, 不要过度设计"
4. 例如: "项目事实: 项目用Python 3.11 + Flask"
如果没有明确偏好, 回复"无明确偏好"。
"""

    PREF_CHAT_PROMPT = """你是用户偏好提取器。
【任务】从这次日常对话中提取用户明确表达的个人偏好、习惯或重要事实。
【规则】
1. 只提取用户明确说过的, 不要猜测
2. 格式: "用户事实: xxx" 或 "用户偏好: xxx"
3. 例如: "用户事实: 在洛杉矶工作"
4. 例如: "用户偏好: 喜欢用具体例子解释概念"
如果没有明确偏好, 回复"无明确偏好"。
"""

    PROJECT_FACT_PROMPT = """你是项目事实提取器。
【任务】从这次编码任务中提取关于当前项目的客观事实(技术栈/入口/配置/目录结构/关键约定)。
【规则】
1. 只提取客观事实, 不要猜测
2. 格式: "项目事实: xxx"
3. 例如: "项目事实: 后端用Flask, 入口是desktop.py"
4. 例如: "项目事实: 数据库用SQLite, 存在data/目录"
5. 例如: "项目事实: 前端用原生HTML+JS, 模板在ui/templates/"
6. 如果没有新的项目事实, 回复"无新项目事实"
"""

    KNOWLEDGE_EXTRACT_PROMPT = """你是知识提取器。
【任务】从这次技术任务的结果中提取值得长期保存的通用技术知识。
【规则】
1. 只提取通用技术知识、最佳实践、概念解释, 不提取个人经验或任务流水账
2. 例如: "WebSocket心跳包: 每30秒发ping, 超时未响应则重连"
3. 例如: "Flask SSE: 用生成器yield数据, 前端EventSource接收, 注意解析data:行"
4. 每条知识简洁具体, 不超过80字, 只输出知识内容不要编号
5. 如果没有值得提取的通用知识, 回复"无通用知识"
"""

    def post_task_learning(self, task: str, result: str, context: str,
                           tools_used: list = None, mode: str = "work",
                           success: bool = True):
        """任务完成后的差异化沉淀入口, 按模式决定写哪些库"""
        if not self.configured:
            return
        try:
            if mode == "work":
                self._learn_work(task, result, context, tools_used, success)
            elif mode == "brainstorm":
                self._learn_brainstorm(task, result)
            elif mode == "chat":
                self._learn_chat(task, result)
        except Exception as e:
            logger.log("secretary", "沉淀失败", str(e))

    def _learn_work(self, task, result, context, tools_used, success=True):
        """Work模式: 经验+工具技巧+任务记录+用户偏好. 失败任务强制复盘失败原因"""
        # 1. 经验库: 任务结果 (拿到 item_id, 后续反思合并到同一条)
        result_item = self.record_result(task, result, tools_used=tools_used, success=success, mode="work")
        result_id = result_item.get("id") if isinstance(result_item, dict) else None
        # 2. 经验库: 反思 (合并到 task_result 条目, 不新建)
        # 失败任务用专门的失败复盘提示词, 重点分析"为什么失败"和"下次怎么避免"
        reflect_prompt = self.FAIL_REFLECT_PROMPT if not success else None
        self.reflect(task, result, context, mode="work", append_to_id=result_id, custom_prompt=reflect_prompt)
        # 3. 工具库: 从工具使用中提炼技巧
        if tools_used:
            self._learn_tool_tips(tools_used, task, result)
        # 4. 记忆库: 任务流水账 (配合 clean_memory 清理)
        self._record_task_memory(task, result)
        # 5. 记忆库: 用户偏好提取
        self._extract_preferences(task, result, self.PREF_WORK_PROMPT, "work")
        # 6. 记忆库: 项目事实提取 (技术栈/入口/配置/目录结构, 长期保留)
        self._extract_project_facts(task, result)
        # 7. 知识库: 通用技术知识提取
        self._extract_knowledge(task, result)

    def _learn_brainstorm(self, task, result):
        """Brainstorm模式: 只沉淀创意模式到经验库"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.3,
                messages=[
                    {"role": "system", "content": self.BRAINSTORM_REFLECT_PROMPT},
                    {"role": "user", "content": f"【脑暴主题】{task[:200]}\n\n【产出】{result[:500]}\n\n请提炼创意模式:"},
                ],
            )
            insight = resp.choices[0].message.content.strip()
            if insight and "无特别需要沉淀" not in insight:
                item = self.libs.experience.add(
                    f"[创意模式] 主题: {task[:60]}\n洞察: {insight[:300]}",
                    meta={"type": "creative_insight"}, mode="brainstorm",
                )
                logger.log("secretary", "脑暴沉淀", "创意模式已存入经验库")
                self._sync_to_ima(item, task)
        except Exception as e:
            logger.log("secretary", "脑暴沉淀失败", str(e))

    def _learn_chat(self, task, result):
        """Chat模式: 只提取用户偏好到记忆库"""
        self._extract_preferences(task, result, self.PREF_CHAT_PROMPT, "chat")

    def _learn_tool_tips(self, tools_used, task, result):
        """从工具使用中提炼技巧到工具库"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.3,
                messages=[
                    {"role": "system", "content": self.TOOL_TIP_PROMPT},
                    {"role": "user", "content": (
                        f"【任务】{task[:150]}\n"
                        f"【使用工具】{', '.join(tools_used)}\n"
                        f"【结果】{result[:300]}\n\n请提炼技巧:"
                    )},
                ],
            )
            tips = resp.choices[0].message.content.strip()
            if tips and "无特别技巧" not in tips:
                for tip in tips.split("\n"):
                    tip = tip.strip().lstrip("-*0123456789. ")
                    if tip and len(tip) > 5:
                        self.libs.tools.add(
                            f"{tip}\n(来源: 任务'{task[:40]}' 使用 {', '.join(tools_used[:3])})",
                            meta={"type": "tool_tip", "tools": tools_used},
                        )
                logger.log("secretary", "工具技巧沉淀", f"{len(tools_used)}个工具使用已提炼")
        except Exception as e:
            logger.log("secretary", "工具技巧沉淀失败", str(e))

    def _record_task_memory(self, task, result):
        """记录任务流水账到记忆库 (clean_memory 会定期清理)"""
        self.libs.memory.add(
            f"完成过任务: {task[:80]} (结果长度: {len(result)}字)",
            meta={"type": "task_memory"},
        )

    def _extract_project_facts(self, task, result):
        """从编码任务中提取项目事实(技术栈/入口/配置/目录结构), 存入记忆库长期保留"""
        try:
            # 只有编码任务才提取项目事实
            if not any(k in task for k in ["写", "代码", "实现", "修改", "修复", "文件", "函数", "重构", "开发"]):
                return
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.2,
                messages=[
                    {"role": "system", "content": self.PROJECT_FACT_PROMPT},
                    {"role": "user", "content": f"""【任务】{task[:200]}

【结果】{result[:500]}

请提取项目事实:"""},
                ],
            )
            facts = resp.choices[0].message.content.strip()
            if facts and "无新项目事实" not in facts:
                for line in facts.split("\n"):
                    line = line.strip().lstrip("-*0123456789. ")
                    if line and "项目事实" in line and len(line) > 8:
                        # 去重: 检查记忆库是否已有相同事实
                        existing = self.libs.memory.search(line[:30], top_k=5) if hasattr(self.libs.memory, 'search') else []
                        if not any(line[:30] in (e.get('content','') if isinstance(e, dict) else str(e)) for e in existing):
                            self.libs.memory.add(
                                line,
                                meta={"type": "project_fact", "importance": 5},
                            )
                            logger.log("secretary", "项目事实提取", line[:60])
        except Exception as e:
            logger.log("secretary", "项目事实提取失败", str(e))

    def _extract_preferences(self, task, result, prompt, mode):
        """从对话中提取用户偏好到记忆库"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.1,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"【用户说】{task[:300]}\n\n【助手回复】{result[:300]}\n\n请提取偏好:"},
                ],
            )
            prefs = resp.choices[0].message.content.strip()
            if prefs and "无明确偏好" not in prefs:
                added = 0
                for line in prefs.split("\n"):
                    line = line.strip().lstrip("-*0123456789. ")
                    if line and len(line) > 3:
                        # 去重: 检查记忆库是否已有相似内容(前20字匹配)
                        if not self._memory_exists(line):
                            self.libs.memory.add(
                                line, meta={"type": "user_preference", "mode": mode},
                            )
                            added += 1
                logger.log("secretary", "用户偏好提取", f"模式: {mode}, 新增{added}条(已去重)")
        except Exception as e:
            logger.log("secretary", "偏好提取失败", str(e))

    def _extract_knowledge(self, task, result):
        """从任务结果中提取通用技术知识到知识库"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.2,
                messages=[
                    {"role": "system", "content": self.KNOWLEDGE_EXTRACT_PROMPT},
                    {"role": "user", "content": (
                        f"【任务】{task[:200]}\n"
                        f"【结果】{result[:600]}\n\n请提取通用知识:"
                    )},
                ],
            )
            knowledge = resp.choices[0].message.content.strip()
            if knowledge and "无通用知识" not in knowledge:
                added = 0
                for line in knowledge.split("\n"):
                    line = line.strip().lstrip("-*0123456789. ")
                    if line and len(line) > 5:
                        # 简单去重: 检查知识库是否已有高度相似内容
                        if not self._knowledge_exists(line):
                            self.libs.knowledge.add(
                                line, meta={"type": "auto_extracted", "source_task": task[:40]},
                            )
                            added += 1
                if added > 0:
                    logger.log("secretary", "知识提取", f"知识库+{added}条")
        except Exception as e:
            logger.log("secretary", "知识提取失败", str(e))

    def _knowledge_exists(self, text: str) -> bool:
        """简单去重: 检查知识库是否已有相似内容(前30字匹配)"""
        prefix = text[:30]
        for item in self.libs.knowledge.all():
            if prefix in item.get("content", ""):
                return True
        return False

    def _memory_exists(self, text: str) -> bool:
        """简单去重: 检查记忆库是否已有相似内容(前20字匹配, 只查user_preference类型)"""
        prefix = text[:20]
        for item in self.libs.memory.all():
            meta = item.get("meta", {})
            if isinstance(meta, str):
                try:
                    import json as _json
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            if meta.get("type") == "user_preference" and prefix in item.get("content", ""):
                return True
        return False

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
3. 【最高优先级】重要经验必须保留, 绝对不能压缩掉:
   - 失败教训(为什么失败、下次怎么避免)
   - 踩过的坑(具体的错误和解决方法)
   - 关键决策(为什么选这个方案)
   - 可复用的模式/技巧
4. 每条经验简洁具体, 不超过80字
5. 只输出精炼后的经验列表, 每条一行, 不要编号, 不要解释
6. 如果重要经验超过 {keep} 条, 优先保留重要经验, 可以超过 {keep} 条

【原始经验】
{raw_experiences}
"""

    def compact_status(self) -> dict:
        """经验压缩状态查询 (供前端展示)"""
        return {
            "running": self._compact_running,
            "last_result": self._last_compact_result,
            "current_count": len(self.libs.experience),
            "threshold": self.EXPERIENCE_COMPACT_THRESHOLD,
        }

    def compact_experience_async(self) -> dict:
        """非阻塞触发经验压缩, 立即返回状态; 压缩在后台 daemon 线程执行

        关键: 按 max_id 快照, 压缩期间新增的条目(id > snapshot)不受影响,
        解决同步压缩 clear()+add() 会丢新数据的竞态。
        """
        if not self.configured:
            return {"ok": False, "reason": "未配置 API, 无法压缩"}
        with self._compact_lock:
            if self._compact_running:
                return {"ok": False, "reason": "正在压缩中", "running": True}
            count = len(self.libs.experience)
            if count < self.EXPERIENCE_COMPACT_THRESHOLD:
                return {"ok": False,
                        "reason": f"经验库仅 {count} 条, 未达阈值 {self.EXPERIENCE_COMPACT_THRESHOLD}"}
            snapshot_max_id = self.libs.experience.db.query_one(
                "SELECT MAX(id) FROM experience"
            )[0] or 0
            self._compact_running = True
        t = threading.Thread(
            target=self._do_compact_background,
            args=(snapshot_max_id,),
            daemon=True, name="experience-compact",
        )
        t.start()
        logger.log("secretary", "经验压缩后台启动",
                   f"快照 id<={snapshot_max_id}, 当前 {count} 条")
        return {"ok": True, "async": True, "snapshot_id": snapshot_max_id,
                "message": "经验压缩已在后台开始"}

    def _backup_experience(self, items: list[dict]):
        """压缩前备份经验库到 data/backups/, 防止 LLM 提炼丢信息"""
        try:
            backup_dir = DATA_DIR / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / f"experience_{time.strftime('%Y%m%d_%H%M%S')}.json"
            import json
            backup_file.write_text(
                json.dumps(items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 只保留最近 5 个备份
            backups = sorted(backup_dir.glob("experience_*.json"))
            for old_bak in backups[:-5]:
                old_bak.unlink(missing_ok=True)
            logger.log("secretary", "经验库已备份", f"{backup_file.name} ({len(items)} 条)")
        except Exception as e:
            logger.log("secretary", "经验库备份失败", str(e))

    def _do_compact_background(self, snapshot_max_id: int):
        """后台线程: 备份 → LLM 提炼 → 按快照范围删除 → 插入精炼条目"""
        try:
            snapshot = [i for i in self.libs.experience.all()
                        if i["id"] <= snapshot_max_id]
            if not snapshot:
                return
            self._backup_experience(snapshot)

            raw_text = "\n".join(
                f"[{i+1}] {item['content'][:200]}"
                for i, item in enumerate(snapshot)
            )
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                messages=[{
                    "role": "user",
                    "content": self.COMPACT_PROMPT.format(
                        keep=self.EXPERIENCE_COMPACT_KEEP,
                        raw_experiences=raw_text[:6000],
                    ),
                }],
            )
            refined = resp.choices[0].message.content.strip()
            usage = resp.usage
            cost_tracker.record(
                model=self.model,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                task="经验库压缩(异步)",
            )

            old_count = len(snapshot)
            deleted = self.libs.experience.delete_before(snapshot_max_id)
            added = 0
            for line in refined.splitlines():
                line = line.strip().lstrip("-•*0123456789.) ")
                if line and len(line) > 5:
                    self.libs.experience.add(line, meta={"type": "compacted"})
                    added += 1

            logger.log("secretary", "经验库后台压缩完成",
                       f"{old_count} 条(删{deleted}) → {added} 条")
            self._last_compact_result = {
                "ok": True, "old": old_count, "new": added,
                "deleted": deleted, "async": True,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as e:
            logger.log("secretary", "经验库后台压缩失败", str(e))
            self._last_compact_result = {"ok": False, "error": str(e)}
        finally:
            with self._compact_lock:
                self._compact_running = False

    def compact_experience(self) -> dict:
        """经验库压缩: 超过阈值时调用 LLM 提炼合并 (同步, 手动触发用)"""
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

            # 压缩前备份, 再清空旧经验写入精炼后的
            old_count = len(self.libs.experience)
            self._backup_experience(self.libs.experience.all())
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


    def _sync_to_ima(self, item, task_summary=""):
        """异步同步经验到 IMA 笔记（云端备份，失败不影响主流程）"""
        if not _IMA_AVAILABLE or not item:
            return
        try:
            ima = get_ima_client()
            if ima.enabled:
                exp_id = item.get("id", 0)
                content = item.get("content", "")
                ima.sync_experience(exp_id, content, task_summary)
        except Exception as e:
            logger.log("secretary", "IMA同步跳过", str(e))

    def record_result(self, task: str, result: str, tools_used: list = None,
                      success: bool = True, mode: str = "work"):
        """任务完成后沉淀到经验库 (结构化: 成功/失败/工具/可复用模式)"""
        tools_str = ", ".join(tools_used) if tools_used else "无"
        status = "成功" if success else "失败"
        item = self.libs.experience.add(
            f"[{status}] 任务: {task[:80]}\n"
            f"使用工具: {tools_str}\n"
            f"结果摘要: {result[:200]}",
            mode=mode,
            meta={"type": "task_result", "success": success,
                  "tools_used": tools_used or []},
        )
        logger.log("secretary", "沉淀完成",
                   f"经验库+1 ({status}, 工具: {tools_str})")
        # 自动检查是否需要压缩 (异步, 不阻塞任务返回)
        if len(self.libs.experience) >= self.EXPERIENCE_COMPACT_THRESHOLD:
            logger.log("secretary", "触发后台自动压缩",
                       f"经验库 {len(self.libs.experience)} 条达阈值")
            self.compact_experience_async()
        self._sync_to_ima(item, task)
        # 异步生成单元测试 (不阻塞主流程, 有条件触发)
        self._maybe_generate_tests(task, tools_used, success, mode)
        return item

    # ========== 测试自动生成 (异步, 有条件触发) ==========

    def _maybe_generate_tests(self, task: str, tools_used: list, success: bool, mode: str = "work"):
        """判断是否需要生成测试, 需要则异步启动"""
        if not self._should_generate_tests(tools_used, success, mode):
            return
        try:
            thread = threading.Thread(
                target=self._generate_tests_async,
                args=(task, tools_used),
                daemon=True
            )
            thread.start()
            logger.log("secretary", "测试生成已启动", f"后台异步生成, 不阻塞主流程")
        except Exception as e:
            logger.log("secretary", "测试生成启动失败", str(e))

    def _should_generate_tests(self, tools_used: list, success: bool, mode: str = "work") -> bool:
        """判断是否需要生成测试:
        - 任务必须成功
        - 必须改了代码 (file_write 或 code_edit 或 code_edit_symbol)
        - 只在 work 模式生成
        """
        if not success:
            return False
        if not tools_used:
            return False
        if mode != "work":
            return False
        code_tools = {"file_write", "code_edit", "code_edit_symbol"}
        return any(t in code_tools for t in tools_used)

    def _generate_tests_async(self, task: str, tools_used: list):
        """后台生成单元测试:
        1. 用 git diff 获取实际改动的代码
        2. 让秘书模型基于实际代码生成 pytest 测试用例
        3. 写入临时目录并运行
        4. 结果存入经验库
        """
        try:
            import tempfile
            import os
            import subprocess
            import sys

            # 1. 用 git diff 获取实际改动的代码 (最近一次提交后的改动)
            changed_code = ""
            try:
                diff_result = subprocess.run(
                    ["git", "diff", "HEAD", "--unified=3"],
                    capture_output=True, text=True, timeout=10,
                    cwd=os.getcwd(), encoding="utf-8", errors="replace"
                )
                if diff_result.returncode == 0 and diff_result.stdout:
                    changed_code = diff_result.stdout[:3000]  # 限制长度避免超token
            except Exception:
                pass

            # 如果没有 git diff (可能是新文件未跟踪), 尝试 git status
            if not changed_code:
                try:
                    status_result = subprocess.run(
                        ["git", "status", "--short"],
                        capture_output=True, text=True, timeout=10,
                        cwd=os.getcwd(), encoding="utf-8", errors="replace"
                    )
                    if status_result.returncode == 0:
                        changed_code = f"变更文件:\n{status_result.stdout[:500]}"
                except Exception:
                    pass

            # 2. 让秘书模型基于实际代码生成测试
            code_context = f"\n\n实际改动代码(git diff):\n```\n{changed_code}\n```" if changed_code else ""
            prompt = f"""为以下任务中改动的函数生成 pytest 单元测试。

任务: {task[:200]}
使用的工具: {', '.join(tools_used)}{code_context}

输出要求:
- 直接输出完整的 Python 测试文件代码, 用 ```python 包裹
- 最多 3 个测试函数: 正常输入、边界值、异常输入
- 导入路径根据实际文件路径推断
- 不要解释, 不要额外文字
"""
            resp = self.client.chat.completions.create(
                model=SECRETARY_CONFIG["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1500,
            )
            raw_content = resp.choices[0].message.content or ""
            test_code = raw_content.strip()
            # 提取代码块 (如果有 ```python 包裹)
            if "```python" in test_code:
                test_code = test_code.split("```python")[1].split("```")[0].strip()
            elif "```" in test_code:
                test_code = test_code.split("```")[1].split("```")[0].strip()

            if not test_code or len(test_code) < 15:
                logger.log("secretary", "测试生成跳过",
                           f"代码为空或过短(len={len(test_code)}), 原始前100字: {raw_content[:100]}")
                self.last_test_result = {
                    "passed": False, "status": "生成失败",
                    "task": task[:80], "output": f"测试代码为空(len={len(test_code)})",
                    "test_count": 0, "timestamp": time.strftime("%H:%M:%S"),
                }
                return

            # 2. 写入项目 tests/auto_generated/ 目录, 从项目根目录运行
            import os as _os
            project_root = _os.getcwd()
            test_dir = _os.path.join(project_root, "tests", "auto_generated")
            _os.makedirs(test_dir, exist_ok=True)
            test_filename = f"test_auto_{int(time.time())}.py"
            test_path = _os.path.join(test_dir, test_filename)
            with open(test_path, "w", encoding="utf-8") as f:
                f.write(test_code)

            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"],
                    capture_output=True, text=True, timeout=20,
                    cwd=project_root, encoding="utf-8", errors="replace"
                )
                output = (result.stdout + result.stderr)[:1000]
                passed = result.returncode == 0

                # 统计测试用例数
                import re as _re
                passed_count = len(_re.findall(r"PASSED", output))
                failed_count = len(_re.findall(r"FAILED", output))
                test_count = passed_count + failed_count or 3

                # 3. 结果存入经验库 + 存储供前端轮询
                status = "通过" if passed else "失败"
                self.last_test_result = {
                    "passed": passed,
                    "status": status,
                    "task": task[:80],
                    "output": output[:500],
                    "test_count": test_count,
                    "test_file": f"tests/auto_generated/{test_filename}",
                    "timestamp": time.strftime("%H:%M:%S"),
                }
                self.libs.tools.add(
                    f"[自动测试{status}] 任务: {task[:60]}\n"
                    f"测试文件: tests/auto_generated/{test_filename}\n"
                    f"测试结果: {output[:300]}",
                    meta={"type": "auto_test", "passed": passed}
                )
                logger.log("secretary", "自动测试完成",
                           f"{status}, {passed_count}通过/{failed_count}失败, 文件: {test_filename}")
            except subprocess.TimeoutExpired:
                self.last_test_result = {
                    "passed": False, "status": "超时",
                    "task": task[:80], "output": "20秒未完成",
                    "test_count": 0, "timestamp": time.strftime("%H:%M:%S"),
                }
                logger.log("secretary", "自动测试超时", "20秒未完成, 跳过")
            except Exception as e:
                logger.log("secretary", "自动测试运行失败", str(e))
                self.last_test_result = {
                    "passed": False, "status": "运行错误",
                    "task": task[:80], "output": str(e)[:300],
                    "test_count": 0, "timestamp": time.strftime("%H:%M:%S"),
                }
        except Exception as e:
            logger.log("secretary", "测试生成异常", str(e))

    def seed_demo_data(self) -> dict:
        """预置实用示例数据 (幂等: 四库已有任何内容则跳过, 防止重复灌入)"""
        existing = (len(self.libs.knowledge) + len(self.libs.tools)
                    + len(self.libs.experience) + len(self.libs.memory))
        if existing > 0:
            logger.log("system", "预置示例数据跳过", f"四库已有 {existing} 条, 不重复填充")
            return {"ok": False, "skipped": True, "existing": existing,
                    "message": f"四库已有 {existing} 条内容, 跳过重复填充"}

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
        return {"ok": True, "skipped": False, "added": total, "total": total,
                "message": f"示例数据已填充, 共 {total} 条"}
