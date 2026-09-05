"""
CoderAgent —— 编码员角色
职责: 根据需求写可运行的代码, PEP8规范, 类型注解, 错误处理
"""
import re
from core.agents.base_agent import BaseAgent, AgentContext


class CoderAgent(BaseAgent):
    name = "coder"
    system_prompt = """你是 Coder, 专业的编码员。

职责: 根据需求写出可运行、规范的代码。

要求:
1. 代码必须可运行, 写完后用 code_exec 验证
2. 遵循 PEP8 规范, 包含类型注解
3. 包含错误处理和边界情况
4. 关键逻辑加注释
5. 修改已有文件用 code_edit, 新文件用 file_write
6. 改代码前先用 code_find_def 定位, 陌生文件先用 code_outline 看结构

输出格式:
## 实现说明
(简要说明实现思路)

## 代码
(完整代码或修改内容)

## 验证结果
(code_exec 的运行结果)
"""
    allowed_tools = [
        "file_write", "code_edit", "code_exec", "file_read",
        "code_search", "code_find_def", "code_outline", "code_lint",
        "code_edit_symbol", "cleanup_temp",
    ]

    def _build_task(self, ctx: AgentContext) -> str:
        task = ctx.task
        # 如果有审查意见或测试失败, 任务变为修复
        if ctx.review_notes:
            task = f"修复以下代码问题:\n" + "\n".join(f"- {n}" for n in ctx.review_notes)
        if ctx.test_results and not ctx.metadata.get("tests_passed", True):
            task += f"\n\n修复以下测试失败:\n{ctx.test_results}"
        return task

    def _parse_result(self, ctx: AgentContext, result: str) -> AgentContext:
        # 智能提取代码块:
        # 1. 优先取包含 def/class/import 的代码块(实现代码)
        # 2. 其次取第一个代码块
        # 3. 最后取最后一个代码块(兜底)
        code_blocks = re.findall(r"```(?:python)?\n(.*?)```", result, re.DOTALL)
        if code_blocks:
            # 优先找包含实现特征的代码块
            impl_blocks = [b for b in code_blocks
                           if any(k in b for k in ["def ", "class ", "import ", "from ", "print("])]
            if impl_blocks:
                ctx.code = impl_blocks[0]  # 取第一个实现代码块
            else:
                ctx.code = code_blocks[0]  # 取第一个
        ctx.metadata["coder_output"] = result
        return ctx
