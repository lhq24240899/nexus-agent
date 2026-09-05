"""
系统控制工具 —— Windows 本地设备控制
功能: 打开应用 / 音量控制 / 锁屏 / 截图 / 系统命令
"""
import os
import subprocess
import ctypes
import platform
from datetime import datetime
from pathlib import Path

from tools.base_tool import BaseTool

# 常见应用名 -> 可执行文件名/路径映射
APP_MAP = {
    # 浏览器
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    # 编辑器
    "notepad": "notepad.exe",
    "记事本": "notepad.exe",
    "vscode": "Code.exe",
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "pycharm": "pycharm64.exe",
    # 通讯
    "wechat": "WeChat.exe",
    "微信": "WeChat.exe",
    "qq": "QQ.exe",
    "dingtalk": "DingtalkLauncher.exe",
    "钉钉": "DingtalkLauncher.exe",
    # 办公
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "ppt": "POWERPNT.EXE",
    # 系统
    "calculator": "calc.exe",
    "计算器": "calc.exe",
    "explorer": "explorer.exe",
    "文件管理器": "explorer.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "任务管理器": "taskmgr.exe",
    "task manager": "taskmgr.exe",
    "设置": "SystemSettings.exe",
    "settings": "SystemSettings.exe",
    # 其他
    "spotify": "Spotify.exe",
    "网易云音乐": "cloudmusic.exe",
}

# 常见安装路径搜索顺序
APP_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
    os.path.expandvars(r"%PROGRAMFILES%"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%"),
    os.path.expandvars(r"%APPDATA%"),
]


def _find_app_path(exe_name: str) -> str | None:
    """在常见路径中查找可执行文件"""
    # 先检查 PATH
    try:
        result = subprocess.run(["where", exe_name], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0].strip()
    except Exception:
        pass

    # 搜索常见安装目录
    for base in APP_PATHS:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            # 限制搜索深度, 避免太慢
            depth = root.replace(base, "").count(os.sep)
            if depth > 3:
                dirs.clear()
                continue
            if exe_name.lower() in [f.lower() for f in files]:
                return os.path.join(root, exe_name)
    return None


class OpenAppTool(BaseTool):
    """打开应用程序"""
    name = "open_app"
    description = "打开Windows本地应用程序。支持常见应用: chrome/edge/firefox浏览器, notepad/vscode/pycharm编辑器, wechat/qq/dingtalk通讯, word/excel/ppt办公, calculator计算器, explorer文件管理器, cmd/powershell终端等。也可以直接传入可执行文件名或完整路径。"
    params_schema = {
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "应用名称, 如 chrome, notepad, wechat, 或完整路径 C:\\...\\app.exe"},
            "args": {"type": "string", "description": "可选, 启动参数, 如打开网址 https://... 或文件路径"},
        },
        "required": ["app"],
    }

    def execute(self, app: str, args: str = "", **kwargs) -> str:
        app_lower = app.strip().lower()

        # 完整路径直接启动
        if os.path.isfile(app):
            exe_path = app
        else:
            # 查映射表
            exe_name = APP_MAP.get(app_lower, app)
            exe_path = _find_app_path(exe_name)
            if not exe_path:
                # 尝试直接用 start 命令 (Windows 会搜索 PATH 和注册表)
                try:
                    cmd = f'start "" "{app}"'
                    if args:
                        cmd += f' {args}'
                    subprocess.Popen(cmd, shell=True)
                    return f"已尝试启动: {app}"
                except Exception as e:
                    return f"错误: 找不到应用 '{app}', 请提供完整路径。原始错误: {e}"

        try:
            if args:
                subprocess.Popen([exe_path, args])
            else:
                subprocess.Popen(exe_path)
            return f"已打开: {app} ({exe_path})"
        except Exception as e:
            return f"错误: 启动 '{app}' 失败: {e}"


class VolumeControlTool(BaseTool):
    """音量控制"""
    name = "volume_control"
    description = "控制Windows系统音量。支持设置音量百分比、静音/取消静音、获取当前音量。"
    params_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "mute", "unmute", "get"], "description": "操作类型: set=设置音量, mute=静音, unmute=取消静音, get=获取当前音量"},
            "level": {"type": "integer", "description": "音量百分比 0-100, action=set 时必填"},
        },
        "required": ["action"],
    }

    def _get_volume_interface(self):
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        # pycaw 新版直接提供 EndpointVolume 属性
        return devices.EndpointVolume

    def execute(self, action: str, level: int = None, **kwargs) -> str:
        try:
            volume = self._get_volume_interface()
        except Exception as e:
            return f"错误: 无法访问音频设备: {e}"

        if action == "set":
            if level is None:
                return "错误: set 操作需要 level 参数 (0-100)"
            level = max(0, min(100, level))
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            return f"音量已设置为 {level}%"

        elif action == "mute":
            volume.SetMute(1, None)
            return "已静音"

        elif action == "unmute":
            volume.SetMute(0, None)
            return "已取消静音"

        elif action == "get":
            current = int(volume.GetMasterVolumeLevelScalar() * 100)
            muted = volume.GetMute()
            status = "已静音" if muted else "正常"
            return f"当前音量: {current}% ({status})"

        return f"错误: 未知操作 '{action}'"


