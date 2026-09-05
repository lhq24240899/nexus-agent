"""
IntentAnalyzer —— 意图识别器
用轻量 LLM 调用分析任务, 判断是否需要 multi-agent, 以及建议的执行策略
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any

from config import DECISION_CONFIG
from utils.logger import logger


@dataclass
class Intent:
    """意图识别结果"""
    task_type: str = "simple_qa"           # simple_qa / coding / research / writing / device_control
    complexity: str = "low"                # low / medium / high
    needs_multi_agent: bool = False
    suggested_agents: list[str] = field(default_factory=list)
    pipeline_type: str = "single"          # single / sequential / parallel / hybrid
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "complexity": self.complexity,
            "needs_multi_agent": self.needs_multi_agent,
            "suggested_agents": self.suggested_agents,
            "pipeline_type": self.pipeline_type,
            "confidence": self.confidence,
        }


# 关键词快速匹配 (第一层, 省 API 调用)
CODING_KEYWORDS = [
    # 明确表示"要写/改代码"的词
    "写代码", "写一个", "实现", "生成代码", "创建文件", "修改代码",
    "开发", "编码", "编程", "脚本", "程序",
    # 明确表示"要修bug"的词
    "修复", "重构", "debug", "调试", "bug",
    # 工程复杂度词
    "搭建", "部署", "架构", "系统设计",
]
RESEARCH_KEYWORDS = [
    "调研", "搜索", "查一下", "最新", "对比", "分析", "研究",
    "资料", "文档", "方案", "选型", "技术", "趋势",
]
WRITING_KEYWORDS = [
    "写文章", "写小说", "写报告", "写文档", "总结", "翻译",
    "润色", "创作", "故事", "文案",
]
DEVICE_KEYWORDS = [
    "打开", "音量", "锁屏", "截图", "关机", "重启", "播放",
    "浏览器", "网页", "抓数据", "爬虫",
]


class IntentAnalyzer:
    """意图识别器: 关键词快速匹配 + LLM 深度分析"""

    SYSTEM_PROMPT = """你是任务意图分析器。分析用户任务, 输出严格的 JSON。

分类规则:
- simple_qa: 简单问答、闲聊、事实查询, 单 agent 可直接回答
- coding: 写代码、修 bug、重构、实现功能, 需要编码+审查+测试
- research: 深度调研、多源搜索、技术选型、对比分析
- writing: 写文章、小说、报告、文档、翻译润色
- device_control: 设备控制、打开应用、浏览器操作、抓数据

复杂度:
- low: 单步操作, 调一个工具就能完成
- medium: 多步骤, 需要2-3次工具调用
- high: 复杂任务, 需要多角色协作或多轮迭代

输出 JSON 格式:
{"task_type": "coding", "complexity": "medium", "needs_multi_agent": true, "suggested_agents": ["coder", "reviewer", "tester"], "pipeline_type": "sequential", "confidence": 0.9}

只输出 JSON, 不要其他文字。"""

    def __init__(self):
        self._client = None
        self._model = None
        if DECISION_CONFIG.get("api_key"):
            from openai import OpenAI
            self._client = OpenAI(
                base_url=DECISION_CONFIG["base_url"],
                api_key=DECISION_CONFIG["api_key"],
                timeout=30.0,
            )
            self._model = DECISION_CONFIG["model"]

    def analyze(self, task: str) -> Intent:
        """分析任务意图"""
        # 第一层: 关键词快速匹配
        quick = self._keyword_match(task)
        if quick and quick.complexity == "low":
            logger.log("intent", "关键词匹配", f"{task[:30]} -> {quick.task_type}")
            return quick
        # coding 任务关键词匹配已经很准, 直接返回不走 LLM (省一次 API 调用)
        if quick and quick.task_type == "coding":
            logger.log("intent", "关键词匹配(coding)", f"{task[:30]} -> coding/{quick.complexity}")
            return quick

        # 第二层: LLM 深度分析
        if self._client:
            try:
                llm_intent = self._llm_analyze(task)
                if llm_intent:
                    logger.log("intent", "LLM分析", f"{task[:30]} -> {llm_intent.task_type}/{llm_intent.complexity}")
                    return llm_intent
            except Exception as e:
                logger.log("intent", "LLM分析失败", str(e))

        # 回退: 用关键词结果
        return quick or Intent(task_type="simple_qa", complexity="low")

    def _keyword_match(self, task: str) -> Intent | None:
        """关键词快速匹配"""
        t = task.lower()

        # 设备控制 (通常简单)
        if any(k in t for k in DEVICE_KEYWORDS) and not any(k in t for k in CODING_KEYWORDS):
            return Intent(
                task_type="device_control",
                complexity="low",
                needs_multi_agent=False,
                suggested_agents=[],
                pipeline_type="single",
                confidence=0.7,
            )

        # 简单问答
        if len(task) < 20 and not any(k in t for k in CODING_KEYWORDS + RESEARCH_KEYWORDS + WRITING_KEYWORDS):
            return Intent(
                task_type="simple_qa",
                complexity="low",
                needs_multi_agent=False,
                suggested_agents=[],
                pipeline_type="single",
                confidence=0.6,
            )

        # 编码任务
        if any(k in t for k in CODING_KEYWORDS):
            complexity = "high" if any(k in t for k in ["重构", "系统", "架构", "完整", "全套"]) else "medium"
            return Intent(
                task_type="coding",
                complexity=complexity,
                needs_multi_agent=True,
                suggested_agents=["coder", "reviewer", "tester"],
                pipeline_type="sequential",
                confidence=0.8,
            )

        # 写作任务 (优先于 research, 避免"写技术文章"被误判)
        if any(k in t for k in WRITING_KEYWORDS) or re.search(r"写.*?(文章|小说|报告|文档|故事|文案|总结)", t):
            return Intent(
                task_type="writing",
                complexity="medium",
                needs_multi_agent=False,  # 写作暂时单 agent
                suggested_agents=[],
                pipeline_type="single",
                confidence=0.7,
            )

        # 调研任务
        if any(k in t for k in RESEARCH_KEYWORDS):
            return Intent(
                task_type="research",
                complexity="medium",
                needs_multi_agent=False,  # 调研暂时单 agent, 后续加 researcher
                suggested_agents=[],
                pipeline_type="single",
                confidence=0.7,
            )

        return None

    def _llm_analyze(self, task: str) -> Intent | None:
        """LLM 深度分析"""
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0.1,
                max_tokens=200,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": task},
                ],
            )
            content = resp.choices[0].message.content.strip()
            # 提取 JSON
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                return None
            data = json.loads(json_match.group())
            return Intent(
                task_type=data.get("task_type", "simple_qa"),
                complexity=data.get("complexity", "low"),
                needs_multi_agent=bool(data.get("needs_multi_agent", False)),
                suggested_agents=data.get("suggested_agents", []),
                pipeline_type=data.get("pipeline_type", "single"),
                confidence=float(data.get("confidence", 0.5)),
            )
        except Exception as e:
            try:
                err_detail = content[:100] if 'content' in locals() else ''
            except Exception:
                err_detail = ''
            logger.log("intent", "LLM解析失败", f"{e}: {err_detail}")
            return None
