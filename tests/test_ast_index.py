"""
AST 索引深度集成测试
验证: 增量索引、file_write后更新、code_edit后更新、code_search走AST
"""
import unittest
import tempfile
import os
import shutil
from pathlib import Path


class TestMultiLangIndexIncremental(unittest.TestCase):
    """测试 MultiLangCodeIndex 的增量索引功能"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nexus_ast_test_")
        # 创建一个测试 Python 文件
        self.test_file = os.path.join(self.tmpdir, "test_module.py")
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write('''def hello():
    """打招呼"""
    return "hello"

class Calculator:
    def add(self, a, b):
        return a + b

def main():
    calc = Calculator()
    print(calc.add(1, 2))
    hello()
''')
        from tools.code_index import MultiLangCodeIndex
        self.idx = MultiLangCodeIndex(project_root=self.tmpdir, db_path=os.path.join(self.tmpdir, "test_idx.db"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_index(self):
        """测试全量构建索引"""
        result = self.idx.build()
        self.assertEqual(result["files"], 1)
        self.assertGreaterEqual(result["symbols"], 3)  # hello, Calculator, add, main
        self.assertGreaterEqual(result["refs"], 2)  # add, hello 的调用

    def test_find_definition(self):
        """测试符号定义查找"""
        self.idx.build()
        defs = self.idx.find_definition("hello")
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs[0]["name"], "hello")
        self.assertEqual(defs[0]["type"], "function")

    def test_find_references(self):
        """测试引用追踪"""
        self.idx.build()
        refs = self.idx.find_references("hello")
        self.assertGreaterEqual(len(refs), 1)
        self.assertIn("hello", refs[0]["context"])

    def test_incremental_index_new_file(self):
        """测试增量索引: 新增文件"""
        self.idx.build()
        # 新增一个文件
        new_file = os.path.join(self.tmpdir, "new_module.py")
        with open(new_file, "w", encoding="utf-8") as f:
            f.write("def new_func():\n    return 42\n")
        # 增量索引
        result = self.idx.index_file(new_file)
        self.assertEqual(result["symbols"], 1)
        # 验证能找到新符号
        defs = self.idx.find_definition("new_func")
        self.assertEqual(len(defs), 1)

    def test_incremental_index_modify_file(self):
        """测试增量索引: 修改文件后符号更新"""
        self.idx.build()
        # 修改文件: 把 hello 改成 goodbye
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write('''def goodbye():
    return "bye"

class Calculator:
    def add(self, a, b):
        return a + b
''')
        # 增量索引
        self.idx.index_file(self.test_file)
        # 旧符号应该不存在了
        old_defs = self.idx.find_definition("hello")
        self.assertEqual(len(old_defs), 0)
        # 新符号应该存在
        new_defs = self.idx.find_definition("goodbye")
        self.assertEqual(len(new_defs), 1)

    def test_incremental_index_unsupported_file(self):
        """测试增量索引: 不支持的文件类型跳过"""
        self.idx.build()
        txt_file = os.path.join(self.tmpdir, "readme.txt")
        with open(txt_file, "w") as f:
            f.write("hello world")
        result = self.idx.index_file(txt_file)
        self.assertEqual(result["symbols"], 0)
        self.assertIn("skipped", result)

    def test_incremental_index_nonexistent_file(self):
        """测试增量索引: 不存在的文件"""
        self.idx.build()
        result = self.idx.index_file("/nonexistent/file.py")
        self.assertEqual(result["symbols"], 0)
        self.assertIn("skipped", result)

    def test_get_file_outline(self):
        """测试文件大纲"""
        self.idx.build()
        outline = self.idx.get_file_outline("test_module.py")
        self.assertGreaterEqual(len(outline), 3)
        names = [item["name"] for item in outline]
        self.assertIn("hello", names)
        self.assertIn("Calculator", names)

    def test_stats(self):
        """测试索引统计"""
        self.idx.build()
        stats = self.idx.stats()
        self.assertGreaterEqual(stats["symbols"], 3)
        self.assertGreaterEqual(stats["references"], 1)
        self.assertEqual(stats["files"], 1)


class TestCodeSearchAST(unittest.TestCase):
    """测试 code_search 走 AST 索引"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nexus_search_test_")
        self.test_file = os.path.join(self.tmpdir, "search_test.py")
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write('''def target_func():
    return "found"

def caller():
    target_func()
''')
        from tools.code_index import MultiLangCodeIndex
        self.idx = MultiLangCodeIndex(project_root=self.tmpdir, db_path=os.path.join(self.tmpdir, "search_idx.db"))
        self.idx.build()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_search_symbol_ast(self):
        """测试符号搜索走 AST 索引"""
        from tools.code_search import CodeSearchTool
        tool = CodeSearchTool(ast_index=self.idx)
        result = tool.execute(pattern="target_func", path=self.tmpdir)
        self.assertIn("AST 定义", result)
        self.assertIn("AST 引用", result)
        self.assertIn("target_func", result)


if __name__ == "__main__":
    unittest.main()
