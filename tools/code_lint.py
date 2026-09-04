"""
代码 Lint 工具
==============
支持 Python (ruff) / JS/TS (eslint/prettier) / Go (gofmt)
检测工具是否安装, 没安装则返回安装命令
改完代码后调用, 自动修复格式问题
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tools.base_tool import BaseTool


def _find_python() -> str:
    """找到虚拟环境的 python"""
    venv_python = os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable


def _check_tool(name: str) -> bool:
    """检查命令行工具是否可用"""
    return shutil.which(name) is not None


class CodeLintTool(BaseTool):
    """代码 Lint + 自动格式化"""

    name = "code_lint"
    description = (
        "对代码文件进行 lint 检查和自动格式化。"
        "Python 用 ruff (check + format), JS/TS 用 prettier, Go 用 gofmt。"
        "【何时用】改完代码后必须调用, 检查语法错误和格式问题, 自动修复可修复的问题。"
        "【不要用】不要用 code_exec 手动跑 ruff/black, 用这个工具统一处理。"
        "工具未安装时会返回安装命令。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件或目录路径",
            },
            "fix": {
                "type": "boolean",
                "description": "是否自动修复可修复的问题, 默认 true",
                "default": True,
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str = "", fix: bool = True, **kwargs) -> str:
        if not path:
            return "错误: 请指定文件或目录路径"

        p = Path(path)
        if not p.exists():
            return f"错误: 路径不存在: {path}"

        # 判断语言
        if p.is_file():
            ext = p.suffix.lower()
        else:
            ext = self._detect_dir_language(p)

        if ext in (".py",):
            return self._lint_python(str(p), fix)
        elif ext in (".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json", ".md"):
            return self._lint_javascript(str(p), fix)
        elif ext in (".go",):
            return self._lint_go(str(p), fix)
        else:
            return f"提示: 不支持的文件类型 {ext}, 跳过 lint。支持: .py/.js/.ts/.go"

    @staticmethod
    def _detect_dir_language(directory: Path) -> str:
        """检测目录主要语言"""
        exts = {}
        for f in directory.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".py", ".js", ".ts", ".go"):
                exts[f.suffix.lower()] = exts.get(f.suffix.lower(), 0) + 1
        if exts:
            return max(exts, key=exts.get)
        return ".py"

    def _lint_python(self, path: str, fix: bool) -> str:
        """Python: ruff check + ruff format"""
        python = _find_python()
        lines = []

        # 1. ruff check (语法/风格检查)
        try:
            cmd = [python, "-m", "ruff", "check", path]
            if fix:
                cmd.append("--fix")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0:
                lines.append("✅ ruff check: 无问题")
            else:
                output = (result.stdout + result.stderr).strip()
                if output:
                    lines.append(f"⚠️ ruff check 发现问题:\n{output[:1500]}")
                else:
                    lines.append("✅ ruff check: 无问题")
        except FileNotFoundError:
            lines.append("❌ ruff 未安装。安装命令: pip install ruff")
            return "\n".join(lines)
        except subprocess.TimeoutExpired:
            lines.append("⚠️ ruff check 超时(30s)")
        except Exception as e:
            lines.append(f"⚠️ ruff check 异常: {e}")

        # 2. ruff format (格式化)
        if fix:
            try:
                result = subprocess.run(
                    [python, "-m", "ruff", "format", path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding="utf-8",
                    errors="replace",
                )
                output = (result.stdout + result.stderr).strip()
                if result.returncode == 0:
                    if "file" in output.lower() or "reformatted" in output.lower():
                        lines.append(f"✅ ruff format: {output}")
                    else:
                        lines.append("✅ ruff format: 已是最佳格式")
                else:
                    lines.append(f"⚠️ ruff format: {output[:500]}")
            except Exception as e:
                lines.append(f"⚠️ ruff format 异常: {e}")

        return "\n".join(lines)

    def _lint_javascript(self, path: str, fix: bool) -> str:
        """JS/TS: prettier (格式化)"""
        lines = []

        # 检查 prettier 是否可用
        if not _check_tool("npx"):
            lines.append("❌ npx 未安装, 无法运行 prettier。请安装 Node.js。")
            return "\n".join(lines)

        try:
            cmd = ["npx", "--yes", "prettier"]
            if fix:
                cmd.append("--write")
            else:
                cmd.append("--check")
            cmd.append(path)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                encoding="utf-8",
                errors="replace",
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                if fix:
                    lines.append(f"✅ prettier 格式化完成:\n{output[:500]}")
                else:
                    lines.append(f"✅ prettier 检查通过:\n{output[:500]}")
            else:
                lines.append(f"⚠️ prettier 发现格式问题:\n{output[:1000]}")
        except subprocess.TimeoutExpired:
            lines.append("⚠️ prettier 超时(60s), 首次运行需要下载包")
        except Exception as e:
            lines.append(f"⚠️ prettier 异常: {e}")

        return "\n".join(lines)

    def _lint_go(self, path: str, fix: bool) -> str:
        """Go: gofmt"""
        lines = []
        if not _check_tool("gofmt"):
            lines.append("❌ gofmt 未安装。请安装 Go。")
            return "\n".join(lines)

        try:
            if fix:
                result = subprocess.run(
                    ["gofmt", "-w", path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    lines.append("✅ gofmt 格式化完成")
                else:
                    lines.append(f"⚠️ gofmt: {result.stderr[:500]}")
            else:
                result = subprocess.run(
                    ["gofmt", "-l", path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding="utf-8",
                    errors="replace",
                )
                output = result.stdout.strip()
                if output:
                    lines.append(f"⚠️ 以下文件需要格式化:\n{output}")
                else:
                    lines.append("✅ gofmt: 格式正确")
        except Exception as e:
            lines.append(f"⚠️ gofmt 异常: {e}")

        return "\n".join(lines)
