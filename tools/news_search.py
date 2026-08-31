"""新闻搜索工具 —— 专门获取热门新闻
优先用 Tavily 新闻搜索, 回退到 RSS 源 (新浪、澎湃等)
"""
import requests
from tools.base_tool import BaseTool
from config import SEARCH_CONFIG


class NewsSearchTool(BaseTool):
    name = "news_search"
    description = (
        "搜索最新热门新闻。用于获取今日热点、时事资讯、行业动态等。"
        "返回新闻标题、摘要、来源和链接。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "新闻关键词, 如 '科技' '经济' '今日热点'。不填则返回综合热门新闻",
            },
            "count": {
                "type": "integer",
                "description": "返回新闻条数, 默认 5 条",
                "default": 5,
            },
        },
        "required": [],
    }

    # 备用 RSS 源
    RSS_SOURCES = [
        ("新浪新闻", "https://feedx.net/rss/sinanews.xml"),
        ("澎湃新闻", "https://feedx.net/rss/thepaper.xml"),
        ("36氪", "https://36kr.com/feed"),
    ]

    def execute(self, query: str = "", count: int = 5, **kwargs) -> str:
        # 1. 优先用 Tavily 新闻搜索
        tavily_result = self._search_tavily_news(query, count)
        if tavily_result and not tavily_result.startswith("失败"):
            return tavily_result

        # 2. 回退到 RSS 源
        rss_result = self._fetch_rss(count)
        if rss_result:
            return f"[来源: RSS]\n{rss_result}"

        return "错误: 所有新闻源均不可用"

    def _search_tavily_news(self, query: str, count: int) -> str:
        """用 Tavily API 搜索新闻"""
        api_key = SEARCH_CONFIG.get("tavily_api_key")
        if not api_key:
            return "失败: 未配置 TAVILY_API_KEY"

        search_query = query or "今日热门新闻 头条"
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": search_query,
                    "search_depth": "basic",
                    "max_results": count,
                    "include_answer": True,
                    "topic": "news",
                    "days": 1,  # 只搜最近 1 天的新闻
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            lines = []
            if data.get("answer"):
                lines.append(f"新闻摘要: {data['answer']}\n")

            for i, r in enumerate(data.get("results", [])[:count]):
                title = r.get("title", "无标题")
                content = r.get("content", "")[:150]
                url = r.get("url", "")
                published = r.get("published_date", "")
                source = r.get("source", "")
                lines.append(f"{i+1}. {title}")
                if published:
                    lines.append(f"   时间: {published}")
                if source:
                    lines.append(f"   来源: {source}")
                if content:
                    lines.append(f"   摘要: {content}")
                lines.append(f"   链接: {url}\n")

            if not lines:
                return "失败: Tavily 新闻搜索返回空结果"
            return "\n".join(lines)
        except Exception as e:
            return f"失败: Tavily 新闻搜索异常: {e}"

    def _fetch_rss(self, count: int) -> str:
        """从 RSS 源获取新闻"""
        try:
            import feedparser
        except ImportError:
            feedparser = None

        all_news = []
        for source_name, rss_url in self.RSS_SOURCES:
            try:
                if feedparser:
                    feed = feedparser.parse(rss_url)
                    for entry in feed.entries[:count]:
                        all_news.append({
                            "title": entry.get("title", ""),
                            "link": entry.get("link", ""),
                            "summary": entry.get("summary", "")[:150],
                            "source": source_name,
                            "published": entry.get("published", ""),
                        })
                else:
                    # 没有 feedparser 时用 requests + 简单解析
                    resp = requests.get(rss_url, timeout=10,
                                        headers={"User-Agent": "Mozilla/5.0"})
                    resp.raise_for_status()
                    import re
                    items = re.findall(
                        r"<item>(.*?)</item>", resp.text, re.DOTALL
                    )
                    for item in items[:count]:
                        title = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
                        link = re.search(r"<link>(.*?)</link>", item, re.DOTALL)
                        desc = re.search(r"<description>(.*?)</description>", item, re.DOTALL)
                        all_news.append({
                            "title": title.group(1) if title else "",
                            "link": link.group(1) if link else "",
                            "summary": (desc.group(1) if desc else "")[:150],
                            "source": source_name,
                            "published": "",
                        })
            except Exception:
                continue

        if not all_news:
            return ""

        lines = []
        for i, news in enumerate(all_news[:count]):
            lines.append(f"{i+1}. {news['title']}")
            if news["source"]:
                lines.append(f"   来源: {news['source']}")
            if news["summary"]:
                import re
                clean = re.sub(r"<[^>]+>", "", news["summary"]).strip()
                lines.append(f"   摘要: {clean}")
            if news["link"]:
                lines.append(f"   链接: {news['link']}")
            lines.append("")
        return "\n".join(lines)
