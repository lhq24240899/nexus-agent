"""
代码搜索工具 —— 符号索引优先, 找不到再 grep
支持: 符号名快速查找(索引)、关键词/正则搜索(grep)、按文件类型过滤
"""
import re
from pathlib import Path
from tools.base_tool import BaseTool

# 安全: 只允许搜索的目录白名单
ALLOWED_ROOTS = [
    Path("D:/nexus_agent"),
    Path("D:/"),
    Path("C:/Users/1"),
    Path.home(),
]

# 搜索时跳过的目录
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "data", "dist", "build", ".idea", ".vscode", "*.egg-info",
}

# 二进制文件扩展名 (跳过)
BINARY_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".docx", ".xlsx", ".pptx", ".class", ".jar",
}


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


def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS or part.startswith("."):
            return True
    if path.suffix.lower() in BINARY_EXTS:
        return True
    return False


def _is_symbol_query(pattern: str) -> bool:
    """判断是否是符号名查询(纯字母数字下划线, 无空格)"""
    return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", pattern))


class CodeSearchTool(BaseTool):
    name = "code_search"
    description = (
        "在代码库中搜索符号(函数/类/变量)或文本。符号走索引秒查，文本走grep。"
        "【何时用】找函数定义在哪、找变量在哪被使用、搜索错误信息相关代码。"
        "【不要用】读具体文件内容用file_read；列目录用file_list。"
        "索引未覆盖的文件可能搜不到，此时用file_read。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "搜索根目录, 默认当前项目"},
            "pattern": {"type": "string", "description": "搜索关键词(符号名或文本)"},
            "file_type": {"type": "string", "description": "文件类型过滤, 如 py/js/ts, 不填则所有"},
            "max_results": {"type": "integer", "description": "最大返回结果数, 默认 30"},
            "use_regex": {"type": "boolean", "description": "是否使用正则, 默认 false"},
        },
        "required": ["pattern"],
    }

    def __init__(self, code_index=None):
        self.code_index = code_index

    def execute(self, pattern: str = "", path: str = ".",
                file_type: str = "", max_results: int = 30,
                use_regex: bool = False, **kwargs) -> str:
        if not pattern:
            return "错误: 请指定搜索关键词"
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"

        root = Path(path).resolve()
        if not root.exists():
            return f"错误: 目录不存在: {path}"

        # 快速路径: 符号名查询走索引
        if self.code_index and _is_symbol_query(pattern) and not use_regex:
            idx_results = self.code_index.search(pattern, project_path=str(root), limit=max_results)
            if idx_results:
                lines = [f"[索引] {r['file']}:{r['line']} ({r['type']}) {r['name']}"
                         for r in idx_results]
                return f"符号索引找到 {len(idx_results)} 个定义:\n{'-' * 50}\n" + "\n".join(lines)

        # 慢速路径: grep
        try:
            if use_regex:
                regex = re.compile(pattern, re.IGNORECASE)
            else:
                regex = re.compile(re.escape(pattern), re.IGNORECASE)
        except re.error as e:
            return f"错误: 正则表达式无效: {e}"

        results = []
        files_scanned = 0
        target_suffix = f".{file_type.lower().lstrip('.')}" if file_type else None

        try:
            for filepath in root.rglob("*"):
                if _should_skip(filepath):
                    continue
                if not filepath.is_file():
                    continue
                if target_suffix and filepath.suffix.lower() != target_suffix:
                    continue
                if filepath.suffix.lower() in BINARY_EXTS:
                    continue

                files_scanned += 1
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = filepath.relative_to(root)
                                results.append(
                                    f"{rel}:{lineno}: {line.rstrip()[:120]}"
                                )
                                if len(results) >= max_results:
                                    break
                except Exception:
                    continue
                if len(results) >= max_results:
                    break
        except Exception as e:
            return f"搜索失败: {e}"

        if not results:
            return f"未找到匹配内容 (扫描了 {files_scanned} 个文件)"

        header = f"找到 {len(results)} 处匹配 (扫描 {files_scanned} 个文件):\n{'-' * 50}\n"
        return header + "\n".join(results)


class IndexProjectTool(BaseTool):
    """建立/重建项目代码符号索引"""
    name = "index_project"
    description = (
        "扫描项目目录, 建立函数/类/变量符号索引。大项目首次运行需要几秒, "
        "之后 code_search 查符号名秒级返回。编码任务开始时建议先调用。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "项目根目录, 默认当前项目"},
        },
        "required": ["path"],
    }

    def __init__(self, code_index=None):
        self.code_index = code_index

    def execute(self, path: str = ".", **kwargs) -> str:
        if not self.code_index:
            return "错误: 代码索引未初始化"
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"
        root = Path(path).resolve()
        if not root.exists():
            return f"错误: 目录不存在: {path}"

        result = self.code_index.index_project(str(root))
        stats = self.code_index.stats()
        return (f"索引完成: {result['symbols']} 个符号\n"
                f"文件: {stats['files_indexed']} 个\n"
                f"类型分布: {stats['by_type']}")
