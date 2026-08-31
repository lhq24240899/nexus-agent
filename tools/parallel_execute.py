"""
子代理并行工具 —— 派发多个子任务并行执行
每个子任务由独立的 DecisionCore 处理, 共享工具管理器
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from tools.base_tool import BaseTool


class ParallelExecuteTool(BaseTool):
    """并行执行多个独立子任务"""
    name = "parallel_execute"
    description = (
        "并行执行多个独立的子任务。当任务可以拆分为互不依赖的子任务时使用, "
        "例如同时分析多个文件、同时查询多个信息源。每个子任务独立推理和调用工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "子任务描述列表, 每个元素是一个独立任务",
            },
            "max_workers": {
                "type": "integer",
                "description": "最大并行数 (默认3, 最多5)",
                "default": 3,
            },
        },
        "required": ["tasks"],
    }

    # 由 dual_agent 初始化后注入
    _decision_core = None
    _tool_manager = None

    @classmethod
    def bind(cls, decision_core, tool_manager):
        cls._decision_core = decision_core
        cls._tool_manager = tool_manager

    def execute(self, tasks: list = None, max_workers: int = 3, **kwargs) -> str:
        if not tasks:
            return "错误: tasks 不能为空"
        if not self._decision_core:
            return "错误: 并行工具未绑定决策核心"

        max_workers = min(max(1, max_workers), 5)
        results = {}

        def _run_subtask(idx: int, task: str) -> tuple:
            """子代理: 独立 DecisionCore 调用 (共享工具)"""
            try:
                # 子任务走快速通道, 不调秘书, 减少开销
                result = self._decision_core.decide(
                    task=task,
                    context="(子代理, 无秘书上下文)",
                    history_text="",
                )
                return idx, result
            except Exception as e:
                return idx, f"[子代理异常] {e}"

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_run_subtask, i, t): i
                for i, t in enumerate(tasks)
            }
            for future in as_completed(futures):
                idx, result = future.result()
                results[idx] = result

        # 按原始顺序输出
        lines = ["=== 并行执行结果 ==="]
        for i in range(len(tasks)):
            lines.append(f"\n--- 子任务 {i+1}: {tasks[i][:60]} ---")
            lines.append(results.get(i, "(未完成)")[:500])
        return "\n".join(lines)
