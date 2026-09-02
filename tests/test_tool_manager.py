"""
工具管理器测试 —— 工具降权机制
验证: 工具使用统计、失败率计算、高失败率工具识别、按成功率排序
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.tool_manager import ToolManager


def test_tool_stats_initial():
    """初始状态: 无统计数据, 失败率为0"""
    tm = ToolManager(linux_embed=None)
    assert tm.get_tool_failure_rate("web_search") == 0.0
    assert tm.get_unreliable_tools() == set()


def test_tool_stats_record_success():
    """成功调用: success+1, 失败率为0"""
    tm = ToolManager(linux_embed=None)
    tm._record_tool_result("code_exec", "Hello World")
    tm._record_tool_result("code_exec", "42")
    stats = tm._tool_stats["code_exec"]
    assert stats["success"] == 2
    assert stats["fail"] == 0
    assert tm.get_tool_failure_rate("code_exec") == 0.0  # 调用<3, 返回0


def test_tool_stats_record_failure():
    """失败调用: fail+1"""
    tm = ToolManager(linux_embed=None)
    tm._record_tool_result("web_search", "错误: 网络超时")
    stats = tm._tool_stats["web_search"]
    assert stats["success"] == 0
    assert stats["fail"] == 1


def test_tool_failure_rate_calculation():
    """失败率计算: 3次调用, 2次失败 = 66.7%"""
    tm = ToolManager(linux_embed=None)
    tm._record_tool_result("mcp_test", "错误: 无响应")
    tm._record_tool_result("mcp_test", "错误: 无响应")
    tm._record_tool_result("mcp_test", "正常结果")
    rate = tm.get_tool_failure_rate("mcp_test")
    assert abs(rate - 2/3) < 0.01


def test_tool_failure_rate_min_calls():
    """调用次数<3时返回0, 避免误判"""
    tm = ToolManager(linux_embed=None)
    tm._record_tool_result("test_tool", "错误: 失败")
    tm._record_tool_result("test_tool", "错误: 失败")
    # 只有2次调用, 即使全失败也返回0
    assert tm.get_tool_failure_rate("test_tool") == 0.0


def test_unreliable_tools_threshold():
    """高失败率工具识别: 失败率>50%且调用>=3"""
    tm = ToolManager(linux_embed=None)
    # 工具A: 3次全失败 -> 不可靠
    for _ in range(3):
        tm._record_tool_result("tool_a", "错误: 失败")
    # 工具B: 3次全成功 -> 可靠
    for _ in range(3):
        tm._record_tool_result("tool_b", "成功")
    # 工具C: 2次失败1次成功(66.7%) -> 不可靠
    tm._record_tool_result("tool_c", "错误")
    tm._record_tool_result("tool_c", "错误")
    tm._record_tool_result("tool_c", "成功")

    unreliable = tm.get_unreliable_tools(threshold=0.5)
    assert "tool_a" in unreliable
    assert "tool_b" not in unreliable
    assert "tool_c" in unreliable


def test_error_detection():
    """错误结果识别: 以错误/失败开头算失败"""
    tm = ToolManager(linux_embed=None)
    error_cases = [
        "错误: 工具不存在",
        "失败: 网络超时",
        "[子代理异常] crash",
        "错误(重试后仍失败): timeout",
    ]
    for case in error_cases:
        tm._record_tool_result("test_tool", case)
    assert tm._tool_stats["test_tool"]["fail"] == 4

    # 正常结果不算失败
    tm._record_tool_result("test_tool", "这是正常的输出结果")
    assert tm._tool_stats["test_tool"]["success"] == 1


def test_get_functions_sorted_by_failure_rate():
    """get_functions按失败率升序排列: 成功率高的排前面"""
    tm = ToolManager(linux_embed=None)
    # 给工具设置不同的失败率
    for _ in range(3):
        tm._record_tool_result("code_exec", "成功")
    for _ in range(3):
        tm._record_tool_result("web_search", "错误: 失败")

    funcs = tm.get_functions()
    names = [f["function"]["name"] for f in funcs]
    # code_exec(成功率高)应该在web_search(失败率高)前面
    if "code_exec" in names and "web_search" in names:
        assert names.index("code_exec") < names.index("web_search")


if __name__ == "__main__":
    test_tool_stats_initial()
    test_tool_stats_record_success()
    test_tool_stats_record_failure()
    test_tool_failure_rate_calculation()
    test_tool_failure_rate_min_calls()
    test_unreliable_tools_threshold()
    test_error_detection()
    test_get_functions_sorted_by_failure_rate()
    print("所有工具管理器测试通过!")
