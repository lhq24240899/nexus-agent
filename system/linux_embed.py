"""
Linux 系统嵌入 —— 对应视频中"把一个完整的 Linux 系统嵌入到 Nexus 肚子里"
支持三种模式 (自动检测):
  1. docker: 运行 Ubuntu 容器 (推荐, 最接近视频效果)
  2. wsl:    使用 Windows Subsystem for Linux
  3. mock:   模拟终端 (无 Docker/WSL 时的演示模式)

嵌入的 Linux 提供: 浏览器、终端、文件系统, Agent 可以直接操作
支持工作目录保持 (cd 后后续命令在新目录执行)
"""
import subprocess
import shlex
import shutil
import os
from config import LINUX_CONFIG
from utils.logger import logger


class LinuxEmbed:
    """在 Nexus 内部嵌入一个可用的 Linux 环境"""

    def __init__(self):
        self.mode = self._detect_mode()
        self.available = self.mode != "none"
        self.container_name = LINUX_CONFIG["container_name"]
        # 默认工作目录: WSL 模式下用项目目录的 WSL 路径, 避免 Agent 把文件建到 ~
        self.cwd = self._default_cwd()
        self._init_env()

    @staticmethod
    def _win_to_wsl_path(win_path: str) -> str:
        """Windows 路径转 WSL 路径: D:/project -> /mnt/d/project"""
        if not win_path or ":" not in win_path:
            return win_path
        drive = win_path[0].lower()
        rest = win_path[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"

    def _default_cwd(self) -> str:
        """根据模式返回默认工作目录"""
        if self.mode == "wsl":
            # WSL 模式: 用当前项目目录的 WSL 路径
            return self._win_to_wsl_path(os.getcwd())
        # docker/mock 模式用 ~
        return "~"

    def _detect_mode(self) -> str:
        """自动检测可用的 Linux 嵌入方式"""
        mode = LINUX_CONFIG["mode"]
        if mode in ("docker", "wsl", "mock"):
            return mode
        if shutil.which("docker"):
            try:
                r = subprocess.run(["docker", "info"], capture_output=True,
                                   timeout=5, text=True)
                if r.returncode == 0:
                    return "docker"
            except Exception:
                pass
        if shutil.which("wsl"):
            try:
                r = subprocess.run(["wsl", "--status"], capture_output=True,
                                   timeout=5, text=True)
                if r.returncode == 0:
                    return "wsl"
            except Exception:
                pass
        return "mock"

    def _init_env(self):
        """初始化 Linux 环境"""
        if self.mode == "docker":
            self._init_docker()
        elif self.mode == "wsl":
            distro = LINUX_CONFIG.get("wsl_distro", "Ubuntu")
            logger.log("linux", "使用 WSL2", f"发行版: {distro}")
        elif self.mode == "mock":
            logger.log("linux", "使用模拟模式",
                       "无 Docker/WSL, 仅演示基本命令")

    def _init_docker(self):
        """启动/创建 Ubuntu 容器"""
        try:
            r = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}",
                 self.container_name],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and "true" in r.stdout:
                logger.log("linux", "Docker 容器已运行", self.container_name)
                return
            if r.returncode == 0:
                subprocess.run(["docker", "start", self.container_name],
                               capture_output=True, timeout=10)
                logger.log("linux", "Docker 容器已启动", self.container_name)
            else:
                subprocess.run(
                    ["docker", "run", "-d", "--name", self.container_name,
                     "--restart", "unless-stopped",
                     LINUX_CONFIG["image"], "sleep", "infinity"],
                    capture_output=True, timeout=60,
                )
                self.exec("apt-get update && apt-get install -y "
                          "curl wget vim nano python3 python3-pip")
                logger.log("linux", "Docker 容器已创建",
                           f"{LINUX_CONFIG['image']} -> {self.container_name}")
        except Exception as e:
            logger.log("linux", "Docker 初始化失败", str(e))
            self.mode = "mock"
            self.available = True

    CWD_MARKER = "__NEXUS_CWD__"

    def _wrap_command(self, command: str) -> str:
        """
        包装命令: 保持工作目录 + 加载常用 alias + 追加 cwd 标记
        所有命令(含命令链中的cd)执行后都能正确更新工作目录
        """
        # 所有命令: 先 cd 到当前目录, 开 alias 展开, 定义 ll, 执行命令, 最后输出标记+pwd
        wrapped = (
            f"cd {self.cwd} 2>/dev/null || cd ~; "
            f"shopt -s expand_aliases 2>/dev/null; "
            f"alias ll='ls -alF' 2>/dev/null; "
            f"{command}; "
            f"echo {self.CWD_MARKER}; pwd"
        )
        return wrapped

    def exec(self, command: str, timeout: int = 30) -> dict:
        """在嵌入的 Linux 中执行命令 (保持工作目录)"""
        wrapped = self._wrap_command(command)
        if self.mode == "docker":
            result = self._exec_docker(wrapped, timeout)
        elif self.mode == "wsl":
            result = self._exec_wsl(wrapped, timeout)
        elif self.mode == "mock":
            result = self._exec_mock(command)
            result["cwd"] = self.cwd
            return result
        else:
            return {"ok": False, "error": "无可用 Linux 环境"}

        # 从输出中解析 cwd 标记行, 下一行就是 pwd 输出 (支持命令链中的 cd)
        stdout = result.get("stdout", "")
        lines = stdout.split("\n")
        marker_idx = None
        for i, line in enumerate(lines):
            if line.strip() == self.CWD_MARKER:
                marker_idx = i
                break
        if marker_idx is not None and marker_idx + 1 < len(lines):
            self.cwd = lines[marker_idx + 1].strip()
            # 去掉标记行和 pwd 行
            result["stdout"] = "\n".join(lines[:marker_idx]).rstrip("\n")

        # 记录当前目录到结果
        result["cwd"] = self.cwd
        return result

    def _exec_docker(self, command: str, timeout: int) -> dict:
        try:
            r = subprocess.run(
                ["docker", "exec", self.container_name,
                 "bash", "-c", command],
                capture_output=True, text=True, timeout=timeout,
            )
            return {
                "ok": r.returncode == 0,
                "stdout": r.stdout,
                "stderr": r.stderr,
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "命令超时", "exit_code": -1}
        except Exception as e:
            return {"ok": False, "error": str(e), "exit_code": -1}

    def _exec_wsl(self, command: str, timeout: int) -> dict:
        distro = LINUX_CONFIG.get("wsl_distro", "Ubuntu")
        try:
            r = subprocess.run(
                ["wsl", "-d", distro, "--", "bash", "-c", command],
                capture_output=True, text=True, timeout=timeout,
            )
            return {
                "ok": r.returncode == 0,
                "stdout": r.stdout,
                "stderr": r.stderr,
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "命令超时", "exit_code": -1}
        except Exception as e:
            return {"ok": False, "error": str(e), "exit_code": -1}

    def _exec_mock(self, command: str) -> dict:
        """模拟 Linux 终端, 支持基本命令用于演示"""
        cmd = command.strip().lower()
        if cmd.startswith("ls"):
            return {"ok": True, "stdout": "bin  dev  etc  home  lib  "
                    "mnt  opt  proc  root  run  sbin  sys  tmp  usr  var\n",
                    "stderr": "", "exit_code": 0}
        if cmd.startswith("pwd"):
            return {"ok": True, "stdout": "/home/nexus\n", "stderr": "", "exit_code": 0}
        if cmd.startswith("whoami"):
            return {"ok": True, "stdout": "nexus\n", "stderr": "", "exit_code": 0}
        if cmd.startswith("uname"):
            return {"ok": True, "stdout": "Linux nexus 5.15.0-generic "
                    "#1 SMP x86_64 GNU/Linux\n", "stderr": "", "exit_code": 0}
        if cmd.startswith("echo "):
            text = command[5:]
            return {"ok": True, "stdout": text + "\n", "stderr": "",
                    "exit_code": 0}
        if cmd.startswith("cat "):
            return {"ok": True, "stdout": "(模拟模式: 文件内容不可用, "
                    "请使用 Docker 或 WSL 获得真实环境)\n",
                    "stderr": "", "exit_code": 0}
        if cmd.startswith("mkdir "):
            return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}
        if cmd.startswith("cd"):
            return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}
        return {
            "ok": True,
            "stdout": f"(模拟模式) 已接收命令: {command}\n"
                      "提示: 安装 Docker 或 WSL2 可获得真实 Linux 环境\n",
            "stderr": "",
            "exit_code": 0,
        }

    def info(self) -> dict:
        """获取嵌入 Linux 的系统信息"""
        if self.mode == "mock":
            return {
                "mode": "mock (模拟)",
                "os": "Ubuntu 24.04 (模拟)",
                "note": "安装 Docker Desktop 或 WSL2 可获得真实环境",
            }
        result = self.exec("uname -a && cat /etc/os-release | head -3 && whoami && pwd")
        return {
            "mode": self.mode,
            "details": result.get("stdout", ""),
            "available": self.available,
            "cwd": self.cwd,
        }

    def cleanup(self):
        """清理 (不删除容器, 只停止)"""
        if self.mode == "docker":
            subprocess.run(["docker", "stop", self.container_name],
                           capture_output=True, timeout=10)
            logger.log("linux", "容器已停止", self.container_name)
