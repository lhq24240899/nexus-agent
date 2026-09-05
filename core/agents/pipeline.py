"""
PipelineExecutor —— 串行流水线执行器
按顺序执行一组 agent, 支持审查/测试不通过时循环修复
"""
import time
from dataclasses import dataclass, field
from typing import Any

from core.agents.base_agent import BaseAgent, AgentContext
from utils.logger import logger


@dataclass
class PipelineResult:
    """流水线执行结果"""
    success: bool
    code: str = ""
    review_notes: list[str] = field(default_factory=list)
    test_results: list[dict] = field(default_factory=list)
    review_passed: bool = False
    tests_passed: bool = False
    rounds: int = 0
    total_time_s: float = 0.0
    agent_outputs: dict[str, str] = field(default_factory=dict)
    error: str = ""
    stopped: bool = False
    cost_estimate: float = 0.0  # 估算token成本(元)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "code": self.code,
            "review_notes": self.review_notes,
            "test_results": self.test_results,
            "review_passed": self.review_passed,
            "tests_passed": self.tests_passed,
            "rounds": self.rounds,
            "total_time_s": round(self.total_time_s, 2),
            "agent_outputs": self.agent_outputs,
            "error": self.error,
            "stopped": self.stopped,
            "cost_estimate": round(self.cost_estimate, 4),
        }

    def summary(self) -> str:
        """生成人类可读的摘要"""
        lines = [f"## 流水线结果 ({'成功' if self.success else '未完全通过'})"]
        lines.append(f"- 轮次: {self.rounds}")
        lines.append(f"- 耗时: {self.total_time_s:.1f}s")
        lines.append(f"- 审查: {'通过' if self.review_passed else '未通过'}")
        lines.append(f"- 测试: {'通过' if self.tests_passed else '未通过'}")
        if self.review_notes:
            lines.append(f"- 审查意见: {len(self.review_notes)} 条")
        if self.code:
            lines.append("\n## 最终代码\n```python\n" + self.code[:2000] + "\n```")
        if self.stopped:
            lines.append("- ⚠️ 任务已被用户停止")
        if self.error:
            lines.append(f"\n## 错误\n{self.error}")
        return "\n".join(lines)


