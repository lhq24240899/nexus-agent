"""
工具基类 —— 参考项目中 LangChain BaseTool 的设计, 简化实现
每个工具: name / description / params schema / execute()
"""
from abc import ABC, abstractmethod
from pydantic import BaseModel


class BaseTool(ABC):
    """所有工具的基类"""

    name: str = ""
    description: str = ""
    params_schema: dict = {}  # OpenAI function calling 格式

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """执行工具, 返回结果字符串"""
        pass

    def to_function(self) -> dict:
        """转为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }
