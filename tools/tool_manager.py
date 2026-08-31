"""
工具管理器 —— 参考项目中 BuiltinProviderManager 的工厂模式
从 providers.yaml 读取注册信息, 动态加载工具实例
支持 plugins/ 目录动态加载自定义工具
"""
import random
import time
import yaml
import importlib.util
import sys
from pathlib import Path
from tools.base_tool import BaseTool
from tools.web_search import WebSearchTool
from tools.code_exec import CodeExecTool
from tools.linux_terminal import LinuxTerminalTool
from tools.current_time import CurrentTimeTool
from tools.file_ops import FileReadTool, FileWriteTool, FileListTool
from tools.code_search import CodeSearchTool, IndexProjectTool
from tools.code_edit import CodeEditTool
from tools.project_analyze import ProjectAnalyzeTool
from tools.git_ops import GitTool
from tools.http_request import HttpRequestTool
from tools.news_search import NewsSearchTool
from tools.parallel_execute import ParallelExecuteTool
from tools.use_skill import UseSkillTool
from tools.cleanup import CleanupTempTool

PLUGINS_DIR = Path(__file__).parent.parent / "plugins"

# 会发起网络请求的工具, 只有这些工具的瞬时错误才自动重试
NETWORK_TOOLS = {"web_search", "http_request", "news_search"}

# 瞬时错误特征 (匹配到则认为是网络/瞬时错误, 可重试)
TRANSIENT_ERROR_MARKERS = (
    "timeout", "timed out", "connection", "connect error", "network",
    "econnreset", "econnrefused", "etimedout",
    "502", "503", "504", "429", "rate limit", "too many requests",
    "超时", "连接超时", "连接失败", "网络错误", "请求失败",
)


