"""
技能系统 —— 把经验变成可执行的工作流
技能 = 触发条件 + 执行步骤 + 所需工具
当任务匹配技能触发条件时, 自动加载技能步骤引导 Agent 执行
支持自我改进: 记录使用结果, 用满3次后自动优化步骤
"""

import json
import re
import time
from pathlib import Path

import yaml

from config import DATA_DIR
from utils.db import get_db

SKILLS_DIR = Path(__file__).parent.parent / "skills"
DB_PATH = DATA_DIR / "nexus.db"


def _dedup_list(items):
    """保持原顺序去重 (去除自动生成技能时 required_tools 中的重复工具名)"""
    if not items:
        return []
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


IMPROVE_PROMPT = """你是 Nexus Agent 的技能优化师。

【技能名称】{name}
【技能描述】{description}
【当前步骤】
{current_steps}

【最近 {count} 次使用记录】
{usage_history}

【任务】
根据使用记录优化这个技能的执行步骤。要求:
1. 如果某步经常失败或被跳过, 删除或替换它
2. 如果缺少关键步骤(如验证), 补上
3. 步骤顺序要合理, 工具选择要准确
4. 保持简洁, 不超过8步
5. 只输出优化后的步骤 JSON 数组, 不要解释, 不要其他文字

格式示例:
[{{"tool": "file_read", "description": "读取相关文件"}}, {{"tool": "code_edit", "description": "修改代码"}}]
"""


class Skill:
    """单个技能"""

    def __init__(self, data: dict):
        self.name: str = data.get("name", "unknown")
        self.description: str = data.get("description", "")
        self.trigger_keywords: list[str] = data.get("trigger", {}).get("keywords", [])
        self.trigger_patterns: list[str] = data.get("trigger", {}).get("patterns", [])
        self.steps: list[dict] = data.get("steps", [])
        self.required_tools: list[str] = data.get("required_tools", [])
        self.tags: list[str] = data.get("tags", [])
        self.version: int = data.get("version", 1)
        self.improved_at: str = data.get("improved_at", "")
        self.confidence: float = data.get("confidence", 0.7)
        self.consecutive_failures: int = data.get("consecutive_failures", 0)

    def matches(self, task: str) -> float:
        """计算任务与技能的匹配度 0-1 (基础分 × 置信度)"""
        score = 0.0
        task_lower = task.lower()
        for kw in self.trigger_keywords:
            if kw.lower() in task_lower:
                score += 0.3
        for pattern in self.trigger_patterns:
            try:
                if re.search(pattern, task, re.IGNORECASE):
                    score += 0.5
            except re.error:
                pass
        base = min(score, 1.0)
        return base * self.confidence

    def to_workflow_text(self) -> str:
        """把技能步骤转成工作流引导文本"""
        ver = f" v{self.version}" if self.version > 1 else ""
        lines = [f"【技能: {self.name}{ver}】", self.description, "", "执行步骤:"]
        for i, step in enumerate(self.steps, 1):
            tool = step.get("tool", "")
            desc = step.get("description", "")
            tool_hint = f" (建议工具: {tool})" if tool else ""
            lines.append(f"  {i}. {desc}{tool_hint}")
        if self.required_tools:
            lines.append(f"\n所需工具: {', '.join(self.required_tools)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "trigger": {
                "keywords": self.trigger_keywords,
                "patterns": self.trigger_patterns,
            },
            "steps": self.steps,
            "required_tools": self.required_tools,
            "tags": self.tags,
            "version": self.version,
            "improved_at": self.improved_at,
            "confidence": round(self.confidence, 2),
            "consecutive_failures": self.consecutive_failures,
        }


