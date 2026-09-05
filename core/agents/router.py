"""
AgentRouter —— 动态多 Agent 路由器
根据意图识别结果, 选择单 agent 或 multi-agent 流水线执行
"""
from dataclasses import dataclass
from typing import Any

from core.agents.intent_analyzer import IntentAnalyzer, Intent
from core.agents.pipeline import PipelineExecutor, PipelineResult
from core.agents.coder_agent import CoderAgent
from core.agents.reviewer_agent import ReviewerAgent
from core.agents.tester_agent import TesterAgent
from utils.logger import logger


@dataclass
class RouteResult:
    """路由执行结果"""
    handled: bool                    # 是否被路由器处理 (False=走原单agent逻辑)
    strategy: str = ""               # 使用的策略
    intent: Intent | None = None
    pipeline_result: PipelineResult | None = None
    output: str = ""                 # 最终输出文本

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "strategy": self.strategy,
            "intent": self.intent.to_dict() if self.intent else None,
            "pipeline_result": self.pipeline_result.to_dict() if self.pipeline_result else None,
            "output": self.output,
        }


class AgentRouter:
    """动态路由器: 根据意图选择执行策略"""

    def __init__(self, tool_manager=None):
        self.intent_analyzer = IntentAnalyzer()
        self.tool_manager = tool_manager
        # 懒加载 agent 池 (避免初始化时就创建所有 agent)
        self._agent_pool: dict[str, Any] = {}

    def route(self, task: str, mode: str = "work") -> RouteResult:
        """
        分析任务并路由到合适的执行策略

        Returns:
            RouteResult: 如果 handled=False, 调用方应走原单 agent 逻辑
        """
        # 1. 意图识别
        intent = self.intent_analyzer.analyze(task)
        logger.log("router", "意图识别",
                   f"type={intent.task_type}, complexity={intent.complexity}, "
                   f"multi_agent={intent.needs_multi_agent}")

        # 2. 聊天模式一律单 agent
        if mode == "chat":
            return RouteResult(handled=False, strategy="chat_single", intent=intent)

        # 3. 根据意图路由
        if not intent.needs_multi_agent:
            return RouteResult(handled=False, strategy="single_agent", intent=intent)

        # 编码任务 -> 编码流水线
        if intent.task_type == "coding" and intent.needs_multi_agent:
            return self._run_coding_pipeline(task, intent)

        # 其他 multi-agent 任务暂时回退单 agent (后续扩展)
        logger.log("router", "暂不支持的 multi-agent 类型", intent.task_type)
        return RouteResult(handled=False, strategy="fallback_single", intent=intent)

    def _run_coding_pipeline(self, task: str, intent: Intent) -> RouteResult:
        """执行编码流水线: coder -> reviewer -> tester"""
        logger.log("router", "执行编码流水线", task[:50])

        coder = self._get_agent("coder")
        reviewer = self._get_agent("reviewer")
        tester = self._get_agent("tester")

        pipeline = PipelineExecutor(
            agents=[coder, reviewer, tester],
            max_rounds=3,
            auto_fix=True,
            name="coding_pipeline",
        )
        result = pipeline.run(task)

        return RouteResult(
            handled=True,
            strategy="coding_pipeline",
            intent=intent,
            pipeline_result=result,
            output=result.summary(),
        )

    def _get_agent(self, name: str):
        """懒加载 agent"""
        if name not in self._agent_pool:
            if name == "coder":
                self._agent_pool[name] = CoderAgent(tool_manager=self.tool_manager)
            elif name == "reviewer":
                self._agent_pool[name] = ReviewerAgent(tool_manager=self.tool_manager)
            elif name == "tester":
                self._agent_pool[name] = TesterAgent(tool_manager=self.tool_manager)
        return self._agent_pool.get(name)
