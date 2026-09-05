"""
BaseAgent —— Multi-Agent 角色基类
每个角色 agent 封装一个 DecisionCore, 配置独立的 system prompt 和工具白名单
"""
from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from core.decision_core import DecisionCore


@dataclass
class AgentContext:
    """agent 之间传递的共享上下文"""
    task: str
    code: str = ""
    files: dict[str, str] = field(default_factory=dict)
    review_notes: list[str] = field(default_factory=list)
    test_results: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""


class BaseAgent(ABC):
    """所有角色 agent 的基类"""

    name: str = ""
    system_prompt: str = ""
    allowed_tools: list[str] | None = None  # None=全部工具, []=无工具

    def __init__(self, tool_manager=None):
        self.core = DecisionCore(
            tool_manager=tool_manager,
            custom_system_prompt=self.system_prompt,
        )
        self.tool_manager = tool_manager

    def run(self, ctx: AgentContext) -> AgentContext:
        """
        执行任务, 返回更新后的上下文

        Args:
            ctx: 共享上下文, 包含任务和前序 agent 的产出

        Returns:
            更新后的 ctx
        """
        # 构建任务描述: 原始任务 + 上下文
        task = self._build_task(ctx)
        # 调用决策核心
        result = self.core.decide(
            task=task,
            context=self._build_context(ctx),
            history_text="",
            allowed_tools=self.allowed_tools,
        )
        ctx.raw_output = result
        # 子类可以重写 _parse_result 提取结构化信息
        ctx = self._parse_result(ctx, result)
        return ctx

    def _build_task(self, ctx: AgentContext) -> str:
        """构建发给决策核心的任务描述, 子类可重写"""
        return ctx.task

    def _build_context(self, ctx: AgentContext) -> str:
        """构建上下文, 子类可重写以注入前序 agent 的产出"""
        parts = []
        if ctx.code:
            parts.append(f"【当前代码】\n{ctx.code}")
        if ctx.review_notes:
            parts.append("【审查意见】\n" + "\n".join(f"- {n}" for n in ctx.review_notes))
        if ctx.test_results:
            parts.append(f"【测试结果】\n{ctx.test_results}")
        return "\n\n".join(parts) if parts else "(无前置上下文)"

    def _parse_result(self, ctx: AgentContext, result: str) -> AgentContext:
        """解析决策核心输出, 子类可重写提取结构化信息"""
        return ctx
