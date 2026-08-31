"""
决策核心 (Nexus) —— 对应视频中的 Nixxes
职责: 基于秘书递达的上下文 + 历史对话做决策, 必要时调用工具
支持 Function Calling: 思考 → 调工具 → 看结果 → 再思考 → 最终回答
支持流式输出 (SSE)
"""
import json
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
        self.ctx_manager = ContextManager(model=self.model, llm_client=self.client if self.configured else None)

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

【强制验证 —— 严禁幻觉】
- 创建/修改/删除文件后, 必须用 file_list 或 ls 验证文件确实存在
- 执行命令后检查退出码, 不能假设成功
- code_exec 是临时环境(写完就删), 写持久文件必须用 file_write
- 工具报错必须在回答中说明, 不能隐瞒或假装成功
- "验证通过"必须基于真实工具输出, 不能编造
- 连续失败3次 → 总结原因, 告诉用户卡在哪里

【输出格式】
## 修改总结
(改了哪些文件, 每个文件改了什么)

## 验证结果
(测试/lint 结果, 通过或失败原因)

## 说明
(如果有需要注意的地方)
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
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def decide(self, task: str, context: str, history_text: str = "",
               allowed_tools: list[str] = None) -> str:
        """非流式决策. allowed_tools: 只允许使用的工具名列表, None=全部"""
        logger.log("nexus", "开始决策",
                   f"任务: {task[:50]}, 工具: {len(allowed_tools) if allowed_tools else '全部'}")
        self.last_tools_used = []
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
                model=self.model,
                temperature=self.temperature,
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
                    # 错误时附加重试提示
                    if tool_result.startswith("错误") or tool_result.startswith("失败"):
                        tool_result += (
                            "\n\n[系统提示] 工具执行失败, 请检查参数是否正确。"
                            "如果是文件不存在, 先用 file_list 确认路径; "
                            "如果是参数错误, 修正后重试; 连续失败请换一种方法。"
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
                model=self.model,
                temperature=self.temperature,
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
                    # 错误时附加重试提示
                    if tool_result.startswith("错误") or tool_result.startswith("失败"):
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