class LockScreenTool(BaseTool):
    """锁屏"""
    name = "lock_screen"
    description = "锁定Windows屏幕。用户需要重新登录才能使用电脑。"
    params_schema = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        try:
            ctypes.windll.user32.LockWorkStation()
            return "已锁定屏幕"
        except Exception as e:
            return f"错误: 锁屏失败: {e}"


class ScreenshotTool(BaseTool):
    """截图"""
    name = "screenshot"
    description = "截取当前屏幕截图, 保存到 temp/ 目录, 返回文件路径。"
    params_schema = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "可选, 自定义文件名, 默认 screenshot_时间戳.png"},
        },
    }

    def execute(self, filename: str = "", **kwargs) -> str:
        try:
            from PIL import ImageGrab
        except ImportError:
            return "错误: PIL 未安装, 无法截图。请运行 pip install pillow"

        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)

        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"

        filepath = temp_dir / filename
        try:
            img = ImageGrab.grab()
            img.save(str(filepath))
            return f"截图已保存: {filepath.resolve()} (分辨率: {img.size[0]}x{img.size[1]})"
        except Exception as e:
            return f"错误: 截图失败: {e}"


class SystemInfoTool(BaseTool):
    """系统信息"""
    name = "system_info"
    description = "获取当前系统信息: 操作系统、CPU、内存、磁盘、运行时间等。"
    params_schema = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        lines = []
        lines.append(f"系统: {platform.system()} {platform.release()} ({platform.version()})")
        lines.append(f"架构: {platform.machine()}")
        lines.append(f"处理器: {platform.processor() or '未知'}")
        lines.append(f"主机名: {platform.node()}")

        try:
            import psutil
            mem = psutil.virtual_memory()
            lines.append(f"内存: {mem.total // (1024**3)}GB 总计, {mem.available // (1024**3)}GB 可用 ({mem.percent}% 已用)")
            disk = psutil.disk_usage(os.path.expandvars("%SYSTEMDRIVE%"))
            lines.append(f"系统盘: {disk.total // (1024**3)}GB 总计, {disk.free // (1024**3)}GB 可用 ({disk.percent}% 已用)")
            lines.append(f"CPU使用率: {psutil.cpu_percent(interval=0.5)}%")
        except ImportError:
            lines.append("(psutil 未安装, 无法获取内存/磁盘信息)")

        return "\n".join(lines)


class RunCommandTool(BaseTool):
    """执行 Windows 命令 (安全限制版)"""
    name = "run_command"
    description = "执行Windows系统命令 (cmd/powershell)。用于系统管理操作, 如查看进程、管理服务、网络诊断等。危险操作(删除/格式化/修改注册表)会被拦截。"
    params_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令, 如 tasklist, ipconfig, netstat, dir 等"},
            "shell": {"type": "string", "enum": ["cmd", "powershell"], "description": "命令解释器, 默认 cmd"},
            "timeout": {"type": "integer", "description": "超时秒数, 默认 15"},
        },
        "required": ["command"],
    }

    # 危险命令黑名单
    DANGEROUS = [
        "format", "del ", "rmdir", "rd ", "erase",
        "reg add", "reg delete", "reg import",
        "shutdown", "restart-computer", "stop-computer",
        "taskkill", "stop-process",
        "diskpart", "chkdsk /f",
        "net user", "net localgroup",
        "icacls", "cacls",
    ]

    def execute(self, command: str, shell: str = "cmd", timeout: int = 15, **kwargs) -> str:
        cmd_lower = command.lower().strip()

        # 安全检查
        for danger in self.DANGEROUS:
            if danger in cmd_lower:
                return f"错误: 命令 '{command}' 包含危险操作 '{danger}', 已被拦截。如需执行请用户手动确认。"

        try:
            if shell == "powershell":
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True, text=True, timeout=timeout, encoding="gbk", errors="replace"
                )
            else:
                result = subprocess.run(
                    ["cmd", "/c", command],
                    capture_output=True, text=True, timeout=timeout, encoding="gbk", errors="replace"
                )

            output = result.stdout or ""
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"

            # 截断过长输出
            if len(output) > 3000:
                output = output[:3000] + "\n... (输出已截断)"

            if result.returncode != 0:
                output += f"\n[退出码: {result.returncode}]"

            return output if output.strip() else "(命令执行完成, 无输出)"

        except subprocess.TimeoutExpired:
            return f"错误: 命令执行超时 ({timeout}秒)"
        except Exception as e:
            return f"错误: 命令执行失败: {e}"
