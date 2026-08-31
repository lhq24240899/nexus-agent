"""
知识库自动更新 —— 抓取网页内容, 摘要后入库
对应视频中秘书"在后台创立并整理知识库"
"""
import re
import requests
from utils.logger import logger


class KnowledgeUpdater:
    """从网页抓取内容, 用 LLM 摘要后加入知识库"""

    def __init__(self, secretary_core):
        self.secretary = secretary_core

    def fetch_url(self, url: str) -> str:
        """抓取网页正文文本"""
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36"
            })
            resp.raise_for_status()
            # 简单提取正文: 去标签, 去脚本样式
            text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:8000]  # 限制长度
        except Exception as e:
            return f"抓取失败: {str(e)}"

    def summarize(self, content: str, url: str) -> str:
        """用秘书 LLM 摘要内容"""
        if not self.secretary.configured:
            return content[:500]
        try:
            resp = self.secretary.client.chat.completions.create(
                model=self.secretary.model,
                temperature=0.3,
                messages=[
                    {"role": "system", "content":
                     "你是知识库管理员。请将以下网页内容摘要为3-5条关键知识点, "
                     "每条不超过50字, 格式为纯文本列表。不要编造, 只基于原文。"},
                    {"role": "user", "content": f"来源: {url}\n\n内容:\n{content[:6000]}"},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.log("secretary", "摘要失败", str(e))
            return content[:500]

    def add_url_to_knowledge(self, url: str) -> dict:
        """抓取网页 → 摘要 → 入库"""
        logger.log("secretary", "知识库更新", f"抓取: {url[:60]}")
        content = self.fetch_url(url)
        if content.startswith("抓取失败"):
            return {"ok": False, "error": content}

        summary = self.summarize(content, url)
        entry = self.secretary.libs.knowledge.add(
            f"[来源: {url}]\n{summary}",
            meta={"type": "web_import", "url": url},
        )
        logger.log("secretary", "知识库已更新",
                   f"新增条目 #{entry['id']}")
        return {
            "ok": True,
            "url": url,
            "entry_id": entry["id"],
            "summary": summary,
        }

    def batch_add(self, urls: list[str]) -> list[dict]:
        """批量添加多个 URL"""
        results = []
        for url in urls:
            results.append(self.add_url_to_knowledge(url))
        return results

    # ========== 后台自动更新 ==========
    def start_auto_update(self, interval_hours: float = 6.0):
        """启动后台定时更新线程"""
        import threading
        import time
        self._auto_update_running = True
        self._auto_update_interval = interval_hours * 3600

        def _loop():
            while self._auto_update_running:
                try:
                    self._check_and_update()
                except Exception as e:
                    logger.log("secretary", "自动更新异常", str(e))
                # 分段睡眠, 支持快速停止
                for _ in range(int(self._auto_update_interval / 10)):
                    if not self._auto_update_running:
                        break
                    time.sleep(10)

        self._auto_update_thread = threading.Thread(
            target=_loop, daemon=True, name="knowledge-auto-update"
        )
        self._auto_update_thread.start()
        logger.log("secretary", "知识库自动更新已启动",
                   f"间隔 {interval_hours} 小时")

    def stop_auto_update(self):
        """停止后台自动更新"""
        self._auto_update_running = False
        logger.log("secretary", "知识库自动更新已停止", "")

    def _check_and_update(self):
        """检查配置的 URL 并更新知识库"""
        import json
        from pathlib import Path
        from config import DATA_DIR
        config_file = DATA_DIR / "auto_update_urls.json"
        if not config_file.exists():
            # 写入默认配置
            config_file.write_text(json.dumps({
                "urls": [],
                "_comment": "添加需要定期抓取的 URL, 例如技术文档、博客 RSS"
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            urls = config.get("urls", [])
        except Exception:
            return
        if not urls:
            return
        logger.log("secretary", "自动更新开始", f"待抓取 {len(urls)} 个 URL")
        for url in urls:
            try:
                self.add_url_to_knowledge(url)
            except Exception as e:
                logger.log("secretary", "自动更新失败", f"{url[:50]}: {e}")
        logger.log("secretary", "自动更新完成", f"已处理 {len(urls)} 个 URL")

    def add_auto_update_url(self, url: str) -> dict:
        """添加自动更新 URL"""
        import json
        from pathlib import Path
        from config import DATA_DIR
        config_file = DATA_DIR / "auto_update_urls.json"
        config = {"urls": []}
        if config_file.exists():
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        if url not in config.get("urls", []):
            config.setdefault("urls", []).append(url)
            config_file.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return {"ok": True, "url": url, "total_urls": len(config["urls"])}
