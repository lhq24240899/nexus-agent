"""代码执行工具 —— 在子进程中运行 Python 代码
带危险代码检测 + 超时限制
注意: Windows 原生 Python 无法使用 ulimit，完整资源隔离建议在 WSL 中执行
"""
import subprocess
import tempfile
import os
import re
import sys
from tools.base_tool import BaseTool

# 危险代码模式
DANGEROUS_CODE_PATTERNS = [
    r"os\.system\s*\(\s*['\"]rm\s+-rf",
    r"subprocess\..*rm\s+-rf",
    r"os\.remove\s*\(\s*['\"]/['\"]",
    r"shutil\.rmtree\s*\(\s*['\"]/['\"]",
    r"os\.system\s*\(\s*['\"]format",
    r"os\.system\s*\(\s*['\"]mkfs",
    r"while\s+True\s*:\s*\n\s*pass",
    r"fork\(\)|os\.fork",
    r"__import__\s*\(\s*['\"]os['\"]\s*\)\.system",
]

DANGEROUS_CODE_RE = re.compile("|".join(DANGEROUS_CODE_PATTERNS), re.IGNORECASE)


class CodeExecTool(BaseTool):
    name = "code_exec"
    description = (
        "执行 Python 代码并返回输出。用于计算、数据处理、验证逻辑、跑测试。"
        "代码在临时环境中运行，执行后临时文件会被删除。"
        "支持中文输出(UTF-8)，Windows 下不会乱码。"
        "【重要】不要用此工具创建/写入持久文件，写文件请用 file_write 工具。"
        "有 15 秒超时限制，危险代码会被拦截。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            }
        },
        "required": ["code"],
    }

    def execute(self, code: str = "", **kwargs) -> str:
        # 修复: 处理 code 为 None 的情况
        if code is None:
            return "错误: code 参数为空 (null), 请提供要执行的 Python 代码"
        if not isinstance(code, str):
            return f"错误: code 参数类型错误, 期望字符串, 得到 {type(code).__name__}"
        if not code.strip():
            return "错误: 代码不能为空"

        # 1. 危险代码检测
        m = DANGEROUS_CODE_RE.search(code)
        if m:
            return f"错误: 代码包含危险操作被拦截 (匹配: {m.group(0)[:50]})"

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                tmp_path = f.name
            try:
                # 2. 子进程执行 + 超时兜底
                # 使用当前 Python 解释器, 避免 PATH 问题
                python_exe = sys.executable or "python"
                # 强制 UTF-8 编码, 解决 Windows GBK 中文乱码
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                result = subprocess.run(
                    [python_exe, tmp_path],
                    capture_output=True, text=True, timeout=15,
                    encoding="utf-8", errors="replace",
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                output = result.stdout or ""
                if result.stderr:
                    output += f"\n[stderr]\n{result.stderr}"
                if result.returncode != 0:
                    output += f"\n[退出码: {result.returncode}]"
                return output if output.strip() else "(代码执行完成, 无输出)"
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except subprocess.TimeoutExpired:
            return "错误: 代码执行超时 (15秒限制，可能是死循环或计算量过大)"
        except Exception as e:
            return f"执行失败: {type(e).__name__}: {str(e)}"
