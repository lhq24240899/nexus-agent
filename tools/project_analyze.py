"""
项目结构分析工具 —— 编码前自动理解项目
识别: 语言/框架/依赖/入口/测试框架/目录结构
"""
import json
from pathlib import Path
from tools.base_tool import BaseTool

ALLOWED_ROOTS = [
    Path("D:/nexus_agent"),
    Path("D:/"),
    Path("C:/Users/1"),
    Path.home(),
]

# 框架/语言识别规则
LANGUAGE_MARKERS = {
    "Python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "*.py"],
    "JavaScript": ["package.json", "*.js", "*.jsx"],
    "TypeScript": ["tsconfig.json", "*.ts", "*.tsx"],
    "Go": ["go.mod", "*.go"],
    "Rust": ["Cargo.toml", "*.rs"],
    "Java": ["pom.xml", "build.gradle", "*.java"],
    "C/C++": ["CMakeLists.txt", "Makefile", "*.c", "*.cpp", "*.h"],
    "Ruby": ["Gemfile", "*.rb"],
    "PHP": ["composer.json", "*.php"],
}

FRAMEWORK_MARKERS = {
    "Python": {
        "Django": ["manage.py", "django"],
        "Flask": ["flask"],
        "FastAPI": ["fastapi"],
        "LangChain": ["langchain"],
    },
    "JavaScript": {
        "React": ["react"],
        "Vue": ["vue"],
        "Angular": ["@angular"],
        "Node.js": ["express", "koa"],
        "Next.js": ["next"],
    },
    "TypeScript": {
        "React": ["react"],
        "Vue": ["vue"],
        "Next.js": ["next"],
        "NestJS": ["@nestjs"],
    },
}

TEST_MARKERS = {
    "Python": ["pytest", "unittest", "tox.ini", "tests/", "test_*.py"],
    "JavaScript": ["jest", "mocha", "vitest", "*.test.js", "*.spec.js"],
    "TypeScript": ["jest", "vitest", "*.test.ts", "*.spec.ts"],
    "Go": ["*_test.go"],
    "Rust": ["#[test]", "tests/"],
    "Java": ["junit", "*.java", "src/test/"],
}

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "*.egg-info",
    "data", "logs", ".next", ".nuxt", "coverage",
}


def _is_allowed(path: str) -> bool:
    try:
        p = Path(path).resolve()
        for root in ALLOWED_ROOTS:
            try:
                p.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False
    except Exception:
        return False


