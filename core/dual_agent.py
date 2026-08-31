"""
双核 Agent 总控 —— 对应视频中的完整系统
流程: 用户任务 → 秘书预判检索(含历史对话) → 决策核心决策 → 秘书沉淀+反思
支持快速通道: 简单问题跳过秘书, 直接回答
"""
import time
import json
import re
from pathlib import Path
from core.decision_core import DecisionCore
from core.secretary_core import SecretaryCore
from system.linux_embed import LinuxEmbed
from tools.tool_manager import ToolManager
from libraries.knowledge_updater import KnowledgeUpdater
from skills.skill_manager import SkillManager
from mcp.mcp_client import MCPManager
from tools.code_index import CodeIndex
from utils.logger import logger
from utils.cost_tracker import cost_tracker
from utils.task_stats import TaskStatsTracker
from config import DATA_DIR

HISTORY_FILE = DATA_DIR / "conversation_history.json"
MAX_HISTORY_TURNS = 6

# 复杂任务关键词: 命中则必须走秘书检索
COMPLEX_KEYWORDS = [
    "分析", "研究", "方案", "对比", "比较", "实现", "代码", "编程",
    "搜索", "查询", "查找", "为什么", "如何", "怎么", "怎样",
    "写", "做", "制作", "生成", "创建", "设计", "优化", "改进",
    "翻译", "总结", "摘要", "解释", "说明", "介绍", "推荐",
    "安装", "配置", "部署", "调试", "修复", "解决",
    "linux", "python", "代码", "函数", "算法", "数据",
    "帮我", "请帮", "需要", "希望",
]


def is_simple_question(task: str) -> bool:
    """判断是否简单问题 (走快速通道)"""
    if len(task) > 30:
        return False
    task_lower = task.lower()
    for kw in COMPLEX_KEYWORDS:
        if kw in task_lower:
            return False
    return True


