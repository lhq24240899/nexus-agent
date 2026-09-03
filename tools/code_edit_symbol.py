"""
按符号名精确编辑工具
====================
直接指定函数/类名 + 新实现，内部用 AST 定位，自动生成 search_replace
比手动 search_replace 更可靠：不需要 Agent 记住原代码的精确文本
"""
import difflib
from pathlib import Path
from tools.base_tool import BaseTool


class CodeEditSymbolTool(BaseTool):
    """按符号名（函数/类）精确替换实现"""
    name = "code_edit_symbol"
    description = (
        "按函数名或类名直接替换其实现代码。内部用AST精确定位符号位置，"
        "自动提取原代码并替换，不需要手动写search文本。"
        "【何时用】修改某个函数/类的实现、重构方法体、修复函数内bug。"
        "【不要用】创建新函数用file_write；改函数名/签名用code_edit的search_replace；"
        "改非函数代码用code_edit。"
        "使用前需先调用 index_project 建立索引。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "symbol_name": {
                "type": "string",
                "description": "要修改的函数名或类名（精确匹配）",
            },
            "new_code": {
                "type": "string",
                "description": "新的实现代码（包括def/class行和完整函数体）",
            },
            "file_hint": {
                "type": "string",
                "description": "可选：指定文件路径，当符号在多个文件有定义时用",
            },
        },
        "required": ["symbol_name", "new_code"],
    }

    def __init__(self, ast_index=None, code_edit_tool=None):
        self.ast_index = ast_index
        self.code_edit = code_edit_tool

    def execute(self, symbol_name: str = "", new_code: str = "",
                file_hint: str = "", **kwargs) -> str:
        if not symbol_name:
            return "错误: 请指定 symbol_name（函数名或类名）"
        if not new_code:
            return "错误: 请提供 new_code（新的实现代码）"
        if not self.ast_index:
            return "错误: AST索引未初始化，请先调用 index_project"
        if not self.code_edit:
            return "错误: code_edit 工具未注入"

        # 1. 用 AST 索引查找符号定义
        defs = self.ast_index.find_definition(symbol_name)
        if not defs:
            return (f"错误: 未找到符号 '{symbol_name}' 的定义。\n"
                    f"可能原因：符号不存在、索引未建立、或符号是内置/第三方库。\n"
                    f"请先用 code_find_def 确认符号位置。")

        # 2. 如果有多个定义，用 file_hint 筛选
        if len(defs) > 1:
            if file_hint:
                defs = [d for d in defs if file_hint in d.get("file", "")]
                if not defs:
                    return f"错误: 在文件 '{file_hint}' 中未找到 '{symbol_name}'"
            else:
                files = [d["file"] for d in defs]
                return (f"错误: 符号 '{symbol_name}' 在 {len(defs)} 个文件中有定义：\n"
                        + "\n".join(f"  - {f}" for f in files)
                        + "\n请用 file_hint 参数指定要修改哪个文件。")

        target = defs[0]
        file_path = target["file"]
        start_line = target["line"]
        end_line = target.get("end_line", start_line)

        # 3. 读取原文件，提取原符号代码
        try:
            p = Path(file_path)
            if not p.is_absolute():
                # 尝试相对于项目根目录
                p = Path(self.ast_index.root) / file_path
            if not p.exists():
                return f"错误: 文件不存在: {p}"
            original = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"读取文件失败: {e}"

        lines = original.splitlines()
        if end_line > len(lines):
            end_line = len(lines)

        # 提取原符号代码（从 start_line 到 end_line）
        old_symbol_lines = lines[start_line - 1:end_line]
        old_symbol = "\n".join(old_symbol_lines)

        if not old_symbol.strip():
            return f"错误: 提取到的原符号代码为空 (行 {start_line}-{end_line})"

        # 4. 检查引用（修改前提示影响范围）
        refs = []
        try:
            refs = self.ast_index.find_references(symbol_name)
        except Exception:
            pass

        # 5. 用 code_edit 的 search_replace 执行替换
        result = self.code_edit.execute(
            path=str(p),
            action="search_replace",
            search=old_symbol,
            replace=new_code,
        )

        # 6. 附加引用信息
        if refs:
            ref_info = f"\n\n[影响范围] 符号 '{symbol_name}' 在项目中有 {len(refs)} 处引用："
            for r in refs[:10]:
                ref_info += f"\n  {r['file']}:{r['line']}  {r.get('context', '')[:60]}"
            if len(refs) > 10:
                ref_info += f"\n  ... 还有 {len(refs) - 10} 处"
            result += ref_info

        return result
