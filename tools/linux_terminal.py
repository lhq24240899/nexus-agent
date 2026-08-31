"""Linux 终端工具 —— 复用 LinuxEmbed, 在嵌入的 Linux 中执行命令
带命令黑名单 + 资源限制（ulimit + timeout 双层保护）
支持工作目录保持 (cd 后后续命令在新目录执行)
"""
from tools.base_tool import BaseTool
from tools.safety import is_dangerous, build_safe_command


class LinuxTerminalTool(BaseTool):
    name = "linux_terminal"
    description = (
        "在嵌入的 Linux 系统中执行 shell 命令。用于文件操作、系统查询、安装软件等。"
        "危险命令会被拦截，命令有 CPU/内存/时间限制。支持 cd 切换目录。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "profile": {
                "type": "string",
                "description": "资源档位: strict(默认)/normal/loose，不传则自动判断",
                "enum": ["strict", "normal", "loose", "auto"],
            },
        },
        "required": ["command"],
    }

    def __init__(self, linux_embed=None):
        self.linux = linux_embed

    def execute(self, command: str = "", profile: str = "auto", **kwargs) -> str:
        if not self.linux or not self.linux.available:
            return "错误: Linux 环境不可用 (未安装 Docker/WSL)"

        # 1. 黑名单检查
        dangerous, matched = is_dangerous(command)
        if dangerous:
            return f"错误: 命令被安全策略拦截 (匹配规则: {matched})"

        # 2. cd 命令不需要资源限制包装 (ulimit 会影响 cd)
        stripped = command.strip()
        if stripped.startswith("cd ") or stripped == "cd":
            result = self.linux.exec(command, timeout=10)
            cwd = result.get("cwd", "~")
            if result.get("error"):
                return f"错误: {result['error']}"
            return f"(当前目录: {cwd})"

        # 2.5 展开常用 alias (timeout 命令不继承 bash alias)
        ALIASES = {
            "ll": "ls -alF",
            "la": "ls -A",
            "l": "ls -CF",
            "grep": "grep --color=auto",
            "..": "cd ..",
        }
        for alias, real in ALIASES.items():
            if stripped == alias or stripped.startswith(alias + " "):
                command = real + stripped[len(alias):]
                break

        # 3. 构建带资源限制的安全命令
        safe_cmd, used_profile = build_safe_command(command, profile)

        # 4. 执行（Python 侧再加一层超时兜底）
        result = self.linux.exec(safe_cmd, timeout=60)

        output = result.get("stdout", "")
        if result.get("stderr"):
            output += f"\n[stderr]\n{result['stderr']}"
        if result.get("error"):
            output += f"\n[错误] {result['error']}"

        # 检查退出码
        exit_code = result.get("exit_code", 0)
        if exit_code == 124:
            output += "\n[提示] 命令执行超时，已被强制终止"
        elif exit_code != 0 and not result.get("error"):
            output += f"\n[提示] 命令退出码: {exit_code}"

        cwd = result.get("cwd", "~")
        if not output.strip():
            output = "(命令执行完成, 无输出)"
        output += f"\n[目录: {cwd} | 档位: {used_profile}]"
        return output
