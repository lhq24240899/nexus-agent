"""
Nexus 桌面端入口 —— pywebview 桌面应用
启动 Flask 后端 + 桌面窗口 + 系统托盘
用法: python desktop.py
"""
import sys
import time
import threading
import webview
from ui.web_ui import create_app
from config import WEB_CONFIG


class DesktopAPI:
    """暴露给前端的桌面端 API (通过 window.pywebview.api 调用)"""

    def choose_folder(self) -> str:
        """弹出系统文件夹选择对话框, 返回选中的目录路径 (用 tkinter, Windows 上最可靠)"""
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()  # 隐藏主窗口
        root.attributes("-topmost", True)  # 置顶
        folder = filedialog.askdirectory(title="Select Project Folder")
        root.destroy()
        return folder or ""


class NexusDesktop:
    """Nexus 桌面应用"""

    def __init__(self):
        self.app = create_app()
        self.server_thread = None
        self.window = None
        self.api = DesktopAPI()
        # 每次启动用时间戳强制 WebView2 重新加载, 彻底避免缓存旧页面
        import time as _time
        self.base_url = f"http://{WEB_CONFIG['host']}:{WEB_CONFIG['port']}/?t={int(_time.time())}"

    def _start_flask(self):
        """在后台线程启动 Flask"""
        self.app.run(
            host=WEB_CONFIG["host"],
            port=WEB_CONFIG["port"],
            debug=False,
            use_reloader=False,  # 必须关闭，否则会开两个线程
        )

    def start(self):
        """启动桌面应用"""
        # 1. 启动 Flask 后端
        self.server_thread = threading.Thread(target=self._start_flask, daemon=True)
        self.server_thread.start()

        # 2. 等待 Flask 就绪
        print("正在启动 Nexus 后端...")
        time.sleep(2)  # 给 Flask 启动时间

        # 3. 创建桌面窗口
        self.window = webview.create_window(
            title="Nexus Dual-Core Agent",
            url=self.base_url,
            width=1280,
            height=800,
            min_size=(900, 600),
            resizable=True,
            frameless=False,
            easy_drag=True,
            js_api=self.api,
        )

        # 4. 启动 GUI 主循环（阻塞）
        print(f"Nexus 桌面端已启动: {self.base_url}")
        webview.start(debug=False)


def main():
    print("=" * 50)
    print("  Nexus Dual-Core Agent - Desktop")
    print("=" * 50)
    app = NexusDesktop()
    app.start()


if __name__ == "__main__":
    main()
