"""
集成测试: 验证流式决策在工具上限时不会输出中途过程文本
模拟 LLM 客户端, 不调用真实 API
"""
import sys
sys.path.insert(0, '.')

from unittest.mock import MagicMock, patch
from core.decision_core import DecisionCore


class MockChunk:
    """模拟流式 API 的 chunk"""
    def __init__(self, content=None, tool_calls=None, usage=None):
        self.choices = [MagicMock()]
        self.choices[0].delta = MagicMock()
        self.choices[0].delta.content = content
        self.choices[0].delta.tool_calls = tool_calls
        self.usage = usage


class MockToolCallDelta:
    def __init__(self, index, call_id, name, args):
        self.index = index
        self.id = call_id
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = args


def make_mock_client(iteration_behaviors):
    """
    创建 mock 客户端, 按迭代次数返回不同响应
    iteration_behaviors: list of (type, content/tool_calls)
    type: 'text' -> 返回文本, 'tools' -> 返回工具调用
    """
    call_count = [0]

    def mock_create(**kwargs):
        idx = call_count[0]
        call_count[0] += 1
        behavior = iteration_behaviors[min(idx, len(iteration_behaviors) - 1)]

        if behavior[0] == 'tools':
            tool_name, tool_args = behavior[1], behavior[2]
            tc = MockToolCallDelta(0, f"call_{idx}", tool_name, tool_args)
            chunks = [MockChunk(content=None, tool_calls=[tc])]
        else:
            text = behavior[1]
            # 分成多个 chunk 模拟流式
            chunks = [MockChunk(content=text[i:i+5], usage=None) for i in range(0, len(text), 5)]
            chunks[-1].usage = MagicMock()
            chunks[-1].usage.prompt_tokens = 100
            chunks[-1].usage.completion_tokens = 50

        def gen():
            for chunk in chunks:
                yield chunk
        return gen()

    client = MagicMock()
    client.chat.completions.create = mock_create
    return client


def test_streaming_at_limit():
    """测试: 达到工具上限时, 中途文本不输出, 强制总结后输出结论"""
    print("=" * 60)
    print("测试1: 工具上限时中途文本被拦截, 输出强制总结")
    print("=" * 60)

    # 模拟行为:
    # 迭代0-11: 工具调用 (12次, 达到max_tool_calls=12)
    # 迭代12: 中途文本 "Now let me verify the script works by running it."
    # 迭代13: (注入提醒后) 仍返回中途文本 (测试强制总结)
    behaviors = []
    for i in range(12):
        behaviors.append(('tools', 'code_exec', '{"code": "print(1)"}'))
    behaviors.append(('text', 'Now let me verify the script works by running it.'))
    behaviors.append(('text', 'Let me check the output.'))  # 提醒后仍中途

    mock_client = make_mock_client(behaviors)

    # mock tool_manager
    mock_tm = MagicMock()
    mock_tm.execute.return_value = "执行成功"
    mock_tm.get_tool.return_value = MagicMock()
    mock_tm.list_tools.return_value = []

    with patch.object(DecisionCore, '__init__', lambda self: None):
        core = DecisionCore()
        core.client = mock_client
        core.model = "test-model"
        core.mode = "work"
        core.override_model = None
        core.max_tool_calls = 12
        core.temperature = 0.7
        core.last_tools_used = []
        core.last_tool_errors = 0
        core.consecutive_code_failures = 0
        core.tool_manager = mock_tm
        core.error_diagnoser = MagicMock()
        core.error_diagnoser.diagnose.return_value = {"files_examined": 0}
        core.error_diagnoser.to_context_string.return_value = ""
        core.error_diagnoser.reset = MagicMock()

        from core.context_manager import ContextManager
        core.ctx_manager = ContextManager(model="test-model", llm_client=None)

        # mock _force_final_summary 返回固定结论
        core._force_final_summary = lambda task, msgs: "【结论】已完成 playwright demo, 文件在 D:/demo/, 运行 python main.py"

        # 执行流式决策
        events = list(core.decide_stream("测试任务", "上下文", ""))

    # 收集所有 token 输出
    tokens = [e["content"] for e in events if e.get("type") == "token"]
    full_output = "".join(tokens)
    replace_events = [e for e in events if e.get("type") == "replace_content"]
    done_events = [e for e in events if e.get("type") == "done"]

    print(f"  总事件数: {len(events)}")
    print(f"  token 事件数: {len(tokens)}")
    print(f"  replace_content 事件数: {len(replace_events)}")
    print(f"  done 事件数: {len(done_events)}")
    print(f"  最终输出: {full_output[:80]}")
    if done_events:
        print(f"  done result: {done_events[0].get('result', '')[:80]}")

    # 验证: 最终输出不包含中途文本
    assert "Now let me verify" not in full_output, "FAIL: 中途文本出现在输出中!"
    assert "Let me check" not in full_output, "FAIL: 第二条中途文本出现在输出中!"
    # 验证: 最终输出包含结论
    if done_events:
        result = done_events[0].get("result", "")
        assert "结论" in result or "已完成" in result, f"FAIL: 最终结果不是结论: {result[:60]}"
    print("  ✅ PASS: 中途文本被拦截, 输出为结论")