def _should_skip(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


class ProjectAnalyzeTool(BaseTool):
    name = "project_analyze"
    description = (
        "分析项目结构。扫描目录树、识别技术栈、统计代码行数、列出主要模块。"
        "【何时用】接到新项目/新任务时，先了解项目结构和技术栈。"
        "【不要用】已经了解项目结构时不要重复调用；找具体文件用file_list；找具体代码用code_search。"
    )

    def __init__(self, profile_manager=None):
        self.profile_manager = profile_manager
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "项目根目录路径"},
            "max_depth": {"type": "integer", "description": "目录扫描深度, 默认3"},
        },
        "required": ["path"],
    }

    def analyze(self, path: str = ".", max_depth: int = 3) -> dict:
        """结构化分析项目, 返回字典 (供 execute 格式化和档案保存共用)"""
        root = Path(path).resolve()
        # 收集文件
        all_files = []
        dirs = []
        for item in root.rglob("*"):
            try:
                rel = item.relative_to(root)
                depth = len(rel.parts)
                if depth > max_depth + 1:
                    continue
                if any(_should_skip(p) for p in rel.parts):
                    continue
                if item.is_file():
                    all_files.append(str(rel))
                elif item.is_dir() and depth <= max_depth:
                    dirs.append(str(rel) + "/")
            except Exception:
                continue

        # 识别语言
        languages = []
        for lang, markers in LANGUAGE_MARKERS.items():
            for marker in markers:
                if marker.startswith("*."):
                    ext = marker[1:]
                    if any(f.endswith(ext) for f in all_files):
                        languages.append(lang)
                        break
                else:
                    if any(marker in f for f in all_files):
                        languages.append(lang)
                        break

        # 识别框架
        frameworks = []
        package_content = ""
        req_content = ""
        for f in all_files:
            if f.endswith("package.json"):
                try:
                    with open(root / f, "r", encoding="utf-8", errors="replace") as fh:
                        package_content = fh.read()
                except Exception:
                    pass
            if f in ("requirements.txt", "pyproject.toml"):
                try:
                    with open(root / f, "r", encoding="utf-8", errors="replace") as fh:
                        req_content = fh.read()
                except Exception:
                    pass

        for lang, fw_map in FRAMEWORK_MARKERS.items():
            if lang not in languages:
                continue
            for fw, keywords in fw_map.items():
                content = package_content if lang in ("JavaScript", "TypeScript") else req_content
                if any(kw in content for kw in keywords):
                    frameworks.append(f"{lang} - {fw}")

        # 识别测试框架
        tests = []
        for lang, markers in TEST_MARKERS.items():
            if lang not in languages:
                continue
            for marker in markers:
                if marker.startswith("*.") or marker.endswith("/"):
                    if any(marker.replace("*", "") in f for f in all_files):
                        tests.append(lang)
                        break
                else:
                    content = package_content if lang in ("JavaScript", "TypeScript") else req_content
                    if marker in content:
                        tests.append(lang)
                        break

        # 找入口文件
        entry_candidates = [
            f for f in all_files
            if f.split("/")[-1].lower() in (
                "main.py", "app.py", "index.py", "run.py", "manage.py",
                "main.js", "index.js", "app.js", "server.js",
                "main.ts", "index.ts", "app.ts",
                "main.go", "main.rs", "Main.java",
            )
        ]

        # 依赖文件
        dep_files = [f for f in all_files if f.split("/")[-1] in (
            "requirements.txt", "pyproject.toml", "package.json", "go.mod",
            "Cargo.toml", "pom.xml", "build.gradle", "Gemfile", "composer.json",
            "Pipfile", "setup.py",
        )]

        # 目录结构
        tree_lines = []
        top_dirs = sorted(set(d.split("/")[0] for d in dirs if "/" in d or d.endswith("/")))
        for d in top_dirs[:20]:
            tree_lines.append(f"  {d}/")
        top_files = sorted(f for f in all_files if "/" not in f)
        for f in top_files[:30]:
            tree_lines.append(f"  {f}")

        return {
            "root": str(root),
            "languages": languages,
            "frameworks": frameworks,
            "tests": tests,
            "entry_files": entry_candidates,
            "dep_files": dep_files,
            "file_count": len(all_files),
            "tree": tree_lines,
        }

    def execute(self, path: str = ".", max_depth: int = 3, **kwargs) -> str:
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"
        root = Path(path).resolve()
        if not root.exists():
            return f"错误: 目录不存在: {path}"
        if not root.is_dir():
            return f"错误: 不是目录: {path}"

        try:
            info = self.analyze(path, max_depth)
        except Exception as e:
            return f"扫描失败: {e}"

        languages = info["languages"]
        frameworks = info["frameworks"]

        # 构建输出
        output = []
        output.append(f"📦 项目分析: {info['root']}")
        output.append(f"{'=' * 50}")
        output.append(f"语言: {', '.join(languages) if languages else '未识别'}")
        output.append(f"框架: {', '.join(frameworks) if frameworks else '未识别'}")
        output.append(f"测试: {', '.join(info['tests']) if info['tests'] else '未发现测试'}")
        output.append(f"文件数: {info['file_count']} (扫描深度 {max_depth})")
        output.append("")

        if info["entry_files"]:
            output.append("🚪 入口文件:")
            for f in info["entry_files"][:5]:
                output.append(f"  - {f}")
            output.append("")

        if info["dep_files"]:
            output.append("📦 依赖文件:")
            for f in info["dep_files"]:
                output.append(f"  - {f}")
            output.append("")

        output.append("📁 目录结构:")
        output.extend(info["tree"])
        output.append("")

        # 编码建议
        output.append("💡 编码建议:")
        if "Python" in languages:
            output.append("  - 用 code_search 找函数定义, file_read 读文件")
            output.append("  - 改完用 code_exec 运行 pytest 验证")
            if "FastAPI" in str(frameworks) or "Flask" in str(frameworks):
                output.append("  - Web 项目, 改完可用 code_exec 启动测试")
        if "JavaScript" in languages or "TypeScript" in languages:
            output.append("  - 用 code_search 找组件/函数, file_read 读源码")
            output.append("  - 改完用 code_exec 运行 npm test / npm run lint")
        if not languages:
            output.append("  - 未识别语言, 先用 file_list 查看目录结构")

        # 自动保存项目档案
        if self.profile_manager:
            try:
                self.profile_manager.save(
                    path=info["root"],
                    languages=languages,
                    frameworks=frameworks,
                    test_frameworks=info["tests"],
                    entry_files=info["entry_files"],
                    dep_files=info["dep_files"],
                )
                output.append("")
                output.append("✅ 项目档案已保存, 后续编码任务自动注入")
            except Exception as e:
                output.append(f"⚠️ 档案保存失败: {e}")

        return "\n".join(output)
