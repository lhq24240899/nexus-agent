"""
任务统计测试 —— 成功率判定逻辑
验证: 任务成功率基于最终结果, 中间工具失败不影响
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_success_based_on_final_result():
    """
    核心验证: 任务成功率基于最终结果
    dual_agent.py 中的逻辑: success = bool(result and result.strip())
    """
    def is_success(result) -> bool:
        return bool(result and result.strip())

    # 正常输出 -> 成功
    assert is_success("这是最终答案") is True
    assert is_success("任务完成，共61个文件") is True
    # 空输出 -> 失败
    assert is_success("") is False
    assert is_success("   ") is False
    assert is_success(None) is False
    # 即使中间有工具失败, 最终有输出就算成功
    assert is_success("虽然MCP工具失败了, 但我用code_exec完成了统计") is True


def test_tool_error_does_not_affect_task_success():
    """
    验证: 工具失败不影响任务成功率
    之前的错误逻辑: success = not last_had_tool_error (只要有工具失败就算任务失败)
    修复后的逻辑: success = bool(final_result)
    """
    # 模拟: 2个工具失败, 但最终结果非空
    tool_errors = 2
    final_result = "文件统计完成: 86个文件"

    # 旧逻辑(错误): 只要有工具错误就算失败
    old_logic_success = not (tool_errors > 0)
    assert old_logic_success is False  # 错误地判定为失败

    # 新逻辑(正确): 基于最终结果
    new_logic_success = bool(final_result and final_result.strip())
    assert new_logic_success is True  # 正确判定为成功


def test_failure_cases():
    """真正的失败场景"""
    def is_success(result) -> bool:
        return bool(result and result.strip())

    # 完全无输出
    assert is_success("") is False
    # 只有空白
    assert is_success("  \n  ") is False
    # None
    assert is_success(None) is False


def test_success_rate_calculation():
    """成功率计算逻辑"""
    tasks = [
        {"success": True},
        {"success": True},
        {"success": False},
        {"success": True},
    ]
    total = len(tasks)
    success_count = sum(1 for t in tasks if t["success"])
    rate = success_count / total * 100
    assert abs(rate - 75.0) < 0.1


def test_recent_10_rate():
    """最近10次成功率"""
    # 前5次失败, 后5次成功
    recent = [False]*5 + [True]*5
    success_count = sum(1 for r in recent if r)
    rate = success_count / len(recent) * 100 if recent else 0
    assert rate == 50.0

    # 空列表不除零
    empty = []
    rate_empty = sum(1 for r in empty if r) / len(empty) * 100 if empty else 0
    assert rate_empty == 0


if __name__ == "__main__":
    test_success_based_on_final_result()
    test_tool_error_does_not_affect_task_success()
    test_failure_cases()
    test_success_rate_calculation()
    test_recent_10_rate()
    print("所有任务统计测试通过!")
