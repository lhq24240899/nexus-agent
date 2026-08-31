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
        "分析项目结构, 识别编程语言、框架、依赖、入口文件、测试框架。"
        "改代码前必须先用这个工具了解项目。返回项目概览和关键文件列表。"
    )
    params_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "项目根目录路径"},
            "max_depth": {"type": "integer", "description": "目录扫描深度, 默认3"},
        },
        "required": ["path"],
    }

    def execute(self, path: str = ".", max_depth: int = 3, **kwargs) -> str:
        if not _is_allowed(path):
            return f"错误: 路径不在允许范围内: {path}"

        root = Path(path).resolve()
        if not root.exists():
            return f"错误: 目录不存在: {path}"
        if not root.is_dir():
            return f"错误: 不是目录: {path}"

        # 收集文件
        all_files = []
        dirs = []
        try:
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
        except Exception as e:
            return f"扫描失败: {e}"

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

        # 目录结构 (前2层)
        tree_lines = []
        top_dirs = sorted(set(d.split("/")[0] for d in dirs if "/" in d or d.endswith("/")))
        for d in top_dirs[:20]:
            tree_lines.append(f"  {d}/")
        top_files = sorted(f for f in all_files if "/" not in f)
        for f in top_files[:30]:
            tree_lines.append(f"  {f}")

        # 构建输出
        output = []
        output.append(f"📦 项目分析: {root}")
        output.append(f"{'=' * 50}")
        output.append(f"语言: {', '.join(languages) if languages else '未识别'}")
        output.append(f"框架: {', '.join(frameworks) if frameworks else '未识别'}")
        output.append(f"测试: {', '.join(tests) if tests else '未发现测试'}")
        output.append(f"文件数: {len(all_files)} (扫描深度 {max_depth})")
        output.append("")

        if entry_candidates:
            output.append("🚪 入口文件:")
            for f in entry_candidates[:5]:
                output.append(f"  - {f}")
            output.append("")

        if dep_files:
            output.append("📦 依赖文件:")
            for f in dep_files:
                output.append(f"  - {f}")
            output.append("")

        output.append("📁 目录结构:")
        output.extend(tree_lines)
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

        return "\n".join(output)
