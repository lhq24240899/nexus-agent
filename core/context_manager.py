"""
上下文管理器 —— 防止对话撑爆模型上下文窗口
三层防护:
1. 工具结果自动截断 (单条不超过 MAX_TOOL_RESULT_CHARS)
2. 实时 token 计数 (每次 API 调用前估算)
3. 超阈值自动压缩 (超过 80% 时, 把早期对话压缩成摘要)
"""
import json
from typing import Optional

# 模型上下文窗口 (token)
CONTEXT_WINDOWS = {
    "deepseek-chat": 128000,
    "deepseek-v4-flash": 128000,
    "deepseek-v4-flash-vision-exp": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "claude-sonnet-4": 200000,
}
DEFAULT_CONTEXT_WINDOW = 128000

# 压缩阈值 (80%)
COMPRESS_THRESHOLD = 0.8

# 单条工具结果最大字符数
MAX_TOOL_RESULT_CHARS = 3000

# 压缩后保留最近几轮工具调用
KEEP_RECENT_ROUNDS = 2

# 尝试导入 tiktoken 做精确计数, 失败则用字符估算
try:
    import tiktoken
    _encoder = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        if not text:
            return 0
        return len(_encoder.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:
        """降级: 中文 1.5 token/字, 英文 0.25 token/字"""
        if not text:
            return 0
        cn = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        en = len(text) - cn
        return int(cn * 1.5 + en * 0.3)


def count_messages_tokens(messages: list) -> int:
    """估算 messages 数组的总 token数 (兼容 dict 和 pydantic ChatCompletionMessage)"""
    total = 0
    for msg in messages:
        # 兼容 dict 和 pydantic 对象
        if isinstance(msg, dict):
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []
        else:
            content = getattr(msg, "content", "") or ""
            tool_calls = getattr(msg, "tool_calls", None) or []
        total += count_tokens(content)
        # 工具调用也占 token
        for tc in tool_calls:
            if isinstance(tc, dict):
                args = tc.get("function", {}).get("arguments", "")
            else:
                func = getattr(tc, "function", None)
                args = getattr(func, "arguments", "") if func else ""
            total += count_tokens(args)
            total += 10  # 工具名和结构开销
        total += 4  # 每条消息的角色标签开销
    return total


def truncate_tool_result(result: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """截断工具结果, 保留开头和结尾"""
    if not result or len(result) <= max_chars:
        return result
    keep_head = max_chars * 2 // 3
    keep_tail = max_chars - keep_head - 50
    return (
        result[:keep_head]
        + f"\n\n... [已截断, 原长度 {len(result)} 字符, 省略中间部分] ...\n\n"
        + result[-keep_tail:]
    )


class ContextManager:
    """上下文压缩管理器"""

    def __init__(self, model: str = "deepseek-chat", llm_client=None):
        self.model = model
        self.context_window = CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)
        self.threshold = int(self.context_window * COMPRESS_THRESHOLD)
        self.llm_client = llm_client
        self.compress_count = 0

    def should_compress(self, messages: list[dict]) -> bool:
        """是否需要压缩"""
        return count_messages_tokens(messages) > self.threshold

    def compress(self, messages: list[dict]) -> list[dict]:
        """
        压缩 messages:
        - 保留 system 和最新的 user 消息
        - 保留最近 KEEP_RECENT_ROUNDS 轮工具调用
        - 更早的内容用 LLM 总结成一条摘要
        """
        if len(messages) <= 4:  # system + user + 最少一轮, 不需要压缩
            return messages

        # 找到最新 user 消息的位置
        last_user_idx = max(
            i for i, m in enumerate(messages) if m["role"] == "user"
        )

        # 保留: system + user + 最近 N 轮 (assistant+tool 对)
        preserved = [messages[0], messages[last_user_idx]]  # system + user

        # 从后往前收集最近的工具调用轮
        recent_rounds = []
        i = len(messages) - 1
        rounds_collected = 0
        while i > last_user_idx and rounds_collected < KEEP_RECENT_ROUNDS:
            if messages[i]["role"] == "tool":
                # 找到对应的 assistant 消息
                j = i - 1
                while j > last_user_idx and messages[j]["role"] != "assistant":
                    j -= 1
                if j > last_user_idx:
                    recent_rounds.insert(0, messages[j])
                    recent_rounds.insert(1, messages[i])
                    rounds_collected += 1
                    i = j - 1
                    continue
            i -= 1

        # 需要压缩的中间部分
        to_compress = messages[last_user_idx + 1:i + 1] if i > last_user_idx else []

        if not to_compress:
            return messages  # 没有可压缩的

        # 用 LLM 总结
        summary = self._summarize(to_compress)
        if summary:
            preserved.append({
                "role": "system",
                "content": f"【之前的对话摘要】\n{summary}\n\n(以上为早期对话的压缩摘要, 后续是最新内容)",
            })
        preserved.extend(recent_rounds)

        self.compress_count += 1
        return preserved

    def _summarize(self, messages: list[dict]) -> str:
        """用 LLM 总结一段对话"""
        if not self.llm_client:
            # 没有 LLM 客户端, 做简单截断摘要
            text = "\n".join(
                f"[{m['role']}] {str(m.get('content', ''))[:200]}"
                for m in messages[:10]
            )
            return f"(无LLM客户端, 保留前10条摘要)\n{text[:1500]}"

        try:
            raw = "\n".join(
                f"[{m['role']}] {str(m.get('content', ''))[:500]}"
                for m in messages
            )[:4000]
            resp = self.llm_client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": (
                        "请用简洁的中文总结以下对话的关键信息(做了什么、发现了什么、结论是什么), "
                        "不超过300字, 只输出摘要不要解释:\n\n" + raw
                    ),
                }],
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return "(摘要生成失败, 已省略早期对话)"
