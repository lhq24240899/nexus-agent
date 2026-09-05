"""
TesterAgent —— 测试员角色
职责: 为代码写单元测试, 跑测试, 验证正确性, 只测不改
"""
import re
from core.agents.base_agent import BaseAgent, AgentContext


class TesterAgent(BaseAgent):
    name = "tester"
    system_prompt = """你是 Tester, 专业的测试员。

职责: 为代码写单元测试并运行, 验证正确性。你只测不改, 测试失败时给出具体失败信息。

测试要求:
1. 覆盖正常用例、边界用例、异常用例
2. 用 pytest 风格写测试
3. 测试文件写到 temp/ 目录, 跑完后清理
4. 用 code_exec 运行测试, 收集结果
5. 如果代码有依赖, 先确保依赖可用

输出格式:
## 测试用例
(列出测试了哪些场景)

## 运行结果
[ALL PASSED] 或 [SOME FAILED]

## 失败详情
(如果有失败, 列出失败用例和错误信息)

## 覆盖率估计
(粗略估计覆盖了哪些分支)
"""
    allowed_tools = ["file_write", "code_exec", "file_read", "cleanup_temp"]

    def _build_task(self, ctx: AgentContext) -> str:
        return f"为以下代码写单元测试并运行:\n\n{ctx.code}" if ctx.code else ctx.task

    def _parse_result(self, ctx: AgentContext, result: str) -> AgentContext:
        # 判断是否通过
        passed = "ALL PASSED" in result or "all passed" in result.lower()
        failed = "SOME FAILED" in result or "FAILED" in result
        if failed:
            passed = False
        ctx.metadata["tests_passed"] = passed

        # 提取测试结果
        test_results = []
        # 匹配测试用例
        for match in re.finditer(r"[-*]\s*(.+?)(?:\n|$)", result):
            line = match.group(1).strip()
            if any(k in line.lower() for k in ["test", "用例", "pass", "fail", "通过", "失败"]):
                test_results.append(line)
        ctx.test_results = test_results if test_results else [result[:300]]
        ctx.metadata["tester_output"] = result
        return ctx
