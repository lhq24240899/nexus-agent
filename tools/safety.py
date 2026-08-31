"""
工具安全模块 —— 命令黑名单 + 资源限制
防止 Agent 执行危险命令或耗尽系统资源
"""
import re

# ============ 命令黑名单 ============
# 匹配到任意一条就拒绝执行
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",              # 删根目录
    r"rm\s+-rf\s+~",              # 删家目录
    r"mkfs",                       # 格式化磁盘
    r"dd\s+if=",                   # 裸设备写入
    r":\(\)\s*\{.*\};:",          # fork 炸弹
    r"shutdown|reboot|halt|poweroff",  # 关机/重启
    r">\s*/dev/sd[a-z]",          # 写裸设备
    r">\s*/dev/zero",             # 写零设备
    r"chmod\s+-R\s+777\s+/",      # 根目录 777
    r"chown\s+-R\s+.*\s+/",       # 递归改根目录属主
    r"curl.*\|\s*(bash|sh|zsh)",  # 管道执行远程脚本
    r"wget.*\|\s*(bash|sh|zsh)",  # 同上
    r"eval\s+`.*`",               # 危险 eval
    r"/etc/shadow|/etc/passwd",   # 敏感文件（读可以，写不行，这里先拦所有操作）
    r"iptables|ufw\s+reset",      # 防火墙重置
    r":\s*>\s+/dev/sda",          # 清空磁盘
]

DANGEROUS_RE = re.compile("|".join(DANGEROUS_PATTERNS), re.IGNORECASE)


def is_dangerous(command: str) -> tuple[bool, str]:
    """检查命令是否危险, 返回 (是否危险, 匹配到的规则)"""
    m = DANGEROUS_RE.search(command)
    if m:
        return True, m.group(0)
    return False, ""


# ============ 资源限制档位 ============
LIMIT_PROFILES = {
    "strict": {
        "cpu_seconds": 10,      # CPU 时间
        "wall_seconds": 15,     # 墙钟时间（timeout 命令）
        "memory_kb": 262144,    # 内存 256MB
        "max_procs": 64,        # 最大进程数（ulimit -u 是用户总进程数，不能太小）
        "file_size_kb": 5120,   # 最大文件 5MB
    },
    "normal": {
        "cpu_seconds": 30,
        "wall_seconds": 45,
        "memory_kb": 524288,    # 512MB
        "max_procs": 128,
        "file_size_kb": 10240,  # 10MB
    },
    "loose": {
        "cpu_seconds": 120,
        "wall_seconds": 180,
        "memory_kb": 1048576,   # 1GB
        "max_procs": 256,
        "file_size_kb": 51200,  # 50MB
    },
}

# 重任务关键词，命中自动用 normal 档
HEAVY_COMMAND_KEYWORDS = [
    "apt install", "apt-get install", "pip install", "pip3 install",
    "npm install", "yarn install", "pnpm install",
    "make", "cmake", "gcc ", "g++ ", "cargo build", "go build",
    "python setup.py", "pip wheel", "dpkg -i",
    "uvicorn", "flask run", "python app.py", "python manage.py",
    "node server", "npm run", "npm start",
    "tar -x", "unzip", "7z x",
    "git clone",
]


def detect_profile(command: str) -> str:
    """根据命令自动判断资源档位"""
    cmd_lower = command.lower()
    for kw in HEAVY_COMMAND_KEYWORDS:
        if kw in cmd_lower:
            return "normal"
    return "strict"


def build_safe_command(command: str, profile: str = "auto") -> tuple[str, str]:
    """
    构建带资源限制的安全命令
    返回 (安全命令, 使用的档位名)
    """
    if profile == "auto":
        profile = detect_profile(command)
    lim = LIMIT_PROFILES.get(profile, LIMIT_PROFILES["strict"])

    # ulimit 限制（CPU/内存/进程/文件大小）
    ulimit_prefix = (
        f"ulimit -t {lim['cpu_seconds']} "
        f"-v {lim['memory_kb']} "
        f"-u {lim['max_procs']} "
        f"-f {lim['file_size_kb']} "
        f"-n 64 && "
    )

    # timeout 命令（墙钟时间，-k 表示超时后先 SIGTERM，5秒后 SIGKILL）
    timeout_prefix = f"timeout -k 5 {lim['wall_seconds']}s "

    safe_cmd = ulimit_prefix + timeout_prefix + command
    return safe_cmd, profile
