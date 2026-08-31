"""
Nexus 双核 Agent —— 全局配置
对应视频: 决策核心(Nexus) + 秘书核心, 各自独立 API
配置优先级: .env 文件 > 环境变量 > 默认值
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# ============ 加载 .env 文件 ============
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# ============ 项目路径 ============
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ============ 决策核心 (Nexus) 配置 ============
DECISION_CONFIG = {
    "base_url": os.getenv("DECISION_BASE_URL", "https://api.deepseek.com"),
    "api_key":  os.getenv("DECISION_API_KEY", os.getenv("DS_API_KEY", "")),
    "model":    os.getenv("DECISION_MODEL", "deepseek-chat"),
    "temperature": 0.7,
}

# ============ 秘书核心配置 ============
SECRETARY_CONFIG = {
    "base_url": os.getenv("SECRETARY_BASE_URL", "https://api.deepseek.com"),
    "api_key":  os.getenv("SECRETARY_API_KEY", os.getenv("DS_API_KEY", "")),
    "model":    os.getenv("SECRETARY_MODEL", "deepseek-chat"),
    "temperature": 0.3,
}

# ============ Embedding 配置 (向量检索 / "标点定位") ============
EMBEDDING_CONFIG = {
    "base_url": os.getenv("EMBED_BASE_URL", "https://api.deepseek.com"),
    "api_key":  os.getenv("EMBED_API_KEY", os.getenv("DS_API_KEY", "")),
    "model":    os.getenv("EMBED_MODEL", "text-embedding-v3"),
    "dim":      int(os.getenv("EMBED_DIM", "1024")),
    "enabled":  os.getenv("USE_EMBEDDING", "1") == "1",
}

# ============ Linux 嵌入配置 ============
LINUX_CONFIG = {
    "mode": os.getenv("LINUX_MODE", "auto"),  # auto / docker / wsl / mock
    "wsl_distro": os.getenv("WSL_DISTRO", "Ubuntu"),
    "container_name": "nexus-linux",
    "image": "ubuntu:22.04",
}

# ============ Web UI 配置 ============
WEB_CONFIG = {
    "host": "127.0.0.1",
    "port": int(os.getenv("WEB_PORT", "7860")),
    "debug": False,
}

# ============ 搜索 API 配置 ============
SEARCH_CONFIG = {
    "tavily_api_key": os.getenv("TAVILY_API_KEY", ""),
    "serper_api_key": os.getenv("SERPER_API_KEY", ""),
    "baidu_api_key": os.getenv("BAIDU_API_KEY", ""),
    # 优先使用的搜索源: tavily / serper / baidu / duckduckgo
    "preferred": os.getenv("SEARCH_PREFERRED", "tavily"),
}

# ============ 成本监控 ============
def _cost(model: str, input_key: str, output_key: str,
          default_in: float, default_out: float) -> dict:
    return {
        "input": float(os.getenv(input_key, default_in)),
        "output": float(os.getenv(output_key, default_out)),
    }

COST_CONFIG = {
    "deepseek-chat":      {"input": 2.0, "output": 3.0},
    "deepseek-reasoner":  {"input": 4.0, "output": 16.0},
    "deepseek-v4-flash":  _cost("v4flash", "COST_SECRETARY_INPUT", "COST_SECRETARY_OUTPUT", 1.0, 2.0),
    "deepseek-v4-flash-vision-exp": _cost("v4flashvision", "COST_DECISION_INPUT", "COST_DECISION_OUTPUT", 1.5, 4.5),
    "deepseek-v4-pro":    {"input": 3.0, "output": 6.0},
    "text-embedding-v3":  _cost("embed", "COST_EMBED_INPUT", "COST_EMBED_OUTPUT", 0.5, 0.0),
    "text-embedding-3-small": _cost("embed3small", "COST_EMBED_INPUT", "COST_EMBED_OUTPUT", 0.02, 0.0),
    "default":            {"input": 2.0, "output": 4.0},
}
