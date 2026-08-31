"""当前时间工具"""
import time
from tools.base_tool import BaseTool


class CurrentTimeTool(BaseTool):
    name = "current_time"
    description = "获取当前日期和时间。当需要知道现在是什么时间时使用。"
    params_schema = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs) -> str:
        return time.strftime("当前时间: %Y-%m-%d %H:%M:%S (%A)")
