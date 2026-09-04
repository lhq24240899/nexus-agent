"""文件读取工具 —— 读取本地文件内容"""
from pathlib import Path
from tools.base_tool import BaseTool

# 安全: 只允许读取的目录白名单
ALLOWED_ROOTS = [
    Path.home(),
    Path("D:/nexus_agent"),
    Path("D:/"),
    Path("C:/Users/1"),
]


def _is_allowed(path: str) -> bool:
    try:
        p = Path(path).resolve()
        for root in ALLOWED_ROOTS:
            try:
                p.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False
    except Exception:
        return False


class FileReadTool(BaseTool):
    name = "file_read"
    description = "读取本地文件内容。支持文本文件(txt/py/md/json/csv等)。【何时用】需要查看文件内容时。【不要用】不要用code_exec执行open()读文件，不要用linux_terminal执行cat读文件。大文件先用file_list确认大小。"
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "max_lines": {"type": "integer", "description": "最多读取行数, 默认 200"},
        },
        "required": ["path"],
    }

    def execute(self, path: str = "", max_lines: int = 200, **kwargs) -> str:
        if not path:
            return "错误: 请指定文件路径"
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"
        p = Path(path)
        if not p.exists():
            return f"错误: 文件不存在: {path}"
        if not p.is_file():
            return f"错误: 不是文件: {path}"
        if p.stat().st_size > 5 * 1024 * 1024:
            return f"错误: 文件过大 ({p.stat().st_size // 1024}KB), 超过 5MB 限制"
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            content = "".join(lines[:max_lines])
            if len(lines) > max_lines:
                content += f"\n... (共 {len(lines)} 行, 已截断到前 {max_lines} 行)"
            return content
        except Exception as e:
            return f"读取失败: {e}"


class FileWriteTool(BaseTool):
    name = "file_write"
    description = "写入内容到本地文件。如果文件存在会覆盖。写入后自动更新代码符号索引(AST+正则)。【何时用】创建新文件、大段重写已有文件。【不要用】不要用code_exec写持久文件(code_exec是临时环境,写完就删)。小修改用code_edit,不要整个文件重写。写之前必须先用file_read确认文件内容(避免覆盖)。"

    def __init__(self, code_index=None, ast_index=None):
        self.code_index = code_index
        self.ast_index = ast_index
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        },
        "required": ["path", "content"],
    }

    def execute(self, path: str = "", content: str = "", **kwargs) -> str:
        if not path:
            return "错误: 请指定文件路径"
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            # 写入后自动增量索引 (AST + 正则)
            if self.code_index:
                try: self.code_index.index_file(str(p))
                except Exception: pass
            if self.ast_index:
                try: self.ast_index.index_file(str(p))
                except Exception: pass
            return f"已写入 {len(content)} 字符到 {path}"
        except Exception as e:
            return f"写入失败: {e}"


class FileListTool(BaseTool):
    name = "file_list"
    description = "列出目录下的文件和子目录。【何时用】查看项目结构、确认文件是否存在。【不要用】不要用code_exec执行os.listdir()，不要用linux_terminal执行ls/dir。"
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径, 默认当前目录"},
        },
        "required": [],
    }

    def execute(self, path: str = ".", **kwargs) -> str:
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"
        p = Path(path)
        if not p.exists():
            return f"错误: 目录不存在: {path}"
        if not p.is_dir():
            return f"错误: 不是目录: {path}"
        try:
            items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            lines = []
            for item in items[:100]:
                if item.is_dir():
                    lines.append(f"[DIR]  {item.name}/")
                else:
                    size = item.stat().st_size
                    if size > 1024 * 1024:
                        size_str = f"{size // 1024 // 1024}MB"
                    elif size > 1024:
                        size_str = f"{size // 1024}KB"
                    else:
                        size_str = f"{size}B"
                    lines.append(f"[FILE] {item.name} ({size_str})")
            if len(items) > 100:
                lines.append(f"... (共 {len(items)} 项, 已显示前 100 项)")
            return "\n".join(lines) if lines else "(空目录)"
        except Exception as e:
            return f"列出失败: {e}"
