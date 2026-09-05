"""
ReviewerAgent —— 代码审查员角色
职责: 审查代码, 找 bug、安全问题、性能问题、规范问题, 只看不写
"""
import re
from core.agents.base_agent import BaseAgent, AgentContext


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    system_prompt = """你是 Reviewer, 严格的代码审查员。

职责: 审查代码, 找出所有问题。你只看不写, 绝对不修改代码。

审查维度:
1. 正确性: 有没有 bug、逻辑错误、边界情况遗漏
2. 安全性: 有没有注入风险、敏感信息泄露、权限问题
3. 性能: 有没有明显的性能问题、内存泄漏
4. 规范: 是否符合 PEP8、有没有类型注解、命名是否清晰
5. 可维护性: 有没有重复代码、耦合是否过高、注释是否充分

输出格式:
## 审查结论
[PASS] 或 [FAIL]

## 问题清单
(如果 FAIL, 列出每个问题, 格式: [严重程度] 问题描述 - 位置 - 修改建议)

## 优点
(代码做得好的地方, 可选)
"""
    allowed_tools = [
        "file_read", "code_search", "code_find_def",
        "code_find_refs", "code_outline", "code_lint",
    ]

    def _build_task(self, ctx: AgentContext) -> str:
        return f"请审查以下代码:\n\n{ctx.code}" if ctx.code else ctx.task

    def _parse_result(self, ctx: AgentContext, result: str) -> AgentContext:
        # 判断是否通过
        passed = "[PASS]" in result and "[FAIL]" not in result
        ctx.metadata["review_passed"] = passed

        # 提取问题清单
        issues = []
        # 匹配 [严重程度] 描述 的格式
        for match in re.finditer(r"\[(高|中|低)\]\s*(.+?)(?=\n\[|\Z)", result, re.DOTALL):
            issues.append(f"[{match.group(1)}] {match.group(2).strip()}")
        if not issues and not passed:
            # 没匹配到格式, 取整个输出作为问题
            issues.append(result[:500])
        ctx.review_notes = issues
        ctx.metadata["reviewer_output"] = result
        return ctx
