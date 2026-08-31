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
        self.cwd = "~"  # 工作目录状态, 支持 cd 保持
        self._init_env()

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

    def _wrap_command(self, command: str) -> str:
        """
        包装命令: 处理 cd + 保持工作目录 + 加载常用 alias
        返回 (包装后的命令, 是否是纯cd命令)
        """
        stripped = command.strip()

        # 处理 cd 命令
        if stripped.startswith("cd ") or stripped == "cd":
            parts = stripped.split(maxsplit=1)
            target = parts[1] if len(parts) > 1 else "~"
            # 展开 ~
            if target.startswith("~"):
                target = "$HOME" + target[1:]
            self.cwd = target
            # cd 命令本身不输出, 但执行一下确认目录存在
            return f"cd {self.cwd} && pwd", True

        # 普通命令: 先 cd 到工作目录, 定义 ll alias, 再执行
        wrapped = (
            f"cd {self.cwd} 2>/dev/null || cd ~; "
            f"alias ll='ls -alF' 2>/dev/null; "
            f"{command}"
        )
        return wrapped, False

    def exec(self, command: str, timeout: int = 30) -> dict:
        """在嵌入的 Linux 中执行命令 (保持工作目录)"""
        wrapped, is_cd = self._wrap_command(command)
        if self.mode == "docker":
            result = self._exec_docker(wrapped, timeout)
        elif self.mode == "wsl":
            result = self._exec_wsl(wrapped, timeout)
        elif self.mode == "mock":
            result = self._exec_mock(command)
        else:
            return {"ok": False, "error": "无可用 Linux 环境"}

        # cd 命令成功后, 把 cwd 解析为绝对路径
        if is_cd and result.get("ok") and result.get("stdout"):
            self.cwd = result["stdout"].strip()
            result["stdout"] = ""  # cd 不显示 pwd 输出

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
