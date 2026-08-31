"""
临时文件清理工具
================
让 Nexus(决策核心) 可以主动清理自己在 temp/ 任务目录中生成的
测试 / 验证 / 演示文件, 对应系统提示词中的临时文件规则。
"""
from tools.base_tool import BaseTool
from utils import temp_workspace


class CleanupTempTool(BaseTool):
    """清理当前任务的临时工作目录"""

    name = "cleanup_temp"
    description = (
        "清理本次任务在临时工作目录(temp/)中生成的测试、验证、演示文件。"
        "每次跑完测试或验证后应主动调用, 保持项目目录干净。"
        "不传 relative_path 时清空整个任务临时目录; "
        "传入 relative_path 时只删除指定的子文件或子目录。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "relative_path": {
                "type": "string",
                "description": "可选: 临时目录内的相对路径(文件或子目录), 留空则清理整个临时目录",
                "default": "",
            },
        },
        "required": [],
    }

    def execute(self, relative_path: str = "", **kwargs) -> str:
        try:
            stats = temp_workspace.cleanup_temp(relative_path or "")
        except ValueError as exc:
            return f"错误: {exc}"
        except Exception as exc:  # noqa: BLE001 - 工具层兜底, 结果回灌模型
            return f"清理失败: {type(exc).__name__}: {exc}"

        if not stats.get("ok"):
            return f"错误: {stats.get('note', '清理未执行')}"
        if stats.get("note"):
            return stats["note"]
        return (
            f"临时目录已清理: 删除文件 {stats['removed_files']} 个, "
            f"文件夹 {stats['removed_dirs']} 个, "
            f"释放 {stats['freed_bytes']} 字节"
        )
