"""
MCP (Model Context Protocol) 客户端
轻量级实现, 支持 stdio 传输, 连接外部 MCP server 获取工具
协议: JSON-RPC 2.0 over stdio
"""
import json
import queue
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional

MCP_CONFIG = Path(__file__).parent.parent / "mcp_servers.json"


class MCPClient:
    """单个 MCP server 的客户端连接"""

    def __init__(self, name: str, command: str, args: list[str] = None,
                 env: dict = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._connected = False

    def connect(self) -> bool:
        """启动 MCP server 进程并初始化"""
        try:
            # Windows 兼容: 解析命令完整路径 (npx -> npx.cmd)
            cmd_path = shutil.which(self.command) or self.command
            self.process = subprocess.Popen(
                [cmd_path] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                env={**__import__("os").environ, **self.env, "PYTHONIOENCODING": "utf-8", "NODE_OPTIONS": "--encoding=utf-8"},
                bufsize=1,
            )
            # 发送 initialize 请求
            init_result = self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nexus-agent", "version": "1.0"},
            })
            if init_result is not None:
                # 发送 initialized 通知
                self._notify("notifications/initialized", {})
                self._connected = True
                return True
        except Exception as e:
            print(f"[MCP] 连接 {self.name} 失败: {e}")
        return False

    def list_tools(self) -> list[dict]:
        """获取 MCP server 提供的工具列表"""
        if not self._connected:
            return []
        result = self._request("tools/list", {})
        if result and "tools" in result:
            return result["tools"]
        return []

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 工具"""
        if not self._connected:
            return f"错误: MCP server '{self.name}' 未连接"
        result = self._request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if result is None:
            return f"错误: MCP 工具 '{tool_name}' 调用无响应"
        # 解析 content
        if "content" in result:
            parts = []
            for item in result["content"]:
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join(parts) if parts else str(result)
        return json.dumps(result, ensure_ascii=False)

    def _request(self, method: str, params: dict, timeout: float = 10.0) -> Optional[dict]:
        """发送 JSON-RPC 请求并等待响应 (带超时)"""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id
            message = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            result_queue = queue.Queue()

            def _read_responses():
                """后台线程读取响应, 找到匹配 id 的放入队列"""
                try:
                    for _ in range(50):
                        line = self.process.stdout.readline()
                        if not line:
                            result_queue.put(None)
                            return
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            resp = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if resp.get("id") == req_id:
                            if "error" in resp:
                                print(f"[MCP] {method} 错误: {resp['error']}")
                                result_queue.put(None)
                                return
                            result_queue.put(resp.get("result"))
                            return
                except Exception as e:
                    print(f"[MCP] 读取响应异常: {e}")
                    result_queue.put(None)

            try:
                self.process.stdin.write(json.dumps(message) + "\n")
                self.process.stdin.flush()
                reader_thread = threading.Thread(target=_read_responses, daemon=True)
                reader_thread.start()
                try:
                    result = result_queue.get(timeout=timeout)
                    return result
                except queue.Empty:
                    print(f"[MCP] {method} 超时 ({timeout}s), server 可能已挂起")
                    self._connected = False
                    return None
            except Exception as e:
                print(f"[MCP] 请求 {method} 异常: {e}")
                self._connected = False
                return None

    def _notify(self, method: str, params: dict):
        """发送 JSON-RPC 通知 (无响应)"""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except Exception:
            pass

    def disconnect(self):
        """关闭连接"""
        self._connected = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()

    def health_check(self) -> bool:
        """健康检查: 调用 tools/list 验证连接是否正常"""
        if not self._connected or not self.process:
            return False
        try:
            result = self._request("tools/list", {}, timeout=5.0)
            return result is not None
        except Exception:
            return False

    def reconnect(self) -> bool:
        """断开并重连"""
        self.disconnect()
        return self.connect()


class MCPManager:
    """MCP 管理器: 管理多个 MCP server 连接"""

    def __init__(self):
        self.clients: dict[str, MCPClient] = {}
        self._mcp_tools: list[dict] = []  # {name, server, description, parameters}
        self._load_config()
        self._connect_all()

    def _load_config(self):
        """从 mcp_servers.json 读取 server 配置"""
        if not MCP_CONFIG.exists():
            # 写入示例配置
            example = {
                "servers": {
                    "example_filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                        "enabled": False,
                        "_comment": "设为 true 后启动时自动连接。需要 Node.js 和 npx。"
                    }
                }
            }
            MCP_CONFIG.write_text(
                json.dumps(example, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return
        try:
            config = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
            for name, cfg in config.get("servers", {}).items():
                if cfg.get("enabled", False):
                    self.clients[name] = MCPClient(
                        name=name,
                        command=cfg["command"],
                        args=cfg.get("args", []),
                        env=cfg.get("env", {}),
                    )
        except Exception as e:
            print(f"[MCP] 配置加载失败: {e}")

    def _connect_all(self):
        """连接所有已配置的 MCP server 并获取工具"""
        for name, client in self.clients.items():
            if client.connect():
                tools = client.list_tools()
                for tool in tools:
                    self._mcp_tools.append({
                        "name": f"mcp_{name}_{tool['name']}",
                        "server": name,
                        "tool_name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {}),
                    })
                print(f"[MCP] {name} 已连接, 获取 {len(tools)} 个工具")
            else:
                print(f"[MCP] {name} 连接失败")

    def get_tools(self) -> list[dict]:
        """获取所有 MCP 工具的 function 定义"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in self._mcp_tools
        ]

    def restart_server(self, name: str, args: list[str] = None) -> bool:
        """重启指定 MCP server (用新 args), 切换工作空间时更新 filesystem 路径"""
        if name not in self.clients:
            print(f"[MCP] restart_server: server '{name}' 不存在")
            return False
        old_client = self.clients[name]
        command = old_client.command
        env = old_client.env
        # 1. 断开旧连接
        old_client.disconnect()
        # 2. 删除旧 server 的工具
        self._mcp_tools = [t for t in self._mcp_tools if t.get("server") != name]
        # 3. 创建新 client (用新 args)
        new_args = args if args is not None else old_client.args
        new_client = MCPClient(name=name, command=command, args=new_args, env=env)
        self.clients[name] = new_client
        # 4. 连接并获取工具
        if new_client.connect():
            tools = new_client.list_tools()
            for tool in tools:
                self._mcp_tools.append({
                    "name": f"mcp_{name}_{tool['name']}",
                    "server": name,
                    "tool_name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {}),
                })
            print(f"[MCP] {name} 已重启, 获取 {len(tools)} 个工具, args={new_args}")
            return True
        else:
            print(f"[MCP] {name} 重启失败")
            return False

    def health_check_all(self) -> dict[str, bool]:
        """健康检查所有 MCP server, 挂了自动重连, 返回 {name: ok}"""
        results = {}
        for name, client in self.clients.items():
            ok = client.health_check()
            if not ok:
                print(f"[MCP] {name} 健康检查失败, 尝试重连...")
                # 删除旧工具
                self._mcp_tools = [t for t in self._mcp_tools if t.get("server") != name]
                # 重连
                if client.reconnect():
                    tools = client.list_tools()
                    for tool in tools:
                        self._mcp_tools.append({
                            "name": f"mcp_{name}_{tool['name']}",
                            "server": name,
                            "tool_name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": tool.get("inputSchema", {}),
                        })
                    print(f"[MCP] {name} 重连成功, 获取 {len(tools)} 个工具")
                    ok = True
                else:
                    print(f"[MCP] {name} 重连失败")
            results[name] = ok
        return results

    def has_tool(self, name: str) -> bool:
        return any(t["name"] == name for t in self._mcp_tools)

    def execute(self, name: str, **kwargs) -> str:
        """执行 MCP 工具"""
        for t in self._mcp_tools:
            if t["name"] == name:
                client = self.clients.get(t["server"])
                if client:
                    return client.call_tool(t["tool_name"], kwargs)
                return f"错误: MCP server '{t['server']}' 不可用"
        return f"错误: MCP 工具 '{name}' 不存在"

    def list_servers(self) -> list[dict]:
        return [
            {"name": name, "connected": c._connected,
             "tools": len([t for t in self._mcp_tools if t["server"] == name])}
            for name, c in self.clients.items()
        ]

    def reload(self) -> str:
        """重新加载配置并连接"""
        for c in self.clients.values():
            c.disconnect()
        self.clients.clear()
        self._mcp_tools.clear()
        self._load_config()
        self._connect_all()
        return f"MCP 已重载, 当前 {len(self._mcp_tools)} 个工具"