def _is_transient_error(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(m in t for m in TRANSIENT_ERROR_MARKERS)


class ToolManager:
    """工具工厂: 注册、查找、执行工具"""

    _tool_classes = {
        "web_search": WebSearchTool,
        "code_exec": CodeExecTool,
        "linux_terminal": LinuxTerminalTool,
        "current_time": CurrentTimeTool,
        "file_read": FileReadTool,
        "file_write": FileWriteTool,
        "file_list": FileListTool,
        "code_search": CodeSearchTool,
        "index_project": IndexProjectTool,
        "code_edit": CodeEditTool,
        "project_analyze": ProjectAnalyzeTool,
        "git": GitTool,
        "http_request": HttpRequestTool,
        "news_search": NewsSearchTool,
        "parallel_execute": ParallelExecuteTool,
        "use_skill": UseSkillTool,
        "cleanup_temp": CleanupTempTool,
    }

    def __init__(self, linux_embed=None, code_index=None, profile_manager=None):
        self.tools: dict[str, BaseTool] = {}
        self._registry: list[dict] = []
        self._plugin_modules: list = []
        self._mcp_manager = None
        self.code_index = code_index
        self.profile_manager = profile_manager
        self._load_registry()
        self._init_tools(linux_embed)
        self._load_plugins()
        # 注入代码索引到相关工具
        if code_index:
            if "code_search" in self.tools:
                self.tools["code_search"].code_index = code_index
            if "index_project" in self.tools:
                self.tools["index_project"].code_index = code_index
            if "file_write" in self.tools:
                self.tools["file_write"].code_index = code_index
            if "code_edit" in self.tools:
                self.tools["code_edit"].code_index = code_index
        if profile_manager and "project_analyze" in self.tools:
            self.tools["project_analyze"].profile_manager = profile_manager

    def _load_registry(self):
        yaml_path = Path(__file__).parent / "providers.yaml"
        if yaml_path.exists():
            with open(yaml_path, encoding="utf-8") as f:
                self._registry = yaml.safe_load(f) or []

    def _init_tools(self, linux_embed=None):
        for item in self._registry:
            name = item.get("name")
            if name in self._tool_classes:
                if name == "linux_terminal":
                    self.tools[name] = self._tool_classes[name](linux_embed=linux_embed)
                else:
                    self.tools[name] = self._tool_classes[name]()

    def _load_plugins(self):
        """从 plugins/ 目录动态加载自定义工具"""
        if not PLUGINS_DIR.exists():
            PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            self._write_example_plugin()
        for py_file in sorted(PLUGINS_DIR.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                self._load_plugin_file(py_file)
            except Exception as e:
                print(f"[Plugin] 加载失败 {py_file.name}: {e}")

    def _load_plugin_file(self, py_file: Path):
        module_name = f"nexus_plugin_{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if not spec or not spec.loader:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self._plugin_modules.append(module)
        # 查找继承 BaseTool 的类并实例化
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and issubclass(attr, BaseTool)
                    and attr is not BaseTool):
                try:
                    instance = attr()
                    if instance.name not in self.tools:
                        self.tools[instance.name] = instance
                        print(f"[Plugin] 已加载工具: {instance.name}")
                except Exception as e:
                    print(f"[Plugin] 实例化 {attr_name} 失败: {e}")

    def _write_example_plugin(self):
        example = '''"""
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
'''
        (PLUGINS_DIR / "example_plugin.py").write_text(
            example, encoding="utf-8"
        )

    def reload_plugins(self) -> str:
        """热重载插件 (运行时重新加载 plugins/ 目录)"""
        # 移除已加载的插件工具
        plugin_names = [
            name for name, tool in self.tools.items()
            if tool.__class__.__module__.startswith("nexus_plugin_")
        ]
        for name in plugin_names:
            del self.tools[name]
        # 移除模块缓存
        for mod in self._plugin_modules:
            if mod.__name__ in sys.modules:
                del sys.modules[mod.__name__]
        self._plugin_modules.clear()
        # 重新加载
        self._load_plugins()
        new_count = len([
            t for t in self.tools.values()
            if t.__class__.__module__.startswith("nexus_plugin_")
        ])
        return f"插件已重载, 当前 {new_count} 个插件工具"

    def get_tool(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def set_mcp_manager(self, mcp_manager):
        """注入 MCP 管理器, MCP 工具将自动纳入工具列表"""
        self._mcp_manager = mcp_manager

    def get_functions(self) -> list[dict]:
        funcs = [t.to_function() for t in self.tools.values()]
        # 加入 MCP 工具
        if self._mcp_manager:
            funcs.extend(self._mcp_manager.get_tools())
        return funcs

    def execute(self, name: str, **kwargs) -> str:
        # MCP 工具优先路由 (MCP 工具也可能走网络, 纳入重试)
        is_mcp = self._mcp_manager and self._mcp_manager.has_tool(name)
        if is_mcp:
            raw_exec = lambda: self._mcp_manager.execute(name, **kwargs)
        else:
            tool = self.tools.get(name)
            if not tool:
                return f"错误: 工具 '{name}' 不存在"
            raw_exec = lambda: tool.execute(**kwargs)

        # 第一次执行
        try:
            result = raw_exec()
        except Exception as e:
            result = f"错误: {type(e).__name__}: {e}"

        # 仅网络类工具的瞬时错误自动重试一次
        if (name in NETWORK_TOOLS or is_mcp) and _is_transient_error(result):
            time.sleep(0.5 + random.random() * 0.5)  # 0.5~1.0s 随机退避
            try:
                result = raw_exec()
            except Exception as e:
                result = f"错误(重试后仍失败): {type(e).__name__}: {e}"

        return result

    def list_tools(self) -> list[dict]:
        tools = [
            {"name": t.name, "description": t.description}
            for t in self.tools.values()
        ]
        if self._mcp_manager:
            for mcp_tool in self._mcp_manager._mcp_tools:
                tools.append({
                    "name": mcp_tool["name"],
                    "description": f"[MCP] {mcp_tool['description']}",
                })
        return tools