def test_mid_process_detection():
    """测试中途检测函数"""
    print("\n" + "=" * 60)
    print("测试2: 中英文中途检测")
    print("=" * 60)

    with patch.object(DecisionCore, '__init__', lambda self: None):
        core = DecisionCore()

    cases = [
        ("Now let me verify the script works by running it.", True),
        ("Let me check the file first.", True),
        ("Next, I'll install the dependencies.", True),
        ("现在写 README 和使用说明。", True),
        ("接下来配置环境变量。", True),
        ("Done. Created 3 files: main.py, requirements.txt, README.md.", False),
        ("已完成 playwright demo 创建, 文件在 D:/demo/", False),
        ("1+1=2", False),
        ("The script runs successfully. Output: Hello World.", False),
        ("I'm going to test this now.", True),
    ]

    all_pass = True
    for text, expected in cases:
        result = core._is_mid_process(text)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            all_pass = False
        print(f"  [{status}] {'中途' if expected else '结论'}: {text[:50]}")

    assert all_pass, "FAIL: 中途检测有错误!"
    print("  ✅ 全部通过")


def test_normal_streaming():
    """测试: 正常任务(未达上限)正常输出"""
    print("\n" + "=" * 60)
    print("测试3: 正常任务(未达上限)正常输出结论")
    print("=" * 60)

    behaviors = [
        ('tools', 'file_write', '{"path":"test.py","content":"print(1)"}'),
        ('text', '已完成。创建了 test.py, 运行 python test.py 即可。'),
    ]

    mock_client = make_mock_client(behaviors)
    mock_tm = MagicMock()
    mock_tm.execute.return_value = "已写入"
    mock_tm.get_tool.return_value = MagicMock()
    mock_tm.list_tools.return_value = []

    with patch.object(DecisionCore, '__init__', lambda self: None):
        core = DecisionCore()
        core.client = mock_client
        core.model = "test-model"
        core.mode = "work"
        core.override_model = None
        core.max_tool_calls = 12
        core.temperature = 0.7
        core.last_tools_used = []
        core.last_tool_errors = 0
        core.consecutive_code_failures = 0
        core.tool_manager = mock_tm
        core.error_diagnoser = MagicMock()
        core.error_diagnoser.diagnose.return_value = {"files_examined": 0}
        core.error_diagnoser.to_context_string.return_value = ""
        core.error_diagnoser.reset = MagicMock()
        from core.context_manager import ContextManager
        core.ctx_manager = ContextManager(model="test-model", llm_client=None)
        core._force_final_summary = lambda t, m: "SUMMARY"

        events = list(core.decide_stream("简单任务", "上下文", ""))

    tokens = [e["content"] for e in events if e.get("type") == "token"]
    full_output = "".join(tokens)
    done_events = [e for e in events if e.get("type") == "done"]

    print(f"  最终输出: {full_output[:60]}")
    assert "已完成" in full_output, "FAIL: 正常任务输出不对!"
    print("  ✅ PASS: 正常任务正常输出")


if __name__ == "__main__":
    test_mid_process_detection()
    test_normal_streaming()
    test_streaming_at_limit()
    print("\n" + "=" * 60)
    print("🎉 全部测试通过!")
    print("=" * 60)
