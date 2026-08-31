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
from utils import temp_workspace
from config import DATA_DIR, MODEL_ROUTING

HISTORY_FILE = DATA_DIR / "conversation_history.json"
MAX_HISTORY_TURNS = 6

# 强复杂信号: 只有命中这些词(或任务超长)才走完整双核流程(秘书四库检索+反思)。
# 设计原则: 默认走快速通道 —— 决策核心本身就带全部工具, 写代码/查资料/解释类
# 任务不需要额外消耗一次秘书 LLM 调用; 只有"需要多源整合/历史经验/工程复杂度高"
# 的任务才值得秘书先检索整理。
COMPLEX_KEYWORDS = [
    # 分析研究 / 多源整合
    "分析", "对比", "比较", "调研", "评估", "方案", "选型", "可行性",
    # 工程复杂度高
    "重构", "架构", "部署", "排查", "调试", "修复", "定位问题",
    "性能优化", "压测", "全链路", "搭建一套", "设计并实现",
    # 明确需要四库 / 历史经验
    "知识库", "经验库", "四库", "历史经验", "之前的记录", "复盘",
]
# 超过该长度的任务默认视为复杂任务 (长描述通常包含多步诉求)
COMPLEX_TASK_MIN_LEN = 40


def is_simple_question(task: str) -> bool:
    """判断是否走快速通道: 无强复杂信号且任务不长 => True"""
    if len(task) > COMPLEX_TASK_MIN_LEN:
        return False
    task_lower = task.lower()
    return not any(kw in task_lower for kw in COMPLEX_KEYWORDS)


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
        from tools.project_profile import ProjectProfileManager
        self.project_profile = ProjectProfileManager()
        self.tool_manager = ToolManager(linux_embed=self.linux, code_index=self.code_index,
                                        profile_manager=self.project_profile)
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
        # 清理上次异常退出残留的临时任务目录
        stale = temp_workspace.cleanup_stale()
        if stale:
            logger.log("system", "临时目录清理", f"清除 {stale} 个残留任务目录")
        # 启动知识库后台自动更新 (每6小时检查一次)
        self.knowledge_updater.start_auto_update(interval_hours=6.0)
        logger.log("system", "工具已加载",
                   f"{len(self.tool_manager.list_tools())} 个工具: "
                   + ", ".join(t["name"] for t in self.tool_manager.list_tools()))
        self.task_history: list[dict] = []
        # 会话级模式 (必须在 _load_conversation 之前初始化)
        self.current_mode = "work"
        # 按模式分开存储对话历史: work(编码) / chat(聊天) / brainstorm(头脑风暴)
        self.conversations: dict[str, list[dict]] = {"work": [], "chat": [], "brainstorm": []}
        self.conversation: list[dict] = []  # 当前模式的对话引用
        self._load_conversation()
        self._chat_tools = ["web_search", "current_time"]
        self._brainstorm_tools = ["web_search", "current_time"]  # 头脑风暴也用轻量工具

    def set_mode(self, mode: str) -> str:
        """切换会话模式并立即应用到决策核心, 同时切换对话历史, 返回实际生效的模式"""
        new_mode = mode if mode in ("work", "chat", "brainstorm") else "work"
        if new_mode != self.current_mode:
            # 保存当前模式的对话
            self.conversations[self.current_mode] = self.conversation
            self.current_mode = new_mode
            # 加载新模式的对话
            self.conversation = self.conversations.get(new_mode, [])
            self._apply_mode(new_mode)
            logger.log("system", "模式切换", f"当前模式: {new_mode}, 历史消息: {len(self.conversation)} 条")
        return self.current_mode

    def get_mode(self) -> str:
        return self.current_mode

    def _apply_mode(self, mode: str | None) -> str:
        """任务前应用模式: 切换决策核心提示词与模型, 返回实际模式"""
        actual = mode if mode in ("work", "chat", "brainstorm") else self.current_mode
        if actual == "chat":
            self.decision.set_mode("chat")
            self.decision.override_model = MODEL_ROUTING["chat"]
        elif actual == "brainstorm":
            self.decision.set_mode("brainstorm")
            self.decision.override_model = MODEL_ROUTING.get("brainstorm", MODEL_ROUTING["chat"])
        else:
            self.decision.set_mode("work")
            self.decision.override_model = None  # 恢复默认决策模型
        return actual

    def _load_conversation(self):
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                # 兼容旧格式: 纯列表 → 当作 work 模式
                if isinstance(data, list):
                    self.conversations = {"work": data, "chat": [], "brainstorm": []}
                elif isinstance(data, dict):
                    self.conversations = {
                        "work": data.get("work", []),
                        "chat": data.get("chat", []),
                        "brainstorm": data.get("brainstorm", []),
                    }
            except Exception:
                self.conversations = {"work": [], "chat": [], "brainstorm": []}
        self.conversation = self.conversations.get(self.current_mode, [])

    def _save_conversation(self, skip_index: bool = False,
                           mode: str | None = None, conv: list | None = None):
        # 确保指定模式的对话已同步回 conversations (默认当前模式)
        mode = mode or self.current_mode
        conv = conv if conv is not None else self.conversation
        self.conversations[mode] = conv
        HISTORY_FILE.write_text(
            json.dumps(self.conversations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 增量索引到向量库 (只索引最后两条; 轻量模式跳过, 避免污染长期记忆)
        if not skip_index:
            self._index_recent_history(conv)

    def _index_recent_history(self, conv: list | None = None):
        """把最近的对话增量索引到向量库, 用于长期记忆检索"""
        if conv is None:
            conv = self.conversation
        try:
            vs = self.secretary.libs.vector_store
            for i, msg in enumerate(conv[-2:]):
                vid = f"conv_{len(conv) - 2 + i}_{msg['role']}"
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
                                   tools_used: list, duration: float,
                                   success: bool):
        """记录技能使用结果, 达到阈值后自动改进 (success 以工具执行情况为准)"""
        try:
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
        self.conversations[self.current_mode] = []
        self._save_conversation()
        logger.log("system", "对话历史已清空", "")

    def add_knowledge_url(self, url: str) -> dict:
        return self.knowledge_updater.add_url_to_knowledge(url)

    def run(self, task: str, use_secretary: str = "auto", mode: str | None = None) -> dict:
        """
        执行任务
        use_secretary: "auto"(自动判断) / "on"(强制秘书) / "off"(强制快速通道)
        mode: None=用会话当前模式, "work"/"chat"=单次覆盖
        """
        logger.log("system", "收到任务", task[:60])
        temp_workspace.begin_task()  # 本次任务的临时工作目录
        history_text = self._recent_history(task)
        t0 = time.time()
        try:
            return self._run_inner(task, use_secretary, history_text, t0, mode)
        finally:
            temp_workspace.end_task()  # 任务结束(含异常)自动清理临时文件

    def _run_inner(self, task: str, use_secretary: str,
                   history_text: str, t0: float, mode: str | None = None) -> dict:
        actual_mode = self._apply_mode(mode)
        self.current_mode = actual_mode
        self.conversation = self.conversations.get(actual_mode, [])
        conv = self.conversation  # 本地引用: 任务执行期间即使切换模式也不会串台
        is_light = actual_mode in ("chat", "brainstorm")

        # 聊天/头脑风暴模式: 强制快速通道, 不调秘书, 用精简提示词+白名单工具+轻量模型
        if is_light:
            # chat/brainstorm: 默认快速通道, 但手动强制开秘书(on)时走完整双核
            fast_path = use_secretary != "on"
        elif use_secretary == "off":
            fast_path = True
        elif use_secretary == "on":
            fast_path = False
        else:  # auto
            fast_path = is_simple_question(task)

        # 临时目录规则(注入给决策核心, 约束测试文件落点) —— 聊天模式不需要
        temp_hint = "" if is_light else temp_workspace.context_hint()
        # 项目档案 (work 模式自动召回当前目录所属项目的技术画像)
        profile_hint = ""
        if not is_light:
            try:
                _prof = self.project_profile.get_for_directory(os.getcwd())
                if _prof:
                    profile_hint = self.project_profile.format_for_context(_prof)
            except Exception:
                pass
        matched_skill = None

        if fast_path:
            # 快速通道: 跳过秘书检索和反思, 直接回答
            logger.log("system", "快速通道",
                       f"{'轻量模式' if is_light else '简单问题'}, 跳过秘书检索")
            t1 = time.time()
            context = "(轻量模式: 精简上下文, 仅可查实时信息)" if is_light else "(快速通道: 未启用秘书检索)"
            if not is_light:
                # 技能匹配 (快速通道也匹配, 编码场景需要工作流引导)
                skill_ctx, matched_skill = self._get_skill_context_with_name(task)
                if skill_ctx:
                    context += "\n\n" + skill_ctx
            if temp_hint:
                context += "\n\n" + temp_hint
            if profile_hint:
                context += "\n\n" + profile_hint
            # 聊天模式固定白名单工具; 普通快速通道做关键词筛选
            if is_light:
                allowed_tools = list(self._chat_tools)  # chat 和 brainstorm 都用轻量工具
            else:
                allowed_tools = self.secretary._select_tools(task) if self.secretary.tool_manager else None
            t2 = time.time()
            result = self.decision.decide(task, context, history_text,
                                          allowed_tools=allowed_tools)
            t3 = time.time()
            reflection = "(轻量模式: 未启用反思与沉淀)" if is_light else "(快速通道: 未启用反思)"
            secretary_time = 0
        else:
            # 完整双核流程
            t1 = time.time()
            context, allowed_tools = self.secretary.anticipate(task, history_text)
            # 技能匹配: 注入工作流引导
            skill_ctx, matched_skill = self._get_skill_context_with_name(task)
            if skill_ctx:
                context += "\n\n" + skill_ctx
            context += "\n\n" + temp_hint
            if profile_hint:
                context += "\n\n" + profile_hint
            t2 = time.time()
            result = self.decision.decide(task, context, history_text,
                                          allowed_tools=allowed_tools)
            t3 = time.time()
            tools_used = self.decision.last_tools_used
            self.secretary.record_result(task, result, tools_used=tools_used, mode=actual_mode)
            reflection = self.secretary.reflect(task, result, context, mode=actual_mode)
            # 技能使用记录 + 自我改进
            if matched_skill:
                self._record_and_improve_skill(
                    matched_skill, task, result, tools_used, t3 - t2,
                    success=not self.decision.last_had_tool_error)
            # 复杂任务自动创建技能
            self._maybe_create_skill(task, result, tools_used)
            secretary_time = round(t2 - t1, 2)

        t4 = time.time()

        # 记录对话历史 (聊天模式不索引长期向量, 避免闲聊污染经验检索)
        conv.append({"role": "user", "content": task})
        conv.append({"role": "assistant", "content": result})
        self._save_conversation(skip_index=is_light, mode=actual_mode, conv=conv)

        record = {
            "task": task,
            "context": context,
            "result": result,
            "reflection": reflection,
            "fast_path": fast_path,
            "mode": actual_mode,
            "history_turns": len(conv) // 2,
            "timing": {
                "secretary_s": secretary_time,
                "decision_s": round(t3 - t2, 2),
                "total_s": round(t4 - t0, 2),
            },
            "cost": cost_tracker.total_today(),
        }
        self.task_history.append(record)
        # 记录任务统计 (聊天模式不沉淀, 避免闲聊稀释编码任务的成功率)
        if not is_light:
            # 成功判定以工具真实执行情况为准, 不再扫描回答文本(会误判"解释错误处理"等正常回答)
            success = not self.decision.last_had_tool_error
            self.stats_tracker.record(
                task=task, success=success,
                tool_count=len(self.decision.last_tools_used),
                duration=record["timing"]["total_s"],
                input_tokens=getattr(self.decision, 'last_input_tokens', 0),
                output_tokens=getattr(self.decision, 'last_output_tokens', 0),
                skill_used=matched_skill or "",
                fast_path=fast_path,
            )
        logger.log("system", "任务完成",
                   f"[{'轻量' if is_light else ('快速' if fast_path else '双核')}] "
                   f"耗时 {record['timing']['total_s']}s, "
                   f"今日花费 ¥{record['cost']['total_cost_yuan']}")
        return record

    def run_stream(self, task: str, use_secretary: str = "auto", mode: str | None = None):
        """流式执行任务, yield SSE 事件"""
        logger.log("system", "收到任务(流)", task[:60])
        temp_workspace.begin_task()  # 本次任务的临时工作目录
        history_text = self._recent_history(task)
        t0 = time.time()
        try:
            yield from self._run_stream_inner(task, use_secretary, history_text, t0, mode)
        finally:
            # 生成器关闭(含客户端断开)时也会执行, 保证临时文件被清理
            temp_workspace.end_task()

    def _run_stream_inner(self, task: str, use_secretary: str,
                          history_text: str, t0: float, mode: str | None = None):
        actual_mode = self._apply_mode(mode)
        self.current_mode = actual_mode
        self.conversation = self.conversations.get(actual_mode, [])
        conv = self.conversation  # 本地引用: 任务执行期间即使切换模式也不会串台
        is_light = actual_mode in ("chat", "brainstorm")

        if is_light:
            # chat/brainstorm: 默认快速通道, 但手动强制开秘书(on)时走完整双核
            fast_path = use_secretary != "on"
        elif use_secretary == "off":
            fast_path = True
        elif use_secretary == "on":
            fast_path = False
        else:
            fast_path = is_simple_question(task)

        if fast_path:
            yield {"type": "status", "message": "轻量模式: 直接回答中..." if is_light else "快速通道: 直接回答中..."}
            context = "(轻量模式: 精简上下文, 仅可查实时信息)" if is_light else "(快速通道: 未启用秘书检索)"
            allowed_tools = list(self._chat_tools) if is_light else (
                self.secretary._select_tools(task) if self.secretary.tool_manager else None)
            secretary_time = 0
            matched_skill = None
        else:
            yield {"type": "status", "message": "秘书正在检索四库..."}
            t1 = time.time()
            context, allowed_tools = self.secretary.anticipate(task, history_text)
            secretary_time = round(time.time() - t1, 2)
            yield {"type": "secretary_done", "context": context,
                   "time_s": secretary_time}
            # 技能匹配: 注入工作流引导 (仅工作模式)
            skill_ctx, matched_skill = self._get_skill_context_with_name(task)
            if skill_ctx:
                context += "\n\n" + skill_ctx
                yield {"type": "status", "message": f"已加载技能工作流..."}

        # 临时目录规则(注入给决策核心, 约束测试文件落点) —— 聊天模式不需要
        if not is_light:
            context += "\n\n" + temp_workspace.context_hint()
            # 项目档案自动召回
            try:
                _prof = self.project_profile.get_for_directory(os.getcwd())
                if _prof:
                    context += "\n\n" + self.project_profile.format_for_context(_prof)
            except Exception:
                pass

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

        # 反思 (非流式, 快速通道/聊天模式跳过)
        reflection = "(轻量模式: 未启用反思与沉淀)" if is_light else "(快速通道: 未启用反思)"
        if not fast_path:
            yield {"type": "status", "message": "秘书正在复盘..."}
            tools_used = self.decision.last_tools_used
            self.secretary.record_result(task, result, tools_used=tools_used, mode=actual_mode)
            reflection = self.secretary.reflect(task, result, context, mode=actual_mode)
            # 技能使用记录 + 自我改进 (与非流式路径对齐)
            if matched_skill:
                self._record_and_improve_skill(
                    matched_skill, task, result, tools_used, decision_time,
                    success=not self.decision.last_had_tool_error)
            # 复杂任务自动创建技能
            self._maybe_create_skill(task, result, tools_used)

        total_time = round(time.time() - t0, 2)

        # 保存对话历史 (轻量模式不索引长期向量)
        conv.append({"role": "user", "content": task})
        conv.append({"role": "assistant", "content": result})
        self._save_conversation(skip_index=is_light, mode=actual_mode, conv=conv)

        record = {
            "task": task, "context": context, "result": result,
            "reflection": reflection, "fast_path": fast_path, "mode": actual_mode,
            "history_turns": len(conv) // 2,
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
        # 记录任务统计 (聊天模式不沉淀)
        if not is_light:
            # 成功判定以工具真实执行情况为准, 不再扫描回答文本(会误判"解释错误处理"等正常回答)
            success = not self.decision.last_had_tool_error
            self.stats_tracker.record(
                task=task, success=success,
                tool_count=len(self.decision.last_tools_used),
                duration=total_time,
                input_tokens=getattr(self.decision, 'last_input_tokens', 0),
                output_tokens=getattr(self.decision, 'last_output_tokens', 0),
                skill_used=matched_skill or "",
                fast_path=fast_path,
            )
        logger.log("system", "任务完成(流)",
                   f"[{'轻量' if is_light else ('快速' if fast_path else '双核')}] 耗时 {total_time}s")
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
            "当前模式": self.current_mode,
            "模型路由": MODEL_ROUTING,
        }
