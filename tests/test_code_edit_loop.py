"""
精确代码编辑 + 编码闭环 测试
"""
import sys
import os
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.code_exec import parse_python_error, format_error_structured, CodeExecTool
from tools.code_edit import CodeEditTool
from tools.code_edit_symbol import CodeEditSymbolTool


# ===== 测试1: 错误解析 =====
class TestErrorParsing(unittest.TestCase):
    def test_parse_zero_division(self):
        stderr = (
            'Traceback (most recent call last):\n'
            '  File "test.py", line 5, in <module>\n'
            '    result = divide(10, 0)\n'
            '  File "test.py", line 2, in divide\n'
            '    return a / b\n'
            'ZeroDivisionError: division by zero\n'
        )
        err = parse_python_error(stderr)
        assert err is not None
        assert err["type"] == "ZeroDivisionError"
        assert err["message"] == "division by zero"
        assert err["file"] == "test.py"
        assert err["line"] == 2
        assert err["in_func"] == "divide"

    def test_parse_name_error(self):
        stderr = (
            'Traceback (most recent call last):\n'
            '  File "app.py", line 10, in main\n'
            '    print(undefined_var)\n'
            "NameError: name 'undefined_var' is not defined\n"
        )
        err = parse_python_error(stderr)
        assert err is not None
        assert err["type"] == "NameError"
        assert "undefined_var" in err["message"]
        assert err["line"] == 10

    def test_parse_no_error(self):
        stderr = "Hello World\n"
        err = parse_python_error(stderr)
        assert err is None

    def test_format_error_with_code(self):
        err = {
            "type": "ZeroDivisionError",
            "message": "division by zero",
            "file": "test.py",
            "line": 2,
            "in_func": "divide",
        }
        code = "def divide(a, b):\n    return a / b\n\nresult = divide(10, 0)\n"
        result = format_error_structured(err, code)
        assert "ZeroDivisionError" in result
        assert "test.py:2" in result
        assert "divide" in result
        assert ">>>" in result  # 报错行标记

    def test_format_error_no_code(self):
        err = {
            "type": "ValueError",
            "message": "invalid literal",
            "file": None,
            "line": None,
            "in_func": None,
        }
        result = format_error_structured(err)
        assert "ValueError" in result
        assert "invalid literal" in result


# ===== 测试2: code_edit_symbol 工具 =====
class TestCodeEditSymbol(unittest.TestCase):
    def test_tool_metadata(self):
        tool = CodeEditSymbolTool()
        assert tool.name == "code_edit_symbol"
        assert "symbol_name" in tool.params_schema["properties"]
        assert "new_code" in tool.params_schema["properties"]
        assert "file_hint" in tool.params_schema["properties"]

    def test_missing_symbol_name(self):
        tool = CodeEditSymbolTool()
        result = tool.execute(symbol_name="", new_code="x=1")
        assert "错误" in result
        assert "symbol_name" in result

    def test_missing_new_code(self):
        tool = CodeEditSymbolTool()
        result = tool.execute(symbol_name="foo", new_code="")
        assert "错误" in result
        assert "new_code" in result

    def test_no_ast_index(self):
        tool = CodeEditSymbolTool()
        result = tool.execute(symbol_name="foo", new_code="x=1")
        assert "AST索引未初始化" in result

    def test_symbol_not_found(self):
        # 创建一个假的 ast_index
        class FakeIndex:
            root = "/tmp"
            def find_definition(self, name):
                return []
        class FakeEdit:
            pass
        tool = CodeEditSymbolTool(ast_index=FakeIndex(), code_edit_tool=FakeEdit())
        result = tool.execute(symbol_name="nonexistent", new_code="x=1")
        assert "未找到符号" in result


# ===== 测试3: code_edit 工具（精确编辑） =====
class TestCodeEdit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.tmpdir, "test.py")
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("def hello():\n    print('hello')\n\ndef world():\n    print('world')\n")
        self.tool = CodeEditTool()
        # 修改 ALLOWED_ROOTS 以允许临时目录
        import tools.code_edit as ce
        self._original_roots = ce.ALLOWED_ROOTS
        ce.ALLOWED_ROOTS.append(self.tmpdir)

    def tearDown(self):
        import tools.code_edit as ce
        ce.ALLOWED_ROOTS = self._original_roots
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_search_replace_success(self):
        result = self.tool.execute(
            path=self.test_file,
            action="search_replace",
            search="def hello():\n    print('hello')",
            replace="def hello():\n    print('hi')",
        )
        assert "编辑成功" in result
        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        # ruff format 可能会改单引号为双引号, 用兼容断言
        assert "print(" in content and "hi" in content
        # 原 print('hello') 已被替换, 但函数名 def hello 还在
        assert "print('hello')" not in content and 'print("hello")' not in content

    def test_search_replace_not_unique(self):
        result = self.tool.execute(
            path=self.test_file,
            action="search_replace",
            search="print(",
            replace="print(",
        )
        assert "不唯一" in result

    def test_search_replace_not_found(self):
        result = self.tool.execute(
            path=self.test_file,
            action="search_replace",
            search="nonexistent_code",
            replace="x=1",
        )
        assert "未找到" in result

    def test_delete_lines(self):
        result = self.tool.execute(
            path=self.test_file,
            action="delete_lines",
            start_line=1,
            end_line=2,
        )
        assert "编辑成功" in result
        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "def hello" not in content
        assert "def world" in content

    def test_append(self):
        result = self.tool.execute(
            path=self.test_file,
            action="append",
            insert="\ndef new_func():\n    pass\n",
        )
        assert "编辑成功" in result
        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "def new_func" in content

    def test_backup_created(self):
        self.tool.execute(
            path=self.test_file,
            action="append",
            insert="# comment\n",
        )
        backup = self.test_file + ".bak"
        assert os.path.exists(backup)


# ===== 测试4: 编码闭环模拟 =====
class TestCodingLoop(unittest.TestCase):
    """模拟 跑→错→修→再跑 闭环"""

    def test_error_then_fix_then_pass(self):
        """模拟：跑代码报错 → 解析错误 → 修复 → 再跑通过"""
        # 初始有 bug 的代码
        buggy_code = "def divide(a, b):\n    return a / b\n\nresult = divide(10, 0)\nprint(result)\n"

        # 第一步：跑代码，捕获错误
        tool = CodeExecTool()
        result = tool.execute(code=buggy_code)

        # 验证返回了结构化错误
        assert "结构化错误" in result
        assert "ZeroDivisionError" in result
        assert "division by zero" in result

        # 第二步：修复代码（模拟 Agent 的修复行为）
        fixed_code = "def divide(a, b):\n    if b == 0:\n        return None\n    return a / b\n\nresult = divide(10, 0)\nprint(result)\n"

        # 第三步：再跑，应该通过
        result2 = tool.execute(code=fixed_code)
        assert "结构化错误" not in result2
        assert "None" in result2  # 输出 None


if __name__ == "__main__":
    unittest.main(verbosity=2)
