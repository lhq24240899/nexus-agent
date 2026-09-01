"""
精确代码编辑工具 —— 类似 Codex 的 search/replace 级别编辑
支持: search_replace / insert_after / insert_before / delete_lines / append
关键: 搜索文本必须唯一匹配, 防止误替换; 编辑前自动读取确认; 编辑后显示 diff
"""
import difflib
from pathlib import Path
from tools.base_tool import BaseTool

# 安全: 允许编辑的目录
ALLOWED_ROOTS = [
    Path("D:/nexus_agent"),
    Path("D:/"),
    Path("C:/Users/1"),
    Path.home(),
]

# 跳过的文件类型
SKIP_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
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


def _read_file(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_file(path: Path, content: str):
    path.write_text(content, encoding="utf-8")


def _make_diff(old: str, new: str, filename: str) -> str:
    """生成统一 diff 格式"""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )
    return "\n".join(diff)


class CodeEditTool(BaseTool):
    name = "code_edit"

    def __init__(self, code_index=None):
        self.code_index = code_index
    description = (
        "精确编辑已有代码文件。支持search_replace(搜索替换,搜索文本必须唯一)、"
        "insert_after/insert_before(插入)、delete_lines(删行)、append(追加)。"
        "【何时用】小范围修改已有文件(改几行/加函数/修bug)。"
        "【不要用】创建新文件用file_write；大段重写(超过50行)用file_write(先file_read读内容)；"
        "search_replace搜索不到或不唯一时，先用file_read确认实际内容(注意空格/缩进/换行)，不要盲目重试。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "action": {
                "type": "string",
                "description": "编辑模式",
                "enum": ["search_replace", "insert_after", "insert_before", "delete_lines", "append", "rollback"],
            },
            "search": {"type": "string", "description": "要搜索的文本 (search_replace/insert_after/insert_before 必需), 必须唯一匹配"},
            "replace": {"type": "string", "description": "替换后的文本 (search_replace 必需)"},
            "insert": {"type": "string", "description": "要插入的文本 (insert_after/insert_before/append 必需)"},
            "start_line": {"type": "integer", "description": "起始行号 (delete_lines 必需, 从1开始)"},
            "end_line": {"type": "integer", "description": "结束行号 (delete_lines 必需, 包含)"},
        },
        "required": ["path", "action"],
    }

    def execute(self, path: str = "", action: str = "",
                search: str = "", replace: str = "", insert: str = "",
                start_line: int = 0, end_line: int = 0, **kwargs) -> str:
        if not path:
            return "错误: 请指定文件路径"
        if not action:
            return "错误: 请指定编辑模式 (action)"
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"

        p = Path(path)
        if not p.exists():
            return f"错误: 文件不存在: {path}"
        if not p.is_file():
            return f"错误: 不是文件: {path}"
        if p.suffix.lower() in SKIP_EXTS:
            return f"错误: 不支持编辑二进制文件: {p.suffix}"
        if p.stat().st_size > 2 * 1024 * 1024:
            return f"错误: 文件过大 ({p.stat().st_size // 1024}KB), 超过 2MB 限制"

        try:
            original = _read_file(p)
        except Exception as e:
            return f"读取失败: {e}"

        lines = original.splitlines()
        result = ""

        # ===== search_replace =====
        if action == "search_replace":
            if not search:
                return "错误: search_replace 需要 search 参数"
            if replace is None:
                return "错误: search_replace 需要 replace 参数"

            count = original.count(search)
            if count == 0:
                # 尝试逐行模糊匹配提示
                return (f"错误: 未找到搜索文本。请确认文本与文件内容完全一致 (包括空格和缩进)。\n"
                        f"文件前 500 字符:\n{original[:500]}")
            if count > 1:
                # 显示所有匹配位置帮助精确定位
                positions = []
                idx = 0
                while True:
                    pos = original.find(search, idx)
                    if pos == -1:
                        break
                    line_no = original[:pos].count("\n") + 1
                    positions.append(line_no)
                    idx = pos + 1
                return (f"错误: 搜索文本在文件中出现 {count} 次 (行 {positions}), 不唯一。"
                        f"请提供更长、更精确的搜索文本以确保唯一匹配。")

            new_content = original.replace(search, replace, 1)
            result = self._apply_edit(p, original, new_content, "search_replace")

        # ===== insert_after =====
        elif action == "insert_after":
            if not search:
                return "错误: insert_after 需要 search 参数"
            if not insert:
                return "错误: insert_after 需要 insert 参数"

            count = original.count(search)
            if count == 0:
                return f"错误: 未找到搜索文本。文件前 500 字符:\n{original[:500]}"
            if count > 1:
                return f"错误: 搜索文本出现 {count} 次, 不唯一, 请提供更精确的文本。"

            pos = original.find(search) + len(search)
            new_content = original[:pos] + "\n" + insert + original[pos:]
            result = self._apply_edit(p, original, new_content, "insert_after")

        # ===== insert_before =====
        elif action == "insert_before":
            if not search:
                return "错误: insert_before 需要 search 参数"
            if not insert:
                return "错误: insert_before 需要 insert 参数"

            count = original.count(search)
            if count == 0:
                return f"错误: 未找到搜索文本。文件前 500 字符:\n{original[:500]}"
            if count > 1:
                return f"错误: 搜索文本出现 {count} 次, 不唯一, 请提供更精确的文本。"

            pos = original.find(search)
            new_content = original[:pos] + insert + "\n" + original[pos:]
            result = self._apply_edit(p, original, new_content, "insert_before")

        # ===== delete_lines =====
        elif action == "delete_lines":
            if start_line <= 0 or end_line <= 0:
                return "错误: delete_lines 需要 start_line 和 end_line (从1开始)"
            if start_line > end_line:
                return "错误: start_line 不能大于 end_line"
            if start_line > len(lines):
                return f"错误: start_line ({start_line}) 超出文件行数 ({len(lines)})"
            if end_line > len(lines):
                end_line = len(lines)

            new_lines = lines[:start_line - 1] + lines[end_line:]
            new_content = "\n".join(new_lines)
            if original.endswith("\n"):
                new_content += "\n"
            result = self._apply_edit(p, original, new_content,
                                      f"delete_lines (行 {start_line}-{end_line})")

        # ===== append =====
        elif action == "append":
            if not insert:
                return "错误: append 需要 insert 参数"
            if not original.endswith("\n"):
                new_content = original + "\n" + insert
            else:
                new_content = original + insert
            result = self._apply_edit(p, original, new_content, "append")

        # ===== rollback =====
        elif action == "rollback":
            backup_path = p.with_suffix(p.suffix + ".bak")
            if not backup_path.exists():
                return f"错误: 未找到备份文件 {backup_path.name}, 无法回滚"
            try:
                backup_content = _read_file(backup_path)
                _write_file(p, backup_content)
                return (f"✅ 回滚成功\n"
                        f"文件: {path}\n"
                        f"已从备份 {backup_path.name} 恢复\n"
                        f"恢复大小: {len(backup_content)} 字符")
            except Exception as e:
                return f"回滚失败: {e}"

        else:
            return f"错误: 未知操作: {action}, 可选: search_replace/insert_after/insert_before/delete_lines/append/rollback"

        return result

    def _apply_edit(self, path: Path, original: str, new_content: str, action_desc: str) -> str:
        """执行编辑, 先备份再写入, 返回结果和 diff"""
        if original == new_content:
            return f"提示: 编辑后内容无变化 ({action_desc})"

        # 自动备份
        backup_path = path.with_suffix(path.suffix + ".bak")
        try:
            backup_path.write_text(original, encoding="utf-8")
        except Exception:
            pass  # 备份失败不阻止编辑

        try:
            _write_file(path, new_content)
        except Exception as e:
            return f"写入失败: {e}"

        # 编辑后自动增量索引
        if self.code_index:
            self.code_index.index_file(str(path))

        diff = _make_diff(original, new_content, path.name)
        diff_preview = diff[:2000] if len(diff) > 2000 else diff
        if len(diff) > 2000:
            diff_preview += f"\n... (diff 共 {len(diff)} 字符, 已截断)"

        return (f"✅ 编辑成功 ({action_desc})\n"
                f"文件: {path}\n"
                f"备份: {backup_path.name}\n"
                f"原大小: {len(original)} 字符 → 新大小: {len(new_content)} 字符\n"
                f"变更行数: +{new_content.count(chr(10)) - original.count(chr(10))} 行\n"
                f"\n--- Diff ---\n{diff_preview}")
