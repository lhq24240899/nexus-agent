"""
Git 工具 —— 查看 Git 仓库状态、差异、提交记录
安全: 只读操作 (status / diff / log / show), 不支持 commit/push 等写操作
写操作通过 linux_terminal 手动执行
"""
import subprocess
from pathlib import Path
from tools.base_tool import BaseTool

# 允许操作的目录
ALLOWED_ROOTS = [
    Path("D:/nexus_agent"),
    Path("D:/"),
    Path("C:/Users/1"),
    Path.home(),
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


def _run_git(args: list[str], cwd: str = ".") -> str:
    """执行 git 命令, 返回输出"""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            output += f"\n[stderr] {result.stderr.strip()[:300]}"
        if not output:
            return "(无输出)"
        return output[:3000]  # 限制长度
    except FileNotFoundError:
        return "错误: 未找到 git 命令, 请确认已安装 Git"
    except subprocess.TimeoutExpired:
        return "错误: git 命令超时 (15秒)"
    except Exception as e:
        return f"错误: {e}"


class GitTool(BaseTool):
    name = "git"
    description = (
        "查看 Git 仓库信息: 状态(status)、文件差异(diff)、提交记录(log)、"
        "查看某次提交(show)。只读操作, 不支持 commit/push。"
        "需要在 Git 仓库目录下使用。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "操作类型: status / diff / log / show / branch",
                "enum": ["status", "diff", "log", "show", "branch"],
            },
            "path": {"type": "string", "description": "Git 仓库路径, 默认当前目录"},
            "target": {"type": "string", "description": "diff: 文件名或分支名; show: commit hash; log: 不需要"},
            "max_count": {"type": "integer", "description": "log 显示的提交数, 默认 10"},
        },
        "required": ["action"],
    }

    def execute(self, action: str = "status", path: str = ".",
                target: str = "", max_count: int = 10, **kwargs) -> str:
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"

        repo = Path(path).resolve()
        if not repo.exists():
            return f"错误: 目录不存在: {path}"

        # 检查是否是 git 仓库
        git_dir = repo / ".git"
        if not git_dir.exists():
            # 向上查找
            parent = repo.parent
            found = False
            while parent != parent.parent:
                if (parent / ".git").exists():
                    repo = parent
                    found = True
                    break
                parent = parent.parent
            if not found:
                return f"错误: {path} 不是 Git 仓库 (未找到 .git 目录)"

        if action == "status":
            return _run_git(["status", "--short", "--branch"], cwd=str(repo))

        elif action == "diff":
            if target:
                return _run_git(["diff", target], cwd=str(repo))
            return _run_git(["diff"], cwd=str(repo))

        elif action == "log":
            return _run_git([
                "log", "--oneline", "--graph",
                f"-{max_count}",
                "--pretty=format:%h %ad %s [%an]",
                "--date=short",
            ], cwd=str(repo))

        elif action == "show":
            if not target:
                return "错误: show 操作需要指定 commit hash (target 参数)"
            return _run_git(["show", "--stat", target], cwd=str(repo))

        elif action == "branch":
            return _run_git(["branch", "-a", "-v"], cwd=str(repo))

        else:
            return f"错误: 不支持的操作: {action}, 可选: status/diff/log/show/branch"