class SkillManager:
    """技能管理器: 加载、匹配、执行、自我改进"""

    IMPROVE_THRESHOLD = 3  # 使用满3次触发改进

    def __init__(self, llm_client=None, model=None):
        self.skills: dict[str, Skill] = {}
        self.llm_client = llm_client
        self.model = model
        self._init_db()
        self._load_skills()

    def _init_db(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.db = get_db()
        self.conn = self.db.conn  # 共享全局连接
        with self.db.transaction():
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT,
                    task TEXT,
                    success INTEGER,
                    tools_used TEXT,
                    tool_count INTEGER,
                    duration REAL,
                    timestamp TEXT
                )
            """)

    def _load_skills(self):
        if not SKILLS_DIR.exists():
            SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        if not list(SKILLS_DIR.glob("*.yaml")):
            self._write_example_skills()
        for yaml_file in sorted(SKILLS_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if data and "name" in data:
                    skill = Skill(data)
                    self.skills[skill.name] = skill
            except Exception as e:
                print(f"[Skill] 加载失败 {yaml_file.name}: {e}")

    def _write_example_skills(self):
        examples = [
            {
                "name": "debug_python_error",
                "description": "系统化调试 Python 错误: 读取代码→复现→定位→修复→验证",
                "trigger": {
                    "keywords": [
                        "报错",
                        "错误",
                        "error",
                        "traceback",
                        "失败",
                        "debug",
                        "异常",
                        "exception",
                    ],
                    "patterns": [r"(报错|错误|error|exception).{0,20}(python|py|\.py)"],
                },
                "steps": [
                    {
                        "tool": "file_read",
                        "description": "读取报错涉及的源文件, 理解上下文",
                    },
                    {
                        "tool": "code_search",
                        "description": "搜索报错相关的函数和调用点",
                    },
                    {
                        "tool": "code_exec",
                        "description": "复现错误, 获取完整 traceback",
                    },
                    {"tool": "code_edit", "description": "根据错误信息精确定位并修复"},
                    {
                        "tool": "code_exec",
                        "description": "运行验证修复是否成功, 失败则继续分析",
                    },
                ],
                "required_tools": [
                    "file_read",
                    "code_search",
                    "code_exec",
                    "code_edit",
                ],
                "tags": ["debug", "python"],
            },
            {
                "name": "code_refactor",
                "description": "代码重构工作流: 分析→理解→重构→验证",
                "trigger": {
                    "keywords": [
                        "重构",
                        "refactor",
                        "优化代码",
                        "代码优化",
                        "整理代码",
                        "clean code",
                    ],
                    "patterns": [],
                },
                "steps": [
                    {"tool": "project_analyze", "description": "分析项目结构和技术栈"},
                    {"tool": "code_search", "description": "搜索需要重构的代码区域"},
                    {"tool": "file_read", "description": "完整阅读待重构的文件"},
                    {"tool": "code_edit", "description": "分步精确重构, 每次小改动"},
                    {"tool": "code_exec", "description": "运行测试验证重构不破坏功能"},
                ],
                "required_tools": [
                    "project_analyze",
                    "code_search",
                    "file_read",
                    "code_edit",
                    "code_exec",
                ],
                "tags": ["refactor", "quality"],
            },
            {
                "name": "add_feature",
                "description": "新功能开发工作流: 理解项目→设计→实现→测试",
                "trigger": {
                    "keywords": [
                        "添加",
                        "新增",
                        "实现",
                        "加一个",
                        "做一个",
                        "feature",
                        "implement",
                    ],
                    "patterns": [
                        r"(添加|新增|实现|加一个).{0,30}(功能|模块|接口|api|按钮|页面)"
                    ],
                },
                "steps": [
                    {
                        "tool": "project_analyze",
                        "description": "分析项目结构, 找到合适的添加位置",
                    },
                    {
                        "tool": "file_read",
                        "description": "阅读相关现有代码, 理解模式和约定",
                    },
                    {"tool": "code_edit", "description": "按项目约定实现新功能"},
                    {"tool": "code_exec", "description": "编写并运行测试验证新功能"},
                ],
                "required_tools": [
                    "project_analyze",
                    "file_read",
                    "code_edit",
                    "code_exec",
                ],
                "tags": ["feature", "development"],
            },
        ]
        for ex in examples:
            (SKILLS_DIR / f"{ex['name']}.yaml").write_text(
                yaml.dump(ex, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )

    def match_skill(self, task: str, threshold: float = 0.3) -> Skill | None:
        best = None
        best_score = 0
        for skill in self.skills.values():
            score = skill.matches(task)
            if score > best_score:
                best_score = score
                best = skill
        if best and best_score >= threshold:
            return best
        return None

    def list_skills(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
                "steps": len(s.steps),
                "version": s.version,
                "confidence": round(s.confidence, 2),
            }
            for s in self.skills.values()
        ]

    def get_skill_workflow(self, task: str) -> str:
        skill = self.match_skill(task)
        if skill:
            return skill.to_workflow_text()
        return ""

    def create_skill(
        self,
        name: str,
        description: str,
        trigger_keywords: list[str],
        steps: list[dict],
        required_tools: list[str] = None,
        tags: list[str] = None,
    ) -> str:
        data = {
            "name": name,
            "description": description,
            "trigger": {"keywords": trigger_keywords, "patterns": []},
            "steps": steps,
            "required_tools": _dedup_list(required_tools),
            "tags": tags or [],
            "version": 1,
            "improved_at": "",
        }
        skill_file = SKILLS_DIR / f"{name}.yaml"
        skill_file.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        self.skills[name] = Skill(data)
        return f"技能已创建: {name} ({len(steps)} 步)"

    def record_usage(
        self,
        skill_name: str,
        task: str,
        success: bool,
        tools_used: list[str],
        duration: float,
    ):
        """记录技能使用结果 + 更新置信度 + 连续失败计数"""
        self.db.execute(
            "INSERT INTO skill_usage (skill_name, task, success, tools_used, tool_count, duration, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                skill_name,
                task[:200],
                1 if success else 0,
                json.dumps(tools_used, ensure_ascii=False),
                len(tools_used),
                duration,
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        # 更新置信度
        if skill_name in self.skills:
            skill = self.skills[skill_name]
            if success:
                skill.confidence = min(1.0, skill.confidence + 0.1)
                skill.consecutive_failures = 0
            else:
                skill.confidence = max(0.1, skill.confidence - 0.15)
                skill.consecutive_failures += 1
            # 连续失败2次自动回滚到上一版本
            if skill.consecutive_failures >= 2 and skill.version > 1:
                self._rollback_skill(skill_name)
            else:
                self._save_skill(skill)

    def get_usage_history(self, skill_name: str, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT task, success, tools_used, tool_count, duration, timestamp FROM skill_usage WHERE skill_name = ? ORDER BY id DESC LIMIT ?",
            (skill_name, limit),
        ).fetchall()
        return [
            {
                "task": r[0],
                "success": bool(r[1]),
                "tools_used": json.loads(r[2]) if r[2] else [],
                "tool_count": r[3],
                "duration": r[4],
                "timestamp": r[5],
            }
            for r in rows
        ]

    def should_improve(self, skill_name: str) -> bool:
        """是否达到改进阈值"""
        count = self.conn.execute(
            "SELECT COUNT(*) FROM skill_usage WHERE skill_name = ?", (skill_name,)
        ).fetchone()[0]
        return count >= self.IMPROVE_THRESHOLD

    def improve_skill(self, skill_name: str) -> dict:
        """用 LLM 优化技能步骤"""
        if skill_name not in self.skills:
            return {"ok": False, "error": f"技能不存在: {skill_name}"}
        if not self.llm_client or not self.model:
            return {"ok": False, "error": "未配置 LLM 客户端"}

        skill = self.skills[skill_name]
        history = self.get_usage_history(skill_name, limit=10)
        if not history:
            return {"ok": False, "error": "无使用记录"}

        # 构造使用历史文本
        history_lines = []
        for i, h in enumerate(history, 1):
            status = "成功" if h["success"] else "失败"
            tools = ", ".join(h["tools_used"]) if h["tools_used"] else "无"
            history_lines.append(
                f"第{i}次 [{status}] 工具({h['tool_count']}个): {tools} | 耗时{h['duration']:.1f}s | 任务: {h['task'][:60]}"
            )
        history_text = "\n".join(history_lines)

        current_steps = "\n".join(
            f"{i + 1}. [{s.get('tool', '?')}] {s.get('description', '')}"
            for i, s in enumerate(skill.steps)
        )

        try:
            resp = self.llm_client.chat.completions.create(
                model=self.model,
                temperature=0.3,
                messages=[
                    {
                        "role": "user",
                        "content": IMPROVE_PROMPT.format(
                            name=skill.name,
                            description=skill.description,
                            current_steps=current_steps,
                            count=len(history),
                            usage_history=history_text,
                        ),
                    }
                ],
            )
            content = resp.choices[0].message.content.strip()
            # 提取 JSON
            json_start = content.find("[")
            json_end = content.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                new_steps = json.loads(content[json_start:json_end])
            else:
                return {"ok": False, "error": "LLM 未返回有效 JSON"}

            if not isinstance(new_steps, list) or len(new_steps) == 0:
                return {"ok": False, "error": "优化后的步骤为空"}

            # 备份旧版本
            old_step_count = len(skill.steps)
            backup_file = SKILLS_DIR / f"{skill_name}_v{skill.version}.yaml.bak"
            backup_file.write_text(
                yaml.dump(
                    skill.to_dict(), allow_unicode=True, default_flow_style=False
                ),
                encoding="utf-8",
            )
            # 只保留最近3个备份
            backups = sorted(SKILLS_DIR.glob(f"{skill_name}_v*.yaml.bak"))
            for old_bak in backups[:-3]:
                old_bak.unlink(missing_ok=True)

            # 更新技能
            skill.steps = new_steps
            skill.version += 1
            skill.improved_at = time.strftime("%Y-%m-%d %H:%M:%S")
            skill.consecutive_failures = 0
            # 新版本置信度略降, 需要重新验证
            skill.confidence = max(0.5, skill.confidence - 0.1)

            self._save_skill(skill)

            # 清空该技能的使用记录(改进后重新积累)
            self.db.execute(
                "DELETE FROM skill_usage WHERE skill_name = ?", (skill_name,)
            )

            return {
                "ok": True,
                "skill": skill_name,
                "old_steps": old_step_count,
                "new_steps": len(new_steps),
                "version": skill.version,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _save_skill(self, skill):
        """保存技能到 yaml 文件"""
        skill_file = SKILLS_DIR / f"{skill.name}.yaml"
        skill_file.write_text(
            yaml.dump(skill.to_dict(), allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    def _rollback_skill(self, skill_name: str) -> bool:
        """回滚到上一版本"""
        skill = self.skills.get(skill_name)
        if not skill or skill.version <= 1:
            return False
        prev_version = skill.version - 1
        backup_file = SKILLS_DIR / f"{skill_name}_v{prev_version}.yaml.bak"
        if not backup_file.exists():
            return False
        try:
            old_data = yaml.safe_load(backup_file.read_text(encoding="utf-8"))
            skill.steps = old_data.get("steps", skill.steps)
            skill.version = prev_version
            skill.confidence = max(0.3, old_data.get("confidence", 0.5))
            skill.consecutive_failures = 0
            skill.improved_at = time.strftime("%Y-%m-%d %H:%M:%S") + " (rollback)"
            self._save_skill(skill)
            print(f"[Skill] {skill_name} auto-rollback to v{prev_version}")
            return True
        except Exception as e:
            print(f"[Skill] {skill_name} rollback failed: {e}")
            return False

    def reload(self) -> str:
        self.skills.clear()
        self._load_skills()
        return f"技能已重载, 当前 {len(self.skills)} 个技能"

    # ========== 技能生命周期管理 ==========

    def get_skill_stats(self, skill_name: str) -> dict:
        """获取技能统计: 使用次数、成功率、最后使用时间"""
        rows = self.conn.execute(
            "SELECT COUNT(*), SUM(success), MAX(timestamp) FROM skill_usage WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        total = rows[0] or 0
        success = rows[1] or 0
        last_used = rows[2] or ""
        rate = (success / total * 100) if total > 0 else 0
        return {
            "total": total,
            "success": success,
            "success_rate": round(rate, 1),
            "last_used": last_used,
        }

    def get_all_stats(self) -> list[dict]:
        """获取所有技能的统计, 按使用次数排序"""
        result = []
        for name, skill in self.skills.items():
            stats = self.get_skill_stats(name)
            result.append(
                {
                    "name": name,
                    "description": skill.description,
                    "version": skill.version,
                    "confidence": round(skill.confidence, 2),
                    **stats,
                }
            )
        result.sort(key=lambda x: x["total"], reverse=True)
        return result

    def cleanup_low_quality(
        self, min_success_rate: float = 40.0, min_usage: int = 5, archive_days: int = 90
    ) -> dict:
        """
        自动淘汰低质量技能
        - 成功率 < min_success_rate 且使用 >= min_usage -> 删除
        - archive_days 天未使用 -> 归档(重命名为 .archived)
        返回淘汰统计
        """
        import time as _time

        deleted = []
        archived = []
        now = _time.time()

        for name in list(self.skills.keys()):
            stats = self.get_skill_stats(name)
            skill = self.skills[name]

            # 低成功率且使用次数足够 -> 删除
            if stats["total"] >= min_usage and stats["success_rate"] < min_success_rate:
                self._delete_skill(name)
                deleted.append(
                    {
                        "name": name,
                        "reason": f"成功率{stats['success_rate']}%",
                        "usage": stats["total"],
                    }
                )
                continue

            # 长期未使用 -> 归档
            if stats["last_used"]:
                try:
                    last_ts = _time.mktime(
                        _time.strptime(stats["last_used"], "%Y-%m-%d %H:%M:%S")
                    )
                    days_unused = (now - last_ts) / 86400
                    if days_unused > archive_days and stats["total"] > 0:
                        self._archive_skill(name)
                        archived.append(
                            {"name": name, "reason": f"{int(days_unused)}天未使用"}
                        )
                except Exception:
                    pass

        return {
            "deleted": deleted,
            "archived": archived,
            "total_deleted": len(deleted),
            "total_archived": len(archived),
        }

    def _delete_skill(self, skill_name: str):
        """删除技能文件和内存引用"""
        skill_file = SKILLS_DIR / f"{skill_name}.yaml"
        if skill_file.exists():
            skill_file.unlink()
        # 同时删除备份
        for bak in SKILLS_DIR.glob(f"{skill_name}_v*.yaml.bak"):
            bak.unlink()
        self.skills.pop(skill_name, None)
        # 删除使用记录
        self.db.execute("DELETE FROM skill_usage WHERE skill_name = ?", (skill_name,))

    def _archive_skill(self, skill_name: str):
        """归档技能(重命名)"""
        skill_file = SKILLS_DIR / f"{skill_name}.yaml"
        if skill_file.exists():
            archived_file = SKILLS_DIR / f"{skill_name}.archived"
            skill_file.rename(archived_file)
        self.skills.pop(skill_name, None)

    def find_similar_skill(
        self, description: str, threshold: float = 0.7
    ) -> str | None:
        """
        查找相似技能(基于描述的关键词重叠)
        返回最相似的技能名, 无相似则返回 None
        """
        if not description:
            return None
        desc_words = set(description.lower().split())
        best_name = None
        best_score = 0
        for name, skill in self.skills.items():
            skill_words = set(skill.description.lower().split())
            if not desc_words or not skill_words:
                continue
            overlap = len(desc_words & skill_words)
            union = len(desc_words | skill_words)
            score = overlap / union if union > 0 else 0
            if score > best_score:
                best_score = score
                best_name = name
        if best_score >= threshold:
            return best_name
        return None

    def match_skills(
        self, task: str, top_k: int = 3, threshold: float = 0.3
    ) -> list[Skill]:
        """
        匹配多个技能, 按匹配度排序, 最多返回 top_k 个
        """
        scored = []
        for skill in self.skills.values():
            score = skill.matches(task)
            if score >= threshold:
                scored.append((score, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_k]]

    def get_skill_workflows(self, task: str, top_k: int = 3) -> str:
        """获取多个匹配技能的工作流文本"""
        skills = self.match_skills(task, top_k=top_k)
        if not skills:
            return ""
        parts = []
        for skill in skills:
            parts.append(skill.to_workflow_text())
        return "\n\n".join(parts)

    def should_generate_skill(
        self,
        task: str,
        tool_calls: list,
        result: str,
        min_tool_calls: int = 3,
        min_similar_tasks: int = 2,
    ) -> bool:
        """
        判断是否应该从经验生成技能
        条件:
        1. 任务成功(结果非空)
        2. 工具调用 >= min_tool_calls 次
        3. 同类任务历史出现 >= min_similar_tasks 次
        4. 没有相似的现有技能
        """
        # 1. 任务必须成功
        if not result or not result.strip():
            return False
        # 2. 工具调用次数足够
        if len(tool_calls) < min_tool_calls:
            return False
        # 3. 检查是否有相似技能(避免重复)
        similar = self.find_similar_skill(task)
        if similar:
            return False
        # 4. 同类任务历史出现次数(从经验库或任务统计中查)
        # 简化: 用关键词匹配 task_stats 表
        try:
            task_words = set(task.lower().split()[:5])
            rows = self.conn.execute(
                "SELECT task FROM task_stats ORDER BY id DESC LIMIT 50"
            ).fetchall()
            similar_count = 0
            for row in rows:
                row_words = set((row[0] or "").lower().split()[:5])
                overlap = len(task_words & row_words)
                if overlap >= 2:
                    similar_count += 1
            if similar_count < min_similar_tasks:
                return False
        except Exception:
            pass
        return True

    def auto_generate_skill(
        self, task: str, tool_calls: list, result: str
    ) -> str | None:
        """
        从任务经验自动生成技能
        成功返回技能名, 不满足条件返回 None
        """
        if not self.should_generate_skill(task, tool_calls, result):
            return None

        # 从工具调用序列提取步骤
        steps = []
        seen_tools = set()
        for tc in tool_calls:
            tool_name = tc.get("tool", "") if isinstance(tc, dict) else str(tc)
            if tool_name and tool_name not in seen_tools:
                seen_tools.add(tool_name)
                steps.append(
                    {
                        "tool": tool_name,
                        "description": f"使用 {tool_name} 完成相关操作",
                    }
                )

        if len(steps) < 2:
            return None

        # 生成技能名(从任务关键词提取)
        import re

        name_words = re.findall(r"[a-zA-Z]+|[一-龥]+", task.lower())
        skill_name = "_".join(name_words[:4]) if name_words else "auto_skill"
        skill_name = skill_name[:50]

        # 检查是否已存在
        if skill_name in self.skills:
            return None

        try:
            self.create_skill(
                name=skill_name,
                description=f"自动生成: {task[:80]}",
                trigger_keywords=name_words[:5],
                steps=steps,
                required_tools=list(seen_tools),
                tags=["auto-generated"],
            )
            return skill_name
        except Exception:
            return None
