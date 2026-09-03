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


# ===== 结构化错误解析 =====
def parse_python_error(stderr: str, stdout: str = "") -> dict:
    """解析 Python  traceback，返回结构化错误信息"""
    if not stderr:
        return None
    combined = (stderr or "") + "\n" + (stdout or "")
    # 匹配最后一个 Error 行
    error_match = re.search(r"^([\w.]+Error|Exception|Warning):\s*(.+)$", combined, re.MULTILINE)
    if not error_match:
        return None
    error_type = error_match.group(1)
    error_msg = error_match.group(2).strip()
    # 匹配文件和行号 (取最后一个 File 行)
    file_matches = re.findall(r'File "([^"]+)", line (\d+)(?:, in (.+))?', combined)
    if not file_matches:
        return {"type": error_type, "message": error_msg, "file": None, "line": None, "in_func": None}
    last_file, last_line, last_func = file_matches[-1]
    return {
        "type": error_type,
        "message": error_msg,
        "file": last_file,
        "line": int(last_line),
        "in_func": last_func,
    }


def format_error_structured(err: dict, source_code: str = "") -> str:
    """格式化结构化错误为可读文本"""
    if not err:
        return ""
    lines = []
    lines.append(f"[错误类型] {err['type']}")
    lines.append(f"[错误信息] {err['message']}")
    if err.get("file"):
        lines.append(f"[位置] {err['file']}:{err['line']}" + (f" (in {err['in_func']})" if err.get("in_func") else ""))
    # 如果有源码，显示报错行上下文
    if source_code and err.get("line"):
        code_lines = source_code.splitlines()
        ln = err["line"]
        start = max(0, ln - 3)
        end = min(len(code_lines), ln + 2)
        lines.append("[代码上下文]")
        for i in range(start, end):
            marker = ">>>" if i == ln - 1 else "   "
            lines.append(f"  {marker} {i+1}: {code_lines[i]}")
    return "\n".join(lines)



class CodeExecTool(BaseTool):
    name = "code_exec"
    description = (
        "执行 Python 代码并返回输出。用于计算、数据处理、验证逻辑、跑测试。"
        "代码在临时环境中运行，执行后临时文件会被删除。"
        "支持中文输出(UTF-8)，Windows 下不会乱码。"
        "【重要】不要用此工具创建/写入持久文件，写文件请用 file_write 工具。"
        "【重要】安装依赖(pip install/npm install)、下载、编译等长命令请用 linux_terminal 工具，不要用 code_exec。"
        "默认 15 秒超时，可通过 timeout 参数延长(最大120秒)，危险代码会被拦截。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认15，最大120。仅用于确实需要长时间的计算任务，安装命令请用linux_terminal。",
                "default": 15,
            }
        },
        "required": ["code"],
    }

    def execute(self, code: str = "", timeout: int = 15, **kwargs) -> str:
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
            # 2. 子进程执行 + 超时兜底(杀整个进程树, 防止Playwright等子进程挂起)
            python_exe = sys.executable or "python"
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            effective_timeout = max(1, min(int(timeout), 120))
            proc = None
            try:
                proc = subprocess.Popen(
                    [python_exe, tmp_path],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    encoding="utf-8", errors="replace",
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                try:
                    stdout, stderr = proc.communicate(timeout=effective_timeout)
                    output = stdout or ""
                    if stderr:
                        output += f"\n[stderr]\n{stderr}"
                    if proc.returncode != 0:
                        output += f"\n[退出码: {proc.returncode}]"
                        # 结构化错误解析
                        err = parse_python_error(stderr, stdout)
                        if err:
                            structured = format_error_structured(err, code)
                            output += f"\n\n===== 结构化错误 =====\n{structured}"
                    return output if output.strip() else "(代码执行完成, 无输出)"
                except subprocess.TimeoutExpired:
                    # 超时: 杀整个进程树(Windows用taskkill /T, Unix用killpg)
                    self._kill_process_tree(proc)
                    try:
                        stdout, stderr = proc.communicate(timeout=3)
                    except Exception:
                        stdout, stderr = "", ""
                    extra = ""
                    if stderr:
                        extra = f" 最后输出: {stderr[:200]}"
                    return (f"错误: 代码执行超时 ({effective_timeout}秒限制, 已强制终止进程树。"
                            f"可能是死循环/浏览器挂起/计算量过大。安装依赖请用linux_terminal，"
                            f"或增大timeout参数){extra}")
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            return f"执行失败: {type(e).__name__}: {str(e)}"

    @staticmethod
    def _kill_process_tree(proc):
        """杀整个进程树, 防止子进程(如Chromium)挂起"""
        try:
            if sys.platform == "win32":
                # Windows: taskkill /T /F /PID 杀进程树
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5,
                )
            else:
                # Unix: 杀进程组
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            # 兜底: 只杀父进程
            try:
                proc.kill()
            except Exception:
                pass
