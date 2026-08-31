"""
Web UI —— 对应视频中的可视化界面
功能: 对话交互 / 日志可视化 / 成本监控 / Linux 终端 / 四库浏览
      知识库导入 / 远程嵌入 API
"""
import json
import queue
import threading
from flask import Flask, render_template, request, jsonify, Response
from config import WEB_CONFIG
from core.dual_agent import DualCoreAgent
from utils.logger import logger
from utils.cost_tracker import cost_tracker


def _add_cors(resp):
    """添加 CORS 头, 支持远程嵌入"""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    agent = DualCoreAgent(use_linux=True)

    # CORS 预检
    @app.before_request
    def handle_options():
        if request.method == "OPTIONS":
            return _add_cors(jsonify({"ok": True}))

    @app.after_request
    def after_request(resp):
        resp = _add_cors(resp)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.route("/")
    def index():
        return render_template("index.html")

    # ============ 对话 ============
    @app.route("/api/chat", methods=["POST"])
    def chat():
        data = request.get_json()
        task = data.get("message", "").strip()
        use_secretary = data.get("use_secretary", "auto")
        mode = data.get("mode")  # None=用会话当前模式
        if not task:
            return jsonify({"error": "消息不能为空"}), 400
        if not agent.secretary.configured or not agent.decision.configured:
            return jsonify({"error": "未配置 API Key"}), 400
        record = agent.run(task, use_secretary=use_secretary, mode=mode)
        return jsonify(record)

    def _safe_generate(gen):
        """包装 SSE 生成器, 确保任何异常都不会导致 Flask 进程崩溃"""
        try:
            yield from gen
        except GeneratorExit:
            raise  # 客户端断开, 正常退出
        except Exception as e:
            import traceback
            import json as _json
            traceback.print_exc()
            err = _json.dumps({"type": "error", "message": "Server error: " + str(e)}, ensure_ascii=False)
            yield "data: " + err + "\n\n"

    @app.route("/api/chat/stream", methods=["POST"])
    def chat_stream():
        """SSE 流式对话"""
        data = request.get_json()
        task = data.get("message", "").strip()
        use_secretary = data.get("use_secretary", "auto")
        mode = data.get("mode")
        if not task:
            return jsonify({"error": "消息不能为空"}), 400
        if not agent.secretary.configured or not agent.decision.configured:
            return jsonify({"error": "未配置 API Key"}), 400

        # 断线重连: 浏览器自动带 Last-Event-ID, 跳过已发送的事件
        last_id = request.headers.get("Last-Event-ID", "")
        skip_count = int(last_id) if last_id.isdigit() else 0

        def generate():
            q = queue.Queue()
            stop_event = threading.Event()
            event_id = 0

            def agent_worker():
                try:
                    for event in agent.run_stream(task, use_secretary=use_secretary, mode=mode):
                        q.put(("data", event))
                    q.put(("done", None))
                except Exception as e:
                    q.put(("error", str(e)))

            def heartbeat_worker():
                while not stop_event.is_set():
                    stop_event.wait(10)
                    if not stop_event.is_set():
                        q.put(("heartbeat", None))

            threading.Thread(target=agent_worker, daemon=True).start()
            threading.Thread(target=heartbeat_worker, daemon=True).start()

            while True:
                try:
                    item_type, item_data = q.get(timeout=120)
                except queue.Empty:
                    yield f"id: {event_id}\ndata: {json.dumps({'type': 'error', 'message': 'stream timeout'}, ensure_ascii=False)}\n\n"
                    break
                if item_type == "heartbeat":
                    yield ": heartbeat\n\n"
                    continue
                # data/error/done 都分配事件ID
                event_id += 1
                # 断线重连: 跳过已经发送过的事件
                if event_id <= skip_count:
                    if item_type == "done":
                        break
                    continue
                if item_type == "data":
                    yield f"id: {event_id}\ndata: {json.dumps(item_data, ensure_ascii=False)}\n\n"
                elif item_type == "error":
                    yield f"id: {event_id}\ndata: {json.dumps({'type': 'error', 'message': item_data}, ensure_ascii=False)}\n\n"
                    break
                elif item_type == "done":
                    yield f"id: {event_id}\ndata: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    break
            stop_event.set()

        resp = Response(_safe_generate(generate()), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"  # 禁用Nginx缓冲
        return resp

    @app.route("/api/history/clear", methods=["POST"])
    def clear_history():
        agent.clear_history()
        return jsonify({"ok": True, "message": "对话历史已清空"})

    @app.route("/api/history")
    def get_history():
        """分页获取对话历史: offset=0 表示最近的消息, 越大越老. mode 参数可指定模式"""
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 20))
        mode = request.args.get("mode", "")
        # 只读返回指定模式的历史, 不改变后端当前模式 (模式切换走 /api/mode)
        if mode in ("work", "chat", "brainstorm"):
            all_msgs = agent.conversations.get(mode, [])
        else:
            all_msgs = agent.conversation
        # 从后往前取: 最近的在最后
        end = len(all_msgs) - offset
        start = max(0, end - limit)
        if start >= end:
            return jsonify({"messages": [], "has_more": False, "total": len(all_msgs)})
        batch = all_msgs[start:end]
        return jsonify({
            "messages": batch,
            "has_more": start > 0,
            "total": len(all_msgs),
            "mode": mode or agent.get_mode(),
        })

    # ============ 日志 ============
    @app.route("/api/logs")
    def logs():
        return jsonify(logger.recent(100))

    # ============ 成本 ============
    @app.route("/api/cost")
    def cost():
        return jsonify({
            "today": cost_tracker.total_today(),
            "history": cost_tracker.history(10),
            "prices": cost_tracker.get_prices(),
        })

    @app.route("/api/cost/calibrate", methods=["POST"])
    def cost_calibrate():
        data = request.get_json()
        model = data.get("model", "deepseek-chat")
        actual_cost = float(data.get("actual_cost", 0))
        period = data.get("period", "today")
        if actual_cost <= 0:
            return jsonify({"error": "实际花费必须大于0"}), 400
        result = cost_tracker.calibrate(model, actual_cost, period)
        return jsonify(result)

    # ============ 系统状态 ============
    @app.route("/api/stats")
    def stats():
        return jsonify(agent.stats())

    # ============ 错误自动诊断 ============
    @app.route("/api/diagnose", methods=["POST"])
    def diagnose():
        """出错时自动检查相关模块状态, 返回每个模块的健康度和建议"""
        data = request.get_json(silent=True) or {}
        error_msg = data.get("msg", "")
        checks = {}

        # 1. LLM 连接
        try:
            from config import DECISION_CONFIG
            checks["LLM API"] = {
                "ok": bool(DECISION_CONFIG.get("api_key")),
                "message": f"base_url={DECISION_CONFIG.get('base_url','?')[:40]}, model={DECISION_CONFIG.get('model','?')}"
            }
        except Exception as e:
            checks["LLM API"] = {"ok": False, "message": str(e)}

        # 2. Embedding
        try:
            from config import EMBEDDING_CONFIG
            checks["Embedding"] = {
                "ok": bool(EMBEDDING_CONFIG.get("api_key")),
                "message": f"model={EMBEDDING_CONFIG.get('model','?')}"
            }
        except Exception as e:
            checks["Embedding"] = {"ok": False, "message": str(e)}

        # 3. 搜索 API
        try:
            from config import SEARCH_CONFIG
            active = [k for k in ["tavily_api_key","serper_api_key","baidu_api_key"] if SEARCH_CONFIG.get(k)]
            checks["Search API"] = {
                "ok": len(active) > 0,
                "message": f"可用: {', '.join(active) if active else '无'}"
            }
        except Exception as e:
            checks["Search API"] = {"ok": False, "message": str(e)}

        # 4. Linux/WSL
        try:
            linux_ok = agent.linux and agent.linux.available if hasattr(agent, 'linux') else False
            checks["Linux/WSL"] = {
                "ok": linux_ok,
                "message": "WSL2 Ubuntu 已连接" if linux_ok else "未连接或不可用"
            }
        except Exception as e:
            checks["Linux/WSL"] = {"ok": False, "message": str(e)}

        # 5. 工具系统
        try:
            tool_count = len(agent.decision.tool_manager.tools) if hasattr(agent, 'decision') and agent.decision.tool_manager else 0
            checks["Tool System"] = {
                "ok": tool_count > 0,
                "message": f"已加载 {tool_count} 个工具"
            }
        except Exception as e:
            checks["Tool System"] = {"ok": False, "message": str(e)}

        # 6. 向量库
        try:
            vec_ok = hasattr(agent, 'secretary') and agent.secretary and hasattr(agent.secretary, 'vector_store')
            checks["Vector Store"] = {
                "ok": vec_ok,
                "message": "向量库就绪" if vec_ok else "未初始化"
            }
        except Exception as e:
            checks["Vector Store"] = {"ok": False, "message": str(e)}

        # 7. 对话历史
        try:
            hist_count = len(agent.conversation)
            checks["Conversation"] = {
                "ok": True,
                "message": f"当前模式 {agent.current_mode}, {hist_count} 条历史"
            }
        except Exception as e:
            checks["Conversation"] = {"ok": False, "message": str(e)}

        # 生成建议
        failed = [k for k, v in checks.items() if not v["ok"]]
        suggestion = ""
        if "LLM API" in failed:
            suggestion += "检查 .env 中的 API key 和 base_url 是否正确。"
        if "Linux/WSL" in failed:
            suggestion += "确认 WSL2 Ubuntu 已启动 (wsl -l -v)。"
        if "Search API" in failed:
            suggestion += "搜索工具不可用, 可在 .env 配置 TAVILY/SERPER/BAIDU key。"
        if not suggestion and failed:
            suggestion = f"以下模块异常: {', '.join(failed)}, 请检查相关配置。"
        if not failed:
            suggestion = "所有模块正常, 错误可能是瞬时网络问题或输入格式问题。"

        return jsonify({"checks": checks, "suggestion": suggestion, "error": error_msg})

    # ============ 模式切换 ============
    @app.route("/api/mode", methods=["GET"])
    def get_mode():
        return jsonify({"mode": agent.get_mode(),
                        "available": ["work", "chat", "brainstorm"]})

    @app.route("/api/mode", methods=["POST"])
    def set_mode():
        data = request.get_json() or {}
        mode = data.get("mode", "work")
        actual = agent.set_mode(mode)
        return jsonify({"ok": True, "mode": actual, "history_count": len(agent.conversation)})

    @app.route("/api/task-stats")
    def task_stats():
        return jsonify(agent.stats_tracker.summary())

    # ============ 四库 ============
    @app.route("/api/libraries")
    def libraries():
        libs = agent.secretary.libs
        def _fmt(i):
            meta = i.get("meta", {}) or {}
            return {"id": i["id"], "content": i["content"],
                    "time": i["timestamp"], "mode": meta.get("mode", "")}
        return jsonify({
            "tools": [_fmt(i) for i in libs.tools.all()],
            "knowledge": [_fmt(i) for i in libs.knowledge.all()],
            "experience": [_fmt(i) for i in libs.experience.all()],
            "memory": [_fmt(i) for i in libs.memory.all()],
        })

    @app.route("/api/libraries/add", methods=["POST"])
    def libraries_add():
        data = request.get_json()
        lib_name = data.get("library", "")
        content = data.get("content", "").strip()
        if not content:
            return jsonify({"error": "内容不能为空"}), 400
        lib_map = {
            "tools": agent.secretary.libs.tools,
            "knowledge": agent.secretary.libs.knowledge,
            "experience": agent.secretary.libs.experience,
            "memory": agent.secretary.libs.memory,
        }
        lib = lib_map.get(lib_name)
        if not lib:
            return jsonify({"error": "未知库"}), 400
        item = lib.add(content)
        return jsonify({"ok": True, "id": item["id"]})

    @app.route("/api/libraries/delete", methods=["DELETE"])
    def libraries_delete():
        lib_name = request.args.get("library", "")
        item_id = request.args.get("id", type=int)
        lib_map = {
            "tools": agent.secretary.libs.tools,
            "knowledge": agent.secretary.libs.knowledge,
            "experience": agent.secretary.libs.experience,
            "memory": agent.secretary.libs.memory,
        }
        lib = lib_map.get(lib_name)
        if not lib or item_id is None:
            return jsonify({"error": "参数错误"}), 400
        lib.delete(item_id)
        return jsonify({"ok": True})

    @app.route("/api/libraries/compact", methods=["POST"])
    def libraries_compact():
        """经验库压缩: 去重合并提炼 (同步, 手动触发)"""
        result = agent.secretary.compact_experience()
        return jsonify(result)

    @app.route("/api/libraries/compact-async", methods=["POST"])
    def libraries_compact_async():
        """经验库压缩: 后台异步触发, 立即返回"""
        result = agent.secretary.compact_experience_async()
        return jsonify(result)

    @app.route("/api/libraries/compact-status")
    def libraries_compact_status():
        return jsonify(agent.secretary.compact_status())

    # ============ 项目档案 ============
    @app.route("/api/project-profile")
    def project_profile_get():
        """查询项目档案: ?path= 指定路径查单个, 不传则列出全部"""
        path = request.args.get("path", "").strip()
        if path:
            prof = agent.project_profile.get(path)
            if not prof:
                # 尝试按目录前缀匹配
                prof = agent.project_profile.get_for_directory(path)
            if not prof:
                return jsonify({"error": "未找到项目档案", "path": path}), 404
            return jsonify(prof)
        return jsonify({"profiles": agent.project_profile.list()})

    @app.route("/api/project-profile", methods=["DELETE"])
    def project_profile_delete():
        data = request.get_json(silent=True) or {}
        path = data.get("path", "").strip()
        if not path:
            return jsonify({"error": "path 不能为空"}), 400
        ok = agent.project_profile.delete(path)
        return jsonify({"ok": ok, "path": path})

    @app.route("/api/project-profile/refresh", methods=["POST"])
    def project_profile_refresh():
        """重新分析项目并刷新档案"""
        data = request.get_json(silent=True) or {}
        path = data.get("path", ".").strip()
        tool = agent.tool_manager.get_tool("project_analyze")
        if not tool:
            return jsonify({"error": "project_analyze 工具未加载"}), 500
        result_text = tool.execute(path=path)
        prof = agent.project_profile.get(path)
        return jsonify({"ok": True, "path": path, "profile": prof, "preview": result_text[:300]})

    @app.route("/api/libraries/clean-memory", methods=["POST"])
    def libraries_clean_memory():
        """记忆库清理: 删除任务流水账"""
        result = agent.secretary.clean_memory()
        return jsonify(result)

    @app.route("/api/knowledge/import", methods=["POST"])
    def knowledge_import():
        """抓取网页并加入知识库"""
        data = request.get_json()
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "URL 不能为空"}), 400
        result = agent.add_knowledge_url(url)
        return jsonify(result)

    # ============ Linux ============
    @app.route("/api/linux", methods=["POST"])
    def linux_exec():
        data = request.get_json()
        command = data.get("command", "").strip()
        if not command:
            return jsonify({"error": "命令不能为空"}), 400
        result = agent.run_linux_command(command)
        return jsonify(result)

    @app.route("/api/linux/info")
    def linux_info():
        if agent.linux:
            return jsonify(agent.linux.info())
        return jsonify({"mode": "未启用"})

    # ============ 示例数据 ============
    @app.route("/api/seed", methods=["POST"])
    def seed():
        result = agent.secretary.seed_demo_data()
        return jsonify(result)

    # ============ 远程嵌入 ============
    @app.route("/api/embed/snippet")
    def embed_snippet():
        """返回可嵌入其他网页的 iframe 代码"""
        base = request.host_url.rstrip("/")
        snippet = (
            f'<!-- Nexus Agent 嵌入 -->\n'
            f'<iframe src="{base}/embed" '
            f'width="400" height="600" '
            f'style="border:1px solid #ddd;border-radius:8px;" '
            f'allow="clipboard-read;clipboard-write"></iframe>'
        )
        return Response(snippet, mimetype="text/plain")

    @app.route("/embed")
    def embed_page():
        """精简版对话页面, 适合 iframe 嵌入"""
        return render_template("embed.html")

    # ============ 外部 API (供其他应用调用) ============
    @app.route("/api/v1/ask", methods=["POST"])
    def api_ask():
        """供外部应用调用的简洁 API: 传入 message, 返回 answer"""
        data = request.get_json()
        message = data.get("message", "").strip()
        if not message:
            return jsonify({"error": "message 不能为空"}), 400
        if not agent.secretary.configured or not agent.decision.configured:
            return jsonify({"error": "未配置 API Key"}), 400
        record = agent.run(message)
        return jsonify({
            "answer": record["result"],
            "context": record["context"],
            "reflection": record.get("reflection", ""),
            "cost": record["cost"]["total_cost_yuan"],
            "time_s": record["timing"]["total_s"],
        })

    return app


    @app.errorhandler(Exception)
    def handle_unexpected_error(e):
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def main():
    app = create_app()
    print(f"\n{'='*50}")
    print(f"  Nexus 双核 Agent 已启动")
    print(f"  控制台: http://{WEB_CONFIG['host']}:{WEB_CONFIG['port']}")
    print(f"  嵌入页: http://{WEB_CONFIG['host']}:{WEB_CONFIG['port']}/embed")
    print(f"  API:    http://{WEB_CONFIG['host']}:{WEB_CONFIG['port']}/api/v1/ask")
    print(f"{'='*50}\n")
    app.run(host=WEB_CONFIG["host"], port=WEB_CONFIG["port"],
            debug=WEB_CONFIG["debug"], threaded=True)


if __name__ == "__main__":
    main()