class DualCoreAgent:
    """双核 Agent: Nexus (决策) + 秘书 (管理)"""

    def __init__(self, use_linux: bool = True):
        logger.log("system", "系统启动", "初始化双核 Agent")
        self.secretary = SecretaryCore()
        self.linux = LinuxEmbed() if use_linux else None
        if self.linux and self.linux.available:
            logger.log("linux", "Linux 环境就绪", f"模式: {self.linux.mode}")
        elif self.linux:
            logger.log("linux", "Linux 不可用", f"回退模式: {self.linux.mode}")
        self.code_index = CodeIndex()
        self.tool_manager = ToolManager(linux_embed=self.linux, code_index=self.code_index)
        self.secretary.tool_manager = self.tool_manager  # 秘书需要工具列表做筛选
        self.decision = DecisionCore(tool_manager=self.tool_manager)
        # 绑定并行工具到决策核心
        from tools.parallel_execute import ParallelExecuteTool
        ParallelExecuteTool.bind(self.decision, self.tool_manager)
        # 技能系统
        self.skill_manager = SkillManager(
            llm_client=self.secretary.client if self.secretary.configured else None,
            model=self.secretary.model,
        )
        from tools.use_skill import UseSkillTool
        UseSkillTool.bind(self.skill_manager)
        # MCP 客户端 (连接外部 MCP server, 扩展工具生态)
        self.mcp_manager = MCPManager()
        self.tool_manager.set_mcp_manager(self.mcp_manager)
        self.stats_tracker = TaskStatsTracker()
        self.knowledge_updater = KnowledgeUpdater(self.secretary)
        # 启动知识库后台自动更新 (每6小时检查一次)
        self.knowledge_updater.start_auto_update(interval_hours=6.0)
        logger.log("system", "工具已加载",
                   f"{len(self.tool_manager.list_tools())} 个工具: "
                   + ", ".join(t["name"] for t in self.tool_manager.list_tools()))
        self.task_history: list[dict] = []
        self.conversation: list[dict] = []
        self._load_conversation()

    def _load_conversation(self):
        if HISTORY_FILE.exists():
            try:
                self.conversation = json.loads(
                    HISTORY_FILE.read_text(encoding="utf-8")
                )
            except Exception:
                self.conversation = []

    def _save_conversation(self):
        HISTORY_FILE.write_text(
            json.dumps(self.conversation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 增量索引到向量库 (只索引最后两条)
        self._index_recent_history()

    def _index_recent_history(self):
        """把最近的对话增量索引到向量库, 用于长期记忆检索"""
        try:
            vs = self.secretary.libs.vector_store
            for i, msg in enumerate(self.conversation[-2:]):
                vid = f"conv_{len(self.conversation) - 2 + i}_{msg['role']}"
                if vid not in vs.documents:
                    vs.add(vid, f"[{msg['role']}] {msg['content'][:500]}")
        except Exception:
            pass  # 索引失败不影响主流程

    def search_relevant_history(self, query: str, top_k: int = 3) -> str:
        """从全部历史对话中检索相关内容 (长期记忆)"""
        try:
            vs = self.secretary.libs.vector_store
            results = vs.search(query, top_k=top_k * 2)
            relevant = []
            for vid, score in results:
                if vid.startswith("conv_") and score > 0.1:
                    content = vs.documents.get(vid, "")
                    if content:
                        relevant.append(f"  [相关度 {score:.2f}] {content[:200]}")
            if relevant:
                return "【相关历史对话】\n" + "\n".join(relevant[:top_k])
        except Exception:
            pass
        return ""

    def _recent_history(self, task: str = "") -> str:
        if not self.conversation:
            return "(无历史对话)"
        # 短期记忆: 最近 6 轮
        recent = self.conversation[-(MAX_HISTORY_TURNS * 2):]
        lines = []
        for msg in recent:
            role = "用户" if msg["role"] == "user" else "Nexus"
            lines.append(f"[{role}] {msg['content'][:200]}")
        result = "\n".join(lines)
        # 长期记忆: 检索相关历史对话 (超出 6 轮的)
        if task and len(self.conversation) > MAX_HISTORY_TURNS * 2:
            long_term = self.search_relevant_history(task)
            if long_term:
                result += "\n\n" + long_term
        return result

    def _get_skill_context(self, task: str) -> str:
        """匹配技能并返回工作流引导文本"""
        try:
            return self.skill_manager.get_skill_workflow(task)
        except Exception:
            return ""

    def _get_skill_context_with_name(self, task: str) -> tuple:
        """匹配技能并返回 (工作流文本, 技能名)"""
        try:
            skill = self.skill_manager.match_skill(task)
            if skill:
                return skill.to_workflow_text(), skill.name
            return "", None
        except Exception:
            return "", None

    def _record_and_improve_skill(self, skill_name: str, task: str, result: str,
                                   tools_used: list, duration: float):
        """记录技能使用结果, 达到阈值后自动改进"""
        try:
            success = "错误" not in result[:200] and "失败" not in result[:200]
            self.skill_manager.record_usage(skill_name, task, success, tools_used, duration)
            if self.skill_manager.should_improve(skill_name):
                improve_result = self.skill_manager.improve_skill(skill_name)
                if improve_result.get("ok"):
                    logger.log("system", "技能自我改进",
                               f"{skill_name} v{improve_result['version']}: "
                               f"{improve_result['old_steps']}→{improve_result['new_steps']}步")
        except Exception as e:
            logger.log("system", "技能改进失败", str(e))

    def _maybe_create_skill(self, task: str, result: str, tools_used: list):
        """复杂任务完成后自动创建可复用技能 (自我进化核心)"""
        try:
            # 只对多工具调用的复杂任务创建技能
            if not tools_used or len(tools_used) < 2:
                return
            # 避免重复创建 (检查是否已有相似技能)
            existing = self.skill_manager.match_skill(task, threshold=0.5)
            if existing:
                return
            # 用秘书模型生成技能定义
            skill_def = self._generate_skill_def(task, result, tools_used)
            if skill_def:
                msg = self.skill_manager.create_skill(
                    name=skill_def["name"],
                    description=skill_def["description"],
                    trigger_keywords=skill_def["keywords"],
                    steps=skill_def["steps"],
                    required_tools=tools_used,
                    tags=skill_def.get("tags", []),
                )
                logger.log("system", "技能自动创建", msg)
        except Exception as e:
            logger.log("system", "技能创建失败", str(e))

    def _generate_skill_def(self, task: str, result: str,
                            tools_used: list) -> dict | None:
        """用 LLM 从任务中提炼技能定义"""
        try:
            if not self.secretary.configured:
                return None
            steps_text = "\n".join(
                f"{i+1}. {t}" for i, t in enumerate(tools_used)
            )
            resp = self.secretary.client.chat.completions.create(
                model=self.secretary.model,
                temperature=0.2,
                messages=[{
                    "role": "system",
                    "content": """你是技能提炼专家。从一个完成的任务中提炼可复用的工作流技能。
输出严格 JSON 格式:
{"name": "技能名(英文下划线)", "description": "一句话描述", "keywords": ["触发关键词1","触发关键词2"], "steps": [{"tool": "工具名", "description": "这一步做什么"}], "tags": ["标签"]}
只输出 JSON, 不要其他内容。"""
                }, {
                    "role": "user",
                    "content": f"任务: {task[:200]}\n使用工具: {steps_text}\n结果摘要: {result[:300]}\n\n提炼技能:",
                }],
            )
            text = resp.choices[0].message.content.strip()
            # 提取 JSON
            import json as _json
            start = text.find('{')
            end = text.rfind('}') + 1
            if start >= 0 and end > start:
                return _json.loads(text[start:end])
        except Exception:
            pass
        return None

    def clear_history(self):
        self.conversation = []
        self._save_conversation()
        logger.log("system", "对话历史已清空", "")

    def add_knowledge_url(self, url: str) -> dict:
        return self.knowledge_updater.add_url_to_knowledge(url)

    def run(self, task: str, use_secretary: str = "auto") -> dict:
        """
        执行任务
        use_secretary: "auto"(自动判断) / "on"(强制秘书) / "off"(强制快速通道)
        """
        logger.log("system", "收到任务", task[:60])
        history_text = self._recent_history(task)
        t0 = time.time()

        # 判断是否走快速通道
        if use_secretary == "off":
            fast_path = True
        elif use_secretary == "on":
            fast_path = False
        else:  # auto
            fast_path = is_simple_question(task)

        if fast_path:
            # 快速通道: 跳过秘书检索和反思, 直接回答
            logger.log("system", "快速通道", "简单问题, 跳过秘书检索")
            t1 = time.time()
            context = "(快速通道: 未启用秘书检索)"
            # 技能匹配 (快速通道也匹配, 编码场景需要工作流引导)
            skill_ctx, matched_skill = self._get_skill_context_with_name(task)
            if skill_ctx:
                context += "\n\n" + skill_ctx
            # 快速通道也做工具筛选 (简单问题只传最少工具)
            allowed_tools = self.secretary._select_tools(task) if self.secretary.tool_manager else None
            result = self.decision.decide(task, context, history_text,
                                          allowed_tools=allowed_tools)
            t2 = time.time()
            reflection = "(快速通道: 未启用反思)"
            t3 = t2
            secretary_time = 0
        else:
            # 完整双核流程
            t1 = time.time()
            context, allowed_tools = self.secretary.anticipate(task, history_text)
            # 技能匹配: 注入工作流引导
            skill_ctx, matched_skill = self._get_skill_context_with_name(task)
            if skill_ctx:
                context += "\n\n" + skill_ctx
            t2 = time.time()
            result = self.decision.decide(task, context, history_text,
                                          allowed_tools=allowed_tools)
            t3 = time.time()
            tools_used = self.decision.last_tools_used
            self.secretary.record_result(task, result, tools_used=tools_used)
            reflection = self.secretary.reflect(task, result, context)
            # 技能使用记录 + 自我改进
            if matched_skill:
                self._record_and_improve_skill(matched_skill, task, result, tools_used, t3 - t2)
            # 技能使用记录 + 自我改进
            if matched_skill:
                self._record_and_improve_skill(matched_skill, task, result, tools_used, decision_time)
            # 复杂任务自动创建技能
            self._maybe_create_skill(task, result, tools_used)
            secretary_time = round(t2 - t1, 2)

        t4 = time.time()

        # 记录对话历史
        self.conversation.append({"role": "user", "content": task})
        self.conversation.append({"role": "assistant", "content": result})
        self._save_conversation()

        record = {
            "task": task,
            "context": context,
            "result": result,
            "reflection": reflection,
            "fast_path": fast_path,
            "history_turns": len(self.conversation) // 2,
            "timing": {
                "secretary_s": secretary_time,
                "decision_s": round(t3 - t2, 2),
                "total_s": round(t4 - t0, 2),
            },
            "cost": cost_tracker.total_today(),
        }
        self.task_history.append(record)
        # 记录任务统计
        success = "错误" not in result[:100] and "失败" not in result[:100]
        self.stats_tracker.record(
            task=task, success=success,
            tool_count=len(self.decision.last_tools_used),
            duration=record["timing"]["total_s"],
            input_tokens=getattr(self.decision, 'last_input_tokens', 0),
            output_tokens=getattr(self.decision, 'last_output_tokens', 0),
            skill_used=matched_skill if 'matched_skill' in dir() else "",
            fast_path=fast_path,
        )
        logger.log("system", "任务完成",
                   f"{'[快速]' if fast_path else '[双核]'} "
                   f"耗时 {record['timing']['total_s']}s, "
                   f"今日花费 ¥{record['cost']['total_cost_yuan']}")
        return record

    def run_stream(self, task: str, use_secretary: str = "auto"):
        """流式执行任务, yield SSE 事件"""
        logger.log("system", "收到任务(流)", task[:60])
        history_text = self._recent_history(task)
        t0 = time.time()

        if use_secretary == "off":
            fast_path = True
        elif use_secretary == "on":
            fast_path = False
        else:
            fast_path = is_simple_question(task)

        if fast_path:
            yield {"type": "status", "message": "快速通道: 直接回答中..."}
            context = "(快速通道: 未启用秘书检索)"
            allowed_tools = self.secretary._select_tools(task) if self.secretary.tool_manager else None
            secretary_time = 0
        else:
            yield {"type": "status", "message": "秘书正在检索四库..."}
            t1 = time.time()
            context, allowed_tools = self.secretary.anticipate(task, history_text)
            secretary_time = round(time.time() - t1, 2)
            yield {"type": "secretary_done", "context": context,
                   "time_s": secretary_time}

        # 技能匹配: 注入工作流引导
        skill_ctx, matched_skill = self._get_skill_context_with_name(task)
        if skill_ctx:
            context += "\n\n" + skill_ctx
            yield {"type": "status", "message": f"已加载技能工作流..."}

        # 流式决策
        result = ""
        tool_calls = 0
        t2 = time.time()
        input_tokens = 0
        output_tokens = 0
        for event in self.decision.decide_stream(task, context, history_text,
                                                  allowed_tools=allowed_tools):
            if event["type"] == "token":
                result += event["content"]
            elif event["type"] == "done":
                result = event["result"]
                tool_calls = event["tool_calls"]
                input_tokens = event.get("input_tokens", 0)
                output_tokens = event.get("output_tokens", 0)
            yield event
        decision_time = round(time.time() - t2, 2)

        # 反思 (非流式, 快速通道跳过)
        reflection = "(快速通道: 未启用反思)"
        if not fast_path:
            yield {"type": "status", "message": "秘书正在复盘..."}
            tools_used = self.decision.last_tools_used
            self.secretary.record_result(task, result, tools_used=tools_used)
            reflection = self.secretary.reflect(task, result, context)
            # 复杂任务自动创建技能
            self._maybe_create_skill(task, result, tools_used)

        total_time = round(time.time() - t0, 2)

        # 保存对话历史
        self.conversation.append({"role": "user", "content": task})
        self.conversation.append({"role": "assistant", "content": result})
        self._save_conversation()

        record = {
            "task": task, "context": context, "result": result,
            "reflection": reflection, "fast_path": fast_path,
            "history_turns": len(self.conversation) // 2,
            "timing": {
                "secretary_s": secretary_time,
                "decision_s": decision_time,
                "total_s": total_time,
            },
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
            "cost": cost_tracker.total_today(),
        }
        self.task_history.append(record)
        # 记录任务统计
        success = "错误" not in result[:100] and "失败" not in result[:100]
        self.stats_tracker.record(
            task=task, success=success,
            tool_count=len(self.decision.last_tools_used),
            duration=total_time,
            input_tokens=getattr(self.decision, 'last_input_tokens', 0),
            output_tokens=getattr(self.decision, 'last_output_tokens', 0),
            skill_used=matched_skill if 'matched_skill' in dir() else "",
            fast_path=fast_path,
        )
        logger.log("system", "任务完成(流)",
                   f"{'[快速]' if fast_path else '[双核]'} 耗时 {total_time}s")
        yield {"type": "complete", "record": record}

    def run_linux_command(self, command: str) -> dict:
        if not self.linux or not self.linux.available:
            return {"ok": False, "error": "Linux 环境不可用"}
        logger.log("linux", "执行命令", command[:60])
        return self.linux.exec(command)

    def stats(self) -> dict:
        return {
            "四库统计": self.secretary.libs.stats(),
            "今日成本": cost_tracker.total_today(),
            "Linux模式": self.linux.mode if self.linux else "未启用",
            "已加载工具": self.tool_manager.list_tools(),
            "历史任务数": len(self.task_history),
            "对话轮次": len(self.conversation) // 2,
        }
