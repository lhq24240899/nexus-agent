"""
技能调用工具 —— Agent 显式加载并执行某个技能的工作流
技能步骤会作为引导注入上下文, Agent 按步骤执行
"""
from tools.base_tool import BaseTool


class UseSkillTool(BaseTool):
    """加载技能工作流"""
    name = "use_skill"
    description = (
        "加载一个已有的技能工作流, 获取执行步骤引导。"
        "当任务匹配某个技能场景时调用, 例如调试 Python 错误用 debug_python_error, "
        "重构用 code_refactor, 新功能用 add_feature。"
        "调用后会返回该技能的详细执行步骤, 请严格按步骤执行。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "技能名称 (debug_python_error / code_refactor / add_feature 等)",
            },
            "list_all": {
                "type": "boolean",
                "description": "设为 true 则列出所有可用技能, 不执行特定技能",
                "default": False,
            },
        },
        "required": [],
    }

    _skill_manager = None

    @classmethod
    def bind(cls, skill_manager):
        cls._skill_manager = skill_manager

    def execute(self, skill_name: str = "", list_all: bool = False, **kwargs) -> str:
        if not self._skill_manager:
            return "错误: 技能管理器未初始化"
        if list_all or not skill_name:
            skills = self._skill_manager.list_skills()
            if not skills:
                return "暂无可用技能"
            lines = ["=== 可用技能 ==="]
            for s in skills:
                lines.append(
                    f"- {s['name']}: {s['description']} "
                    f"({s['steps']}步, 标签: {', '.join(s['tags'])})"
                )
            return "\n".join(lines)
        skill = self._skill_manager.skills.get(skill_name)
        if not skill:
            available = ", ".join(self._skill_manager.skills.keys())
            return f"错误: 技能 '{skill_name}' 不存在。可用: {available}"
        return skill.to_workflow_text()
