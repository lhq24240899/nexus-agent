"""
浏览器自动化工具 —— 基于 Playwright 同步 API
功能: 打开网页 / 点击 / 输入 / 抓数据 / 截图 / 关闭
连接系统已安装的 Chrome, 无需额外下载浏览器
"""
import threading
from pathlib import Path
from datetime import datetime

from tools.base_tool import BaseTool


class BrowserManager:
    """浏览器单例管理器, 保持浏览器实例在多次工具调用间存活"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._playwright = None
        self._browser = None
        self._page = None

    def _ensure_browser(self):
        """确保浏览器已启动"""
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        # 尝试连接系统 Chrome, 失败则用 Playwright 自带 Chromium
        try:
            self._browser = self._playwright.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--start-maximized"],
            )
        except Exception:
            self._browser = self._playwright.chromium.launch(
                headless=False,
                args=["--start-maximized"],
            )
        self._page = self._browser.new_page()
        return self._page

    def get_page(self):
        return self._ensure_browser()

    def close(self):
        """关闭浏览器"""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _resolve_locator(self, page, selector: str):
        """解析选择器: 支持 CSS 选择器、text=文本、role=类型"""
        selector = selector.strip()
        if selector.startswith("text="):
            return page.get_by_text(selector[5:].strip())
        if selector.startswith("role="):
            parts = selector[5:].split(":", 1)
            role = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else None
            if name:
                return page.get_by_role(role, name=name)
            return page.get_by_role(role)
        # 默认 CSS 选择器
        return page.locator(selector)


class BrowserOpenTool(BaseTool):
    """打开网页"""
    name = "browser_open"
    description = "在Chrome浏览器中打开指定网址。浏览器窗口会保持打开, 后续操作(browser_click/browser_type等)在同一窗口中执行。"
    params_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网址, 如 https://www.baidu.com"},
            "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"], "description": "等待加载状态, 默认 load"},
        },
        "required": ["url"],
    }

    def execute(self, url: str, wait_until: str = "load", **kwargs) -> str:
        try:
            mgr = BrowserManager()
            page = mgr.get_page()
            page.goto(url, wait_until=wait_until, timeout=30000)
            title = page.title()
            return f"已打开: {url}\n页面标题: {title}\n当前URL: {page.url}"
        except Exception as e:
            return f"错误: 打开网页失败: {e}"


class BrowserClickTool(BaseTool):
    """点击元素"""
    name = "browser_click"
    description = "点击网页中的元素。选择器支持: CSS选择器(如 .btn, #id), text=按钮文本, role=button:名称。点击前会自动等待元素可见。"
    params_schema = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "元素选择器, 如 text=搜索, .submit-btn, #login-button"},
        },
        "required": ["selector"],
    }

    def execute(self, selector: str, **kwargs) -> str:
        try:
            mgr = BrowserManager()
            page = mgr.get_page()
            locator = mgr._resolve_locator(page, selector)
            locator.first.wait_for(state="visible", timeout=10000)
            locator.first.click()
            return f"已点击: {selector}\n当前URL: {page.url}"
        except Exception as e:
            return f"错误: 点击失败: {e}"


class BrowserTypeTool(BaseTool):
    """输入文本"""
    name = "browser_type"
    description = "在网页输入框中输入文本。选择器支持 CSS选择器、text=占位符文本、role=textbox:名称。输入前自动清空。"
    params_schema = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "输入框选择器, 如 #search, text=搜索, role=textbox:搜索"},
            "text": {"type": "string", "description": "要输入的文本"},
            "press_enter": {"type": "boolean", "description": "输入后是否按回车提交, 默认 false"},
        },
        "required": ["selector", "text"],
    }

    def execute(self, selector: str, text: str, press_enter: bool = False, **kwargs) -> str:
        try:
            mgr = BrowserManager()
            page = mgr.get_page()
            locator = mgr._resolve_locator(page, selector)
            locator.first.wait_for(state="visible", timeout=10000)
            locator.first.fill(text)
            if press_enter:
                locator.first.press("Enter")
            return f"已在 '{selector}' 中输入: {text[:50]}{'...' if len(text) > 50 else ''}" + (" (已按回车)" if press_enter else "")
        except Exception as e:
            return f"错误: 输入失败: {e}"


class BrowserGetTextTool(BaseTool):
    """获取页面文本/抓数据"""
    name = "browser_get_text"
    description = "获取网页内容。不传选择器时返回整个页面文本; 传选择器时返回匹配元素的文本。用于抓数据、读取搜索结果、提取页面信息。"
    params_schema = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "可选, 元素选择器。不传则返回整个页面文本。支持 CSS选择器、text=文本"},
            "max_chars": {"type": "integer", "description": "最大返回字符数, 默认 3000"},
        },
    }

    def execute(self, selector: str = "", max_chars: int = 3000, **kwargs) -> str:
        try:
            mgr = BrowserManager()
            page = mgr.get_page()
            if selector:
                locator = mgr._resolve_locator(page, selector)
                count = locator.count()
                if count == 0:
                    return f"未找到元素: {selector}"
                texts = []
                for i in range(min(count, 20)):
                    texts.append(locator.nth(i).inner_text())
                result = "\n---\n".join(texts)
            else:
                result = page.inner_text("body")
            if len(result) > max_chars:
                result = result[:max_chars] + "\n... (已截断)"
            return result if result.strip() else "(页面无文本内容)"
        except Exception as e:
            return f"错误: 获取文本失败: {e}"


class BrowserScreenshotTool(BaseTool):
    """浏览器截图"""
    name = "browser_screenshot"
    description = "对当前浏览器页面截图, 保存到 temp/ 目录, 返回文件路径。"
    params_schema = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "可选, 自定义文件名, 默认 browser_时间戳.png"},
            "full_page": {"type": "boolean", "description": "是否截取整页(滚动), 默认 false 只截可视区域"},
        },
    }

    def execute(self, filename: str = "", full_page: bool = False, **kwargs) -> str:
        try:
            mgr = BrowserManager()
            page = mgr.get_page()
            temp_dir = Path("temp")
            temp_dir.mkdir(exist_ok=True)
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"browser_{timestamp}.png"
            filepath = temp_dir / filename
            page.screenshot(path=str(filepath), full_page=full_page)
            return f"截图已保存: {filepath.resolve()}"
        except Exception as e:
            return f"错误: 截图失败: {e}"


class BrowserCloseTool(BaseTool):
    """关闭浏览器"""
    name = "browser_close"
    description = "关闭浏览器窗口, 释放资源。任务完成后应主动调用。"
    params_schema = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        try:
            BrowserManager().close()
            return "浏览器已关闭"
        except Exception as e:
            return f"错误: 关闭浏览器失败: {e}"
