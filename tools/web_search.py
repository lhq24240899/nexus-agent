"""联网搜索工具 —— 多源搜索, 优先 Tavily (国内可用), 回退 Serper/百度/DuckDuckGo"""
import re
import requests
from tools.base_tool import BaseTool
from config import SEARCH_CONFIG


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "联网搜索信息。支持多搜索引擎(Serper/Baidu/Tavily)。【何时用】需要实时信息(新闻/股价/最新文档)、不确定的事实核查、查找API用法。【不要用】本地代码问题用code_search/file_read；数学计算用code_exec；已知信息直接回答不要搜索。搜索结果是摘要，需要详细内容时用http_request抓取具体网页。"
    params_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或查询语句",
            }
        },
        "required": ["query"],
    }

    def execute(self, query: str = "", **kwargs) -> str:
        if not query:
            return "错误: 请输入搜索关键词"

        # 按优先级尝试多个搜索源
        sources = [
            ("tavily", self._search_tavily),
            ("serper", self._search_serper),
            ("baidu", self._search_baidu),
            ("duckduckgo", self._search_duckduckgo),
        ]

        errors = []
        for source_name, search_fn in sources:
            try:
                result = search_fn(query)
                if result and not result.startswith("失败"):
                    return f"[来源: {source_name}]\n{result}"
                errors.append(f"{source_name}: {result}")
            except Exception as e:
                errors.append(f"{source_name}: {e}")

        return f"所有搜索源均失败:\n" + "\n".join(errors)

    def _search_tavily(self, query: str) -> str:
        """Tavily 搜索 (推荐, 专为 AI 设计, 返回结构化结果)"""
        api_key = SEARCH_CONFIG.get("tavily_api_key")
        if not api_key:
            return "失败: 未配置 TAVILY_API_KEY"
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = []
        if data.get("answer"):
            lines.append(f"摘要: {data['answer']}")
        for i, r in enumerate(data.get("results", [])[:5]):
            title = r.get("title", "")
            content = r.get("content", "")[:200]
            url = r.get("url", "")
            lines.append(f"{i+1}. {title}\n   {content}\n   {url}")
        if not lines:
            return "失败: Tavily 返回空结果"
        return "\n".join(lines)

    def _search_serper(self, query: str) -> str:
        """Serper 搜索 (Google 结果)"""
        api_key = SEARCH_CONFIG.get("serper_api_key")
        if not api_key:
            return "失败: 未配置 SERPER_API_KEY"
        resp = requests.get(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key},
            params={"q": query, "num": 5},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = []
        for i, r in enumerate(data.get("organic", [])[:5]):
            title = r.get("title", "")
            snippet = r.get("snippet", "")[:200]
            link = r.get("link", "")
            lines.append(f"{i+1}. {title}\n   {snippet}\n   {link}")
        if not lines:
            return "失败: Serper 返回空结果"
        return "\n".join(lines)

    def _search_baidu(self, query: str) -> str:
        """百度搜索"""
        api_key = SEARCH_CONFIG.get("baidu_api_key")
        if not api_key:
            return "失败: 未配置 BAIDU_API_KEY"
        # 百度搜索 API (需要根据实际 API 调整)
        resp = requests.get(
            "https://baidu.com/s",
            params={"wd": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        # 简单解析百度搜索结果
        results = re.findall(
            r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL,
        )
        lines = []
        for i, (url, title) in enumerate(results[:5]):
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            if clean_title:
                lines.append(f"{i+1}. {clean_title}\n   {url}")
        if not lines:
            return "失败: 百度搜索解析失败"
        return "\n".join(lines)

    def _search_duckduckgo(self, query: str) -> str:
        """DuckDuckGo (无需 API key, 但国内可能超时)"""
        url = "https://html.duckduckgo.com/html/"
        resp = requests.post(url, data={"q": query}, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        results = re.findall(
            r'class="result__snippet">(.*?)</a>',
            resp.text, re.DOTALL,
        )
        if not results:
            results = re.findall(
                r'class="result__a"[^>]*>(.*?)</a>',
                resp.text, re.DOTALL,
            )
        clean = []
        for r in results[:5]:
            text = re.sub(r"<[^>]+>", "", r).strip()
            if text:
                clean.append(text)
        if not clean:
            return "失败: DuckDuckGo 无结果"
        return "\n".join(f"{i+1}. {t}" for i, t in enumerate(clean))