class PipelineExecutor:
    """串行流水线执行器"""

    def __init__(
        self,
        agents: list[BaseAgent],
        max_rounds: int = 3,
        auto_fix: bool = True,
        name: str = "pipeline",
        stop_event=None,
        timeout_s: float = 300.0,
    ):
        self.agents = agents
        self.max_rounds = max_rounds
        self.auto_fix = auto_fix
        self.name = name
        self.stop_event = stop_event
        self.timeout_s = timeout_s
        # 字典缓存 agent, 避免每次遍历
        self._agent_map = {a.name: a for a in agents}

    def _check_stop_or_timeout(self, elapsed: float) -> bool:
        """检查是否需要停止: 用户stop或超时. 返回True表示应停止"""
        if self.stop_event and self.stop_event.is_set():
            return True
        if elapsed > self.timeout_s:
            logger.log("pipeline", self.name, f"超时({self.timeout_s}s), 终止流水线")
            return True
        return False

    def run(self, task: str) -> PipelineResult:
        """
        执行流水线

        标准编码流水线: coder -> reviewer -> (循环修复) -> tester -> (循环修复)
        支持 stop_event 中断和超时控制
        """
        start = time.time()
        ctx = AgentContext(task=task)
        result = PipelineResult(success=False)
        actual_rounds = 0

        def _elapsed():
            return time.time() - start

        try:
            # 第1步: 编码 (只跑一次)
            coder = self._get_agent("coder")
            if coder:
                if self._check_stop_or_timeout(_elapsed()):
                    result.stopped = True
                    return self._finalize(result, ctx, start, actual_rounds)
                logger.log("pipeline", self.name, "Coder 开始编码")
                ctx = coder.run(ctx)
                result.agent_outputs["coder"] = ctx.raw_output
                ctx.code = ctx.code or ctx.raw_output
                result.cost_estimate += len(ctx.raw_output) * 0.00001  # 粗略估算

            # 第2步: 审查循环
            reviewer = self._get_agent("reviewer")
            if reviewer and ctx.code:
                for round_num in range(1, self.max_rounds + 1):
                    if self._check_stop_or_timeout(_elapsed()):
                        result.stopped = True
                        break
                    actual_rounds = round_num
                    logger.log("pipeline", self.name, f"Reviewer 第{round_num}轮审查")
                    ctx = reviewer.run(ctx)
                    result.agent_outputs[f"reviewer_round{round_num}"] = ctx.raw_output
                    result.review_notes = ctx.review_notes
                    result.review_passed = ctx.metadata.get("review_passed", False)
                    result.cost_estimate += len(ctx.raw_output) * 0.00001

                    if result.review_passed:
                        logger.log("pipeline", self.name, "审查通过")
                        break
                    if not self.auto_fix:
                        break
                    # 回去修
                    if coder and round_num < self.max_rounds:
                        if self._check_stop_or_timeout(_elapsed()):
                            result.stopped = True
                            break
                        logger.log("pipeline", self.name, f"审查未通过, Coder 第{round_num}轮修复")
                        ctx = coder.run(ctx)
                        result.agent_outputs[f"coder_fix_round{round_num}"] = ctx.raw_output
                        result.cost_estimate += len(ctx.raw_output) * 0.00001

            # 第3步: 测试循环
            tester = self._get_agent("tester")
            if tester and ctx.code and not result.stopped:
                for round_num in range(1, self.max_rounds + 1):
                    if self._check_stop_or_timeout(_elapsed()):
                        result.stopped = True
                        break
                    actual_rounds = max(actual_rounds, round_num)
                    logger.log("pipeline", self.name, f"Tester 第{round_num}轮测试")
                    ctx = tester.run(ctx)
                    result.agent_outputs[f"tester_round{round_num}"] = ctx.raw_output
                    result.test_results = ctx.test_results
                    result.tests_passed = ctx.metadata.get("tests_passed", False)
                    result.cost_estimate += len(ctx.raw_output) * 0.00001

                    if result.tests_passed:
                        logger.log("pipeline", self.name, "测试通过")
                        break
                    if not self.auto_fix:
                        break
                    # 回去修
                    if coder and round_num < self.max_rounds:
                        if self._check_stop_or_timeout(_elapsed()):
                            result.stopped = True
                            break
                        logger.log("pipeline", self.name, f"测试失败, Coder 第{round_num}轮修复")
                        ctx = coder.run(ctx)
                        result.agent_outputs[f"coder_testfix_round{round_num}"] = ctx.raw_output
                        result.cost_estimate += len(ctx.raw_output) * 0.00001

            # 汇总
            result.code = ctx.code
            result.rounds = actual_rounds  # 实际轮次, 不是最大轮次
            result.success = result.review_passed and result.tests_passed and not result.stopped
            # 如果没有 reviewer/tester, 只要有 code 就算成功
            if not reviewer and not tester and ctx.code and not result.stopped:
                result.success = True

        except Exception as e:
            result.error = str(e)
            logger.log("pipeline", self.name, f"流水线异常: {e}")

        return self._finalize(result, ctx, start, actual_rounds)

    def _finalize(self, result, ctx, start, actual_rounds):
        """统一收尾: 记录耗时和日志"""
        result.total_time_s = time.time() - start
        if result.rounds == 0:
            result.rounds = actual_rounds
        stop_str = " (已停止)" if result.stopped else ""
        logger.log("pipeline", self.name,
                   f"完成{stop_str}: success={result.success}, rounds={result.rounds}, "
                   f"time={result.total_time_s:.1f}s, cost≈{result.cost_estimate:.4f}元")
        return result

    def _get_agent(self, name: str) -> BaseAgent | None:
        """按名称获取 agent (字典缓存, O(1))"""
        return self._agent_map.get(name)
