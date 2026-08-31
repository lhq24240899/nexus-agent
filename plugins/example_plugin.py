"""
Nexus 自定义工具插件示例
复制此文件, 修改类名和逻辑即可添加新工具
"""
from tools.base_tool import BaseTool


class ExamplePluginTool(BaseTool):
    """示例插件工具: 回显输入"""
    name = "example_echo"
    description = "示例插件工具, 回显输入的消息"
    params_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "要回显的消息"},
        },
        "required": ["message"],
    }

    def execute(self, message: str = "", **kwargs) -> str:
        return f"[插件回显] {message}"
