"""
AST 代码索引工具
================
基于 Python AST 的精确符号查询: 定义定位、引用追踪、文件大纲
比 code_search(关键词/grep) 更精确, 能区分"定义"和"调用"
"""
from tools.base_tool import BaseTool


class CodeFindDefTool(BaseTool):
    """符号跳转: 精确定位函数/类的定义位置"""
    name = "code_find_def"
    description = (
        "精确定位函数或类的定义位置(文件+行号+签名+docstring)。"
        "【何时用】改代码前先找函数/类在哪定义、想看函数签名和参数、确认某个符号是不是本项目定义的。"
        "【不要用】搜文本内容用code_search; 读文件用file_read。"
        "使用前需先调用 index_project 建立索引。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "函数名或类名(精确匹配)"},
        },
        "required": ["name"],
    }

    def __init__(self, ast_index=None):
        self.ast_index = ast_index

    def execute(self, name: str = "", **kwargs) -> str:
        if not name:
            return "错误: 请指定符号名"
        if not self.ast_index:
            return "错误: AST索引未初始化, 请先调用 index_project"
        defs = self.ast_index.find_definition(name)
        if not defs:
            return "未找到 '%s' 的定义 (可能是内置函数/第三方库, 或索引未建立)" % name
        lines = ["找到 %d 个定义:" % len(defs)]
        for d in defs:
            lines.append("-" * 50)
            lines.append("  %s @ %s:%d-%d" % (d["signature"], d["file"], d["line"], d["end_line"]))
            if d["doc"]:
                lines.append("  doc: %s" % d["doc"][:120])
        return "\n".join(lines)


class CodeFindRefsTool(BaseTool):
    """引用追踪: 查找函数/类被哪些文件调用"""
    name = "code_find_refs"
    description = (
        "查找函数或类在项目中所有被调用的位置(文件+行号+代码上下文)。"
        "【何时用】改函数名/删函数前看影响范围、重构前评估调用方、排查某个功能在哪里被使用。"
        "【不要用】找定义用code_find_def; 搜文本用code_search。"
        "使用前需先调用 index_project 建立索引。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "函数名或类名(精确匹配)"},
        },
        "required": ["name"],
    }

    def __init__(self, ast_index=None):
        self.ast_index = ast_index

    def execute(self, name: str = "", **kwargs) -> str:
        if not name:
            return "错误: 请指定符号名"
        if not self.ast_index:
            return "错误: AST索引未初始化, 请先调用 index_project"
        refs = self.ast_index.find_references(name)
        if not refs:
            return "未找到 '%s' 的引用" % name
        lines = ["找到 %d 处引用:" % len(refs)]
        for r in refs[:30]:
            lines.append("  %s:%d  %s" % (r["file"], r["line"], r["context"][:80]))
        if len(refs) > 30:
            lines.append("  ... 还有 %d 处, 已截断" % (len(refs) - 30))
        return "\n".join(lines)


class CodeOutlineTool(BaseTool):
    """文件大纲: 列出一个文件里所有类和函数"""
    name = "code_outline"
    description = (
        "列出指定 Python 文件的结构大纲(所有类/函数名+行号+签名), 快速了解文件结构。"
        "【何时用】打开陌生文件前先看结构、想知道文件里有哪些函数、重构前梳理文件组织。"
        "【不要用】读具体代码用file_read; 搜符号用code_find_def。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径(相对或绝对, 支持模糊匹配)"},
        },
        "required": ["file_path"],
    }

    def __init__(self, ast_index=None):
        self.ast_index = ast_index

    def execute(self, file_path: str = "", **kwargs) -> str:
        if not file_path:
            return "错误: 请指定文件路径"
        if not self.ast_index:
            return "错误: AST索引未初始化, 请先调用 index_project"
        items = self.ast_index.get_file_outline(file_path)
        if not items:
            return "未找到文件 '%s' 的结构 (可能不在索引中, 或不是Python文件)" % file_path
        lines = ["%s 结构大纲 (%d 个符号):" % (file_path, len(items))]
        lines.append("-" * 50)
        for it in items:
            lines.append("  L%-4d %-8s %s" % (it["line"], it["type"], it["signature"]))
        return "\n".join(lines)
