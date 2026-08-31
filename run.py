"""
Nexus 双核 Agent —— 入口程序
用法:
  python run.py              # 启动 Web UI (默认)
  python run.py --cli        # 命令行交互模式
  python run.py --demo       # 运行演示任务
"""
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WEB_CONFIG


def run_web():
    """启动 Web UI"""
    from ui.web_ui import main
    main()


def run_cli():
    """命令行交互模式"""
    from core.dual_agent import DualCoreAgent
    agent = DualCoreAgent(use_linux=True)
    agent.secretary.seed_demo_data()

    print("\n" + "=" * 55)
    print("  Nexus 双核 Agent —— 命令行模式")
    print("  输入任务开始, 输入 quit 退出, linux <命令> 操作嵌入系统")
    print("=" * 55 + "\n")

    while True:
        try:
            task = input("\n[你] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break
        if not task:
            continue
        if task.lower() in ("quit", "exit", "q"):
            print("再见!")
            break
        if task.lower().startswith("linux "):
            cmd = task[6:]
            result = agent.run_linux_command(cmd)
            print(f"\n[Linux] {result.get('stdout', '')}")
            if result.get('stderr'):
                print(f"[Linux 错误] {result['stderr']}")
            continue
        if task.lower() == "stats":
            import json
            print(json.dumps(agent.stats(), ensure_ascii=False, indent=2))
            continue

        record = agent.run(task)
        print(f"\n[Nexus 决策]\n{record['result']}")
        print(f"\n[耗时] 秘书 {record['timing']['secretary_s']}s + "
              f"决策 {record['timing']['decision_s']}s = "
              f"{record['timing']['total_s']}s")
        print(f"[今日花费] ¥{record['cost']['total_cost_yuan']}")


def run_demo():
    """运行演示任务"""
    from core.dual_agent import DualCoreAgent
    agent = DualCoreAgent(use_linux=True)
    agent.secretary.seed_demo_data()

    tasks = [
        "帮我分析一下用向量检索做知识库匹配的技术方案",
        "基于刚才的分析, 给我一个最简实现思路",
    ]
    for task in tasks:
        record = agent.run(task)
        print(f"\n{'='*50}")
        print(f"任务: {task}")
        print(f"{'='*50}")
        print(f"\n秘书递达的上下文:\n{record['context']}")
        print(f"\nNexus 决策:\n{record['result']}")
        print(f"\n耗时: {record['timing']['total_s']}s, "
              f"今日花费: ¥{record['cost']['total_cost_yuan']}")


if __name__ == "__main__":
    if "--cli" in sys.argv:
        run_cli()
    elif "--demo" in sys.argv:
        run_demo()
    else:
        run_web()
