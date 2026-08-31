"""
决策核心 (Nexus) —— 对应视频中的 Nixxes
职责: 基于秘书递达的上下文 + 历史对话做决策, 必要时调用工具
支持 Function Calling: 思考 → 调工具 → 看结果 → 再思考 → 最终回答
支持流式输出 (SSE)
"""
import json
import os

from openai import OpenAI
from config import DECISION_CONFIG
from utils.logger import logger
from utils.cost_tracker import cost_tracker
from core.context_manager import ContextManager, truncate_tool_result, count_messages_tokens


class DecisionCore:
    """决策核心: 接收任务 + 秘书整理的上下文 + 历史对话, 可调用工具"""

    def __init__(self, tool_manager=None):
        api_key = DECISION_CONFIG["api_key"] or "sk-not-configured"
        self.client = OpenAI(
            base_url=DECISION_CONFIG["base_url"],
            api_key=api_key,
            timeout=120.0,
        )
        self.model = DECISION_CONFIG["model"]
        self.temperature = DECISION_CONFIG["temperature"]
        self.configured = bool(DECISION_CONFIG["api_key"])
        self.tool_manager = tool_manager
        self.max_tool_calls = 8
        self.last_tools_used: list[str] = []
        # 本轮决策中工具执行失败的次数 (成功判定依据, 每次 decide 开头重置)
        self.last_tool_errors = 0
        # 模式: work=完整编码助手, chat=轻量聊天 (影响系统提示词/工具/模型)
        self.mode = "work"
        # 模型覆盖: None 用默认决策模型, 非 None 时临时切换 (聊天模式用 flash)
        self.override_model: str | None = None
        self.ctx_manager = ContextManager(model=self.model, llm_client=self.client if self.configured else None)
        from core.error_diagnoser import CodeErrorDiagnoser
        self.error_diagnoser = CodeErrorDiagnoser(work_dir=os.getcwd())

    def set_mode(self, mode: str):
        """切换工作/聊天/头脑风暴模式 (由 DualCoreAgent 在任务前调用)"""
        self.mode = mode if mode in ("work", "chat", "brainstorm") else "work"

    def _active_model(self) -> str:
        return self.override_model or self.model

    def _active_system_prompt(self) -> str:
        if self.mode == "chat":
            return self.CHAT_SYSTEM_PROMPT
        if self.mode == "brainstorm":
            return self.BRAINSTORM_SYSTEM_PROMPT
        return self.SYSTEM_PROMPT

    def _active_temperature(self) -> float:
        """按模式返回 temperature: brainstorm高发散, chat自然, work稳定"""
        if self.mode == "brainstorm":
            return 0.95
        if self.mode == "chat":
            return 0.5
        return self.temperature  # work 默认 0.7

    @staticmethod
    def _is_tool_error(tool_result: str) -> bool:
        """工具返回是否为执行失败 (成功统计与重试提示共用同一判定)"""
        return tool_result.startswith(("错误", "失败", "[子代理异常]"))

    @property
    def last_had_tool_error(self) -> bool:
        """本轮决策是否出现过工具执行失败"""
        return self.last_tool_errors > 0

    SYSTEM_PROMPT = """你是 Nexus —— 专业编码助手, 双核 Agent 的决策核心。

【输出原则 —— 最高优先级, 违反则回答无效】
- 调用工具时绝对不要输出任何文字! 直接调用工具, 不要说"我要..."/"让我..."/"接下来..."
- 工具调用之间不要输出过渡文字, 所有思考在内部完成
- 只有最终回答(不再调用工具时)才输出文字
- 最终回答只给结果: 改了什么、验证结果、关键代码, 不要描述过程
- 简单问题直接给答案, 不废话; 先结论后细节

【工具选择原则 —— 减少无效调用】
- 写文件/创建脚本: 必须用 file_write 一次写完, 绝对不要用 code_exec 反复试写
- code_exec 只用于运行验证(跑测试/看输出), 不用于创建文件
- 修改已有文件: 小改用 code_edit, 大改用 file_write(先 file_read 读内容)
- 查文件内容: 用 file_read, 不要用 code_exec 读文件
- 列目录: 用 file_list, 不要用 code_exec 执行 ls
- 原则: 一次 file_write + 一次 code_exec 验证 = 2次调用, 不要拆成8次 code_exec

【编码工作流】
1. 理解: project_analyze 看项目结构 → code_search 找位置 → file_read 读文件
2. 方案: 明确改哪些文件, 小步修改, 改完验证再改下一个
3. 执行: 小改动用 code_edit(search_replace, 搜索文本必须唯一), 新文件用 file_write
   - 没读文件前绝对不用 file_write 覆盖
   - code_edit 搜索不到/不唯一时, 先用 file_read 确认实际内容(注意空格/缩进)
4. 验证(必须): code_exec 跑测试/lint → 失败就看错误→修复→重跑, 直到通过
5. 总结: 改了哪些文件、验证结果、未解决的问题

【临时文件规则 —— 必须遵守】
- 测试、验证、演示用的一次性文件(测试脚本/临时数据/输出产物)必须写入用户消息中标注的【临时工作目录】, 严禁写到项目根目录或其他位置
- 验证完成后必须主动调用 cleanup_temp 工具删除本次产生的临时文件, 再给出最终回答
- 正式源码和用户要求持久保留的文件禁止放入临时目录(任务结束会被自动清空)
- code_exec 自身运行后会删除脚本, 但脚本里 open() 写出的文件不会, 这类文件同样必须落在临时目录并在事后清理

【强制验证 —— 严禁幻觉】
- 创建/修改/删除文件后, 必须用 file_list 或 ls 验证文件确实存在
- 执行命令后检查退出码, 不能假设成功
- code_exec 是临时环境(写完就删), 写持久文件必须用 file_write
- 工具报错必须在回答中说明, 不能隐瞒或假装成功
- "验证通过"必须基于真实工具输出, 不能编造
- 连续失败3次 → 总结原因, 告诉用户卡在哪里

【自我验证工作流 —— 向 Codex 学习, 必须遵守】
- 改完代码必须自己启动验证, 绝对不要让用户当测试员
- 验证流程: 启动服务/运行测试 → 实际访问确认渲染 → 检查运行时报错(函数是否存在/API是否可达) → 验证关联功能 → 清理临时文件 → git diff确认
- 同一问题修改2次仍未生效, 立即停止猜测, 用证据定位根因: curl请求确认返回内容, 检查函数是否存在, 分层排查(代码/配置/缓存/环境)
- UI修改不生效时优先怀疑缓存(WebView2/浏览器缓存), 用时间戳URL或no-cache头解决
- 用完即删临时文件(_patch_*.py/_test_*.py等), 不要留在项目目录

【输出格式】
## 修改总结
(改了哪些文件, 每个文件改了什么)

## 验证结果
(测试/lint 结果, 通过或失败原因)

## 说明
(如果有需要注意的地方)
"""

    CHAT_SYSTEM_PROMPT = """你是 Nexus —— 个人聊天助手, 当前处于聊天模式。

【定位】
- 轻松、自然、简洁地对话, 像朋友聊天, 不要长篇大论
- 先给结论, 再给必要细节; 能一句话说清就不写三段
- 用户问技术/概念可以解释, 但不要主动执行工程任务(写代码/改文件/跑命令)

【可用工具】
- web_search: 查实时信息、新闻、资料
- current_time: 看当前时间
- 其他工具不要调用 (聊天模式不碰文件和代码)

【不要做】
- 不要输出"修改总结/验证结果"这类工程化格式
- 不要调用编码工具或创建文件
- 不要编造事实, 不确定就说不确定
"""

    BRAINSTORM_SYSTEM_PROMPT = """你是 Nexus —— 头脑风暴 Agent, 当前处于创意发散模式。

【核心任务】
针对用户给出的主题, 自动运行一场完整的端到端头脑风暴, 全程不需要用户介入。

【五顶思维帽 —— 必须依次切换】
1. 疯子帽: 抛开所有约束, 提出最疯狂、最不可能、最跨界的想法, 越离谱越好
2. 律师帽: 对每个想法做正反面剖析, 列出优点、缺点、风险、可行性
3. 婴儿帽: 用最原始、最天真的视角重新提问, 挑战所有默认假设
4. 记者帽: 用 web_search 查证关键事实, 收集数据、案例、趋势, 用事实支撑创意
5. 建筑师帽: 整合所有想法, 构建可落地的方案框架, 给出实施路径

【四步工作流 —— 必须贯穿全程】
1. 破冰: 用一个随机词或跨界类比打开思路(自己想一个随机词, 强行和主题关联)
2. 发散: 五顶帽子轮番上阵, 尽可能多地产生想法, 不评判
3. 碰撞: 把不同想法交叉组合, 寻找化学反应, 列出组合后的新可能
4. 收敛: 筛选出最有价值的3个方向, 给出具体的行动建议

【必须包含的产出】
- 正反面优缺点剖析(至少对TOP5想法逐一分析)
- 一篇"2033年未来新闻稿": 想象这个想法在2033年成为现实, 写一篇新闻报道
- 利益相关者地图: 列出谁会受益、谁会受损、谁是关键决策者

【铁律】
- 全程禁止向用户提问, 禁止让用户做选择题, 禁止让用户中途输入
- 直接输出一场完整的头脑风暴, 不要说"让我们开始吧"之类的过渡语
- 以收敛结论和一句话金句收尾
- 可以用 web_search 查资料和趋势, 但不要因为查资料而中断创意流程

【输出格式】
用清晰的标题分隔五个帽子和四个步骤, 最后给收敛结论和金句。
"""

    def _get_filtered_functions(self, allowed_tools: list[str] = None) -> list:
        """根据白名单过滤工具, 减少上下文 token"""
        if not self.tool_manager:
            return []
        all_funcs = self.tool_manager.get_functions()
        if not allowed_tools:
            return all_funcs
        allowed = set(allowed_tools)
        return [f for f in all_funcs
                if f.get("function", {}).get("name") in allowed]

    def _build_messages(self, task, context, history_text):
        history_section = f"\n\n【历史对话】\n{history_text}" if history_text else ""
        user_content = (
            f"【任务】\n{task}\n\n"
            f"【秘书递达的上下文】\n{context}"
            f"{history_section}"
        )
        return [
            {"role": "system", "content": self._active_system_prompt()},
            {"role": "user", "content": user_content},
        ]

    def decide(self, task: str, context: str, history_text: str = "",
               allowed_tools: list[str] = None) -> str:
        """非流式决策. allowed_tools: 只允许使用的工具名列表, None=全部"""
        logger.log("nexus", "开始决策",
                   f"任务: {task[:50]}, 工具: {len(allowed_tools) if allowed_tools else '全部'}")
        self.last_tools_used = []
        self.last_tool_errors = 0
        self.error_diagnoser.reset()
        messages = self._build_messages(task, context, history_text)
        total_input = 0
        total_output = 0
        tool_call_count = 0
        funcs = self._get_filtered_functions(allowed_tools)

        while True:
            # 上下文压缩检查
            if self.ctx_manager.should_compress(messages):
                old_tokens = count_messages_tokens(messages)
                messages = self.ctx_manager.compress(messages)
                new_tokens = count_messages_tokens(messages)
                logger.log("nexus", "上下文压缩",
                           f"{old_tokens} → {new_tokens} token (第{self.ctx_manager.compress_count}次)")

            kwargs = {}
            if funcs:
                kwargs["tools"] = funcs
                kwargs["tool_choice"] = "auto"

            resp = self.client.chat.completions.create(
                model=self._active_model(),
                temperature=self._active_temperature(),
                messages=messages,
                **kwargs,
            )
            message = resp.choices[0].message
            usage = resp.usage
            total_input += usage.prompt_tokens
            total_output += usage.completion_tokens

            if message.tool_calls and tool_call_count < self.max_tool_calls:
                messages.append(message)
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    self.last_tools_used.append(tool_name)
                    try:
                        tool_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}
                    logger.log("nexus", "调用工具",
                               f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)[:50]})")
                    tool_result = self.tool_manager.execute(tool_name, **tool_args)
                    # 工具结果截断, 防止撑爆上下文
                    tool_result = truncate_tool_result(tool_result)
                    # 错误时计数 + 自动诊断代码 + 注入相关文件上下文
                    if self._is_tool_error(tool_result):
                        self.last_tool_errors += 1
                        try:
                            diag = self.error_diagnoser.diagnose(
                                tool_output=tool_result,
                                tool_name=tool_name,
                                tool_args=tool_args,
                            )
                            diag_ctx = self.error_diagnoser.to_context_string(diag)
                            tool_result += diag_ctx
                            logger.log("nexus", "代码错误自动诊断",
                                       f"读取 {diag.get('files_examined', 0)} 个文件, "
                                       f"错误 {len(diag.get('errors', []))} 个")
                        except Exception as diag_err:
                            tool_result += (
                                "\n\n[系统提示] 工具执行失败, 请检查代码和参数。"
                                "如果是文件不存在, 先用 file_list 确认路径; "
                                "如果是语法错误, 修正后重试; 连续失败请换一种方法。"
                            )
                    logger.log("nexus", "工具返回",
                               f"{tool_name}: {tool_result[:60]}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    })
                tool_call_count += 1
                continue

            result = (message.content or "").strip()
            cost_tracker.record(
                model=self.model,
                input_tokens=total_input,
                output_tokens=total_output,
                task=f"决策: {task[:30]}",
            )
            self.last_input_tokens = total_input
            self.last_output_tokens = total_output
            logger.log("nexus", "决策完成",
                       f"输出 {len(result)} 字, 调用工具 {tool_call_count} 次, token: {total_input}+{total_output}")
            return result

    def decide_stream(self, task: str, context: str, history_text: str = "",
                      allowed_tools: list[str] = None):
        """
        流式决策, yield 事件字典:
        {"type": "token", "content": "..."}
        {"type": "tool_start", "name": "...", "args": "..."}
        {"type": "tool_end", "name": "...", "result": "..."}
        {"type": "done", "result": "...", "tool_calls": N}
        allowed_tools: 只允许使用的工具名列表, None=全部
        """
        logger.log("nexus", "开始流式决策",
                   f"任务: {task[:50]}, 工具: {len(allowed_tools) if allowed_tools else '全部'}")
        self.last_tools_used = []
        self.last_tool_errors = 0
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        messages = self._build_messages(task, context, history_text)
        funcs = self._get_filtered_functions(allowed_tools)
        tool_call_count = 0
        full_result = ""
        total_input = 0
        total_output = 0

        while True:
            # 上下文压缩检查: 超过阈值自动压缩
            if self.ctx_manager.should_compress(messages):
                old_tokens = count_messages_tokens(messages)
                messages = self.ctx_manager.compress(messages)
                new_tokens = count_messages_tokens(messages)
                logger.log("nexus", "上下文压缩",
                           f"{old_tokens} → {new_tokens} token (第{self.ctx_manager.compress_count}次)")

            kwargs = {"stream": True, "stream_options": {"include_usage": True}}
            if funcs:
                kwargs["tools"] = funcs
                kwargs["tool_choice"] = "auto"

            stream = self.client.chat.completions.create(
                model=self._active_model(),
                temperature=self._active_temperature(),
                messages=messages,
                **kwargs,
            )

            # 收集流式响应
            content_parts = []
            tool_calls_buffer = {}  # index -> {name, args_str}
            chunk_usage = None

            for chunk in stream:
                # 收集 usage (流式模式下只有最后一个 chunk 有)
                if hasattr(chunk, 'usage') and chunk.usage:
                    chunk_usage = chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 普通文本 token (先缓冲, 确认没有工具调用后才输出)
                if delta.content:
                    content_parts.append(delta.content)

                # 工具调用 (流式累积)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buffer:
                            tool_calls_buffer[idx] = {
                                "id": tc.id,
                                "name": tc.function.name or "",
                                "args": "",
                            }
                        if tc.function.name:
                            tool_calls_buffer[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_buffer[idx]["args"] += tc.function.arguments

            # 判断本轮是否会执行工具
            will_execute_tools = bool(tool_calls_buffer) and tool_call_count < self.max_tool_calls
            # 不执行工具时(最终回答或达到工具上限), 输出文本
            # 执行工具时, content_parts 是思考过程, 丢弃不输出
            if not will_execute_tools:
                for part in content_parts:
                    yield {"type": "token", "content": part}

            # 累计 token 用量
            if chunk_usage:
                total_input += getattr(chunk_usage, 'prompt_tokens', 0) or 0
                total_output += getattr(chunk_usage, 'completion_tokens', 0) or 0

            # 如果有工具调用, 执行后继续
            if tool_calls_buffer and tool_call_count < self.max_tool_calls:
                # 构造 assistant message (含 tool_calls)
                assistant_msg = {
                    "role": "assistant",
                    "content": "".join(content_parts) or None,
                    "tool_calls": [],
                }
                for idx in sorted(tool_calls_buffer.keys()):
                    tc = tool_calls_buffer[idx]
                    assistant_msg["tool_calls"].append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["args"],
                        },
                    })
                messages.append(assistant_msg)

                # 执行每个工具
                for idx in sorted(tool_calls_buffer.keys()):
                    tc = tool_calls_buffer[idx]
                    tool_name = tc["name"]
                    self.last_tools_used.append(tool_name)
                    try:
                        tool_args = json.loads(tc["args"]) if tc["args"] else {}
                    except json.JSONDecodeError:
                        tool_args = {}
                    yield {"type": "tool_start", "name": tool_name,
                           "args": json.dumps(tool_args, ensure_ascii=False)[:80]}
                    logger.log("nexus", "调用工具",
                               f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)[:50]})")
                    tool_result = self.tool_manager.execute(tool_name, **tool_args)
                    # 错误时计数并附加重试提示
                    if self._is_tool_error(tool_result):
                        self.last_tool_errors += 1
                        tool_result += (
                            "\n\n[系统提示] 工具执行失败, 请检查参数是否正确。"
                            "如果是文件不存在, 先用 file_list 确认路径; "
                            "如果是参数错误, 修正后重试; 连续失败请换一种方法。"
                        )
                    logger.log("nexus", "工具返回",
                               f"{tool_name}: {tool_result[:60]}")
                    yield {"type": "tool_end", "name": tool_name,
                           "result": tool_result[:200]}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result,
                    })

                tool_call_count += 1
                content_parts = []  # 重置, 下一轮是最终回答
                continue

            # 没有工具调用, 这是最终回答
            full_result = "".join(content_parts).strip()
            self.last_input_tokens = total_input
            self.last_output_tokens = total_output
            cost_tracker.record(
                model=self.model,
                input_tokens=total_input,
                output_tokens=total_output,
                task=f"决策(流): {task[:30]}",
            )
            logger.log("nexus", "流式决策完成",
                       f"输出 {len(full_result)} 字, 调用工具 {tool_call_count} 次, token: {total_input}+{total_output}")
            yield {"type": "done", "result": full_result, "tool_calls": tool_call_count,
                   "input_tokens": total_input, "output_tokens": total_output}
            return
