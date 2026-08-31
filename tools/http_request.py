"""HTTP 请求工具 —— 调用任意 API 或获取网页内容
支持 HTML 解析 (BeautifulSoup), 自动提取标题/正文/链接
"""
import requests
from tools.base_tool import BaseTool


class HttpRequestTool(BaseTool):
    name = "http_request"
    description = (
        "发送 HTTP 请求获取网页内容或调用 API。支持 GET/POST/PUT/DELETE。"
        "获取网页时可自动解析 HTML 提取正文和链接 (parse_html=true)。"
        "用于获取实时信息、调用第三方 API、抓取网页等。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "请求 URL"},
            "method": {
                "type": "string",
                "description": "HTTP 方法",
                "enum": ["GET", "POST", "PUT", "DELETE"],
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "请求头 (JSON 对象)",
            },
            "body": {
                "type": "string",
                "description": "请求体 (POST/PUT 时使用)",
            },
            "parse_html": {
                "type": "boolean",
                "description": "是否自动解析 HTML, 提取标题/正文/链接 (网页抓取时建议 true)",
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数, 默认 15",
                "default": 15,
            },
        },
        "required": ["url"],
    }

    def execute(self, url: str = "", method: str = "GET",
                headers: dict = None, body: str = "",
                parse_html: bool = False,
                timeout: int = 15, **kwargs) -> str:
        if not url:
            return "错误: 请指定 URL"
        if not url.startswith(("http://", "https://")):
            return "错误: URL 必须以 http:// 或 https:// 开头"

        try:
            kwargs = {"timeout": timeout, "headers": headers or {}}
            if method.upper() == "GET":
                resp = requests.get(url, **kwargs)
            elif method.upper() == "POST":
                kwargs["data"] = body
                resp = requests.post(url, **kwargs)
            elif method.upper() == "PUT":
                kwargs["data"] = body
                resp = requests.put(url, **kwargs)
            elif method.upper() == "DELETE":
                resp = requests.delete(url, **kwargs)
            else:
                return f"错误: 不支持的方法 {method}"

            result = f"状态码: {resp.status_code}\n"

            # 如果是 HTML 且要求解析, 用 BeautifulSoup 提取
            content_type = resp.headers.get("Content-Type", "")
            if parse_html and ("html" in content_type or "<html" in resp.text[:500].lower()):
                result += self._parse_html(resp.text)
            else:
                content = resp.text
                if len(content) > 5000:
                    content = content[:5000] + "\n... (内容过长, 已截断)"
                result += f"响应内容:\n{content}"

            return result
        except requests.exceptions.Timeout:
            return f"错误: 请求超时 ({timeout}秒)"
        except requests.exceptions.ConnectionError:
            return f"错误: 无法连接到 {url}"
        except Exception as e:
            return f"请求失败: {type(e).__name__}: {e}"

    @staticmethod
    def _parse_html(html: str) -> str:
        """用 BeautifulSoup 解析 HTML, 提取标题、正文、链接"""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return "[错误] 未安装 beautifulsoup4, 无法解析 HTML"

        soup = BeautifulSoup(html, "lxml")

        # 移除脚本和样式
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        lines = []

        # 标题
        title = soup.find("title")
        if title and title.get_text(strip=True):
            lines.append(f"标题: {title.get_text(strip=True)}")

        # 所有 h1-h3 标题
        headings = []
        for h in soup.find_all(["h1", "h2", "h3"]):
            text = h.get_text(strip=True)
            if text and len(text) < 200:
                headings.append(text)
        if headings:
            lines.append(f"\n主要标题:\n" + "\n".join(f"  - {h}" for h in headings[:10]))

        # 正文 (尝试找 article/main 或 body 中的段落)
        main_content = soup.find("article") or soup.find("main") or soup.find("div", class_=lambda x: x and "content" in str(x).lower()) or soup.body
        if main_content:
            paragraphs = []
            for p in main_content.find_all("p"):
                text = p.get_text(strip=True)
                if text and len(text) > 20:
                    paragraphs.append(text)
            if paragraphs:
                body_text = "\n".join(paragraphs[:15])
                if len(body_text) > 3000:
                    body_text = body_text[:3000] + "\n... (正文过长, 已截断)"
                lines.append(f"\n正文:\n{body_text}")

        # 链接
        links = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            if text and href.startswith("http") and len(text) < 100:
                links.append(f"{text} -> {href}")
        if links:
            lines.append(f"\n相关链接 (前10条):\n" + "\n".join(f"  - {l}" for l in links[:10]))

        if len(lines) <= 1:
            # 如果没提取到什么, 返回纯文本
            text = soup.get_text(separator="\n", strip=True)
            text = "\n".join(line for line in text.split("\n") if line.strip())
            if len(text) > 3000:
                text = text[:3000] + "\n... (已截断)"
            lines.append(f"\n页面文本:\n{text}")

        return "\n".join(lines)
