"""
代码错误自动诊断模块 —— Nexus 的"代码排错本能"
当 Nexus 写的代码运行出错时, 自动:
1. 解析错误信息 (traceback / 编译错误 / 运行时错误)
2. 提取出错文件、行号、错误类型
3. 自动读取出错文件 + 相关联的导入模块
4. 把代码上下文注入 LLM, 让 Nexus 自己分析并修正
不需要用户手动干预。
"""
import os
import re
import json
from typing import Optional


class CodeErrorDiagnoser:
    """代码错误自动诊断器"""

    # 常见错误模式
    ERROR_PATTERNS = [
        # Python traceback
        (r'File "([^"]+)", line (\d+)', 'python'),
        # Node.js
        (r'at .+ \(([^:]+):(\d+):\d+\)', 'node'),
        (r'Error: .+', 'node'),
        # 通用文件引用
        (r'([A-Za-z]:\\[^\s:]+\.\w+):(\d+)', 'generic'),
        (r'(/[^\s:]+\.\w+):(\d+)', 'generic'),
        # 模块未找到
        (r'ModuleNotFoundError: No module named [\'"]([^\'"]+)[\'"]', 'python_import'),
        (r"Cannot find module '([^']+)'", 'node_import'),
        # 语法错误
        (r'SyntaxError: .+', 'syntax'),
        (r'IndentationError: .+', 'syntax'),
    ]

    def __init__(self, work_dir: str = None):
        self.work_dir = work_dir or os.getcwd()
        self.last_diagnosis: Optional[dict] = None
        self.max_related_files = 5  # 最多读取多少个关联文件

    def diagnose(self, tool_output: str, tool_name: str = "", tool_args: dict = None) -> dict:
        """
        分析工具返回的错误输出, 自动读取相关代码。
        返回诊断结果, 可直接注入 LLM 上下文。
        """
        tool_args = tool_args or {}
        errors = self._parse_errors(tool_output)
        files_to_read = set()
        missing_modules = []

        for err in errors:
            if err.get('file'):
                files_to_read.add(err['file'])
            if err.get('module'):
                missing_modules.append(err['module'])

        # 也检查工具参数里提到的文件
        for key in ['file_path', 'path', 'filename', 'file', 'command', 'code']:
            val = tool_args.get(key, '')
            if isinstance(val, str):
                found = self._extract_files_from_text(val)
                files_to_read.update(found)

        # 读取出错文件的代码
        code_contexts = []
        for fpath in list(files_to_read)[:self.max_related_files]:
            code = self._read_file_safely(fpath)
            if code:
                related = self._find_related_imports(code, fpath)
                code_contexts.append({
                    'file': fpath,
                    'code': code,
                    'related_imports': related,
                })
                # 也读取关联的导入文件
                for rel in related[:3]:
                    rel_code = self._read_file_safely(rel)
                    if rel_code:
                        code_contexts.append({
                            'file': rel,
                            'code': rel_code[:3000],
                            'related_imports': [],
                        })

        # 如果没找到具体文件, 但有错误信息, 尝试从工作目录找相关文件
        if not code_contexts and tool_output:
            code_contexts = self._guess_related_files(tool_output, tool_name, tool_args)

        result = {
            'tool_name': tool_name,
            'errors': errors,
            'missing_modules': missing_modules,
            'code_contexts': code_contexts,
            'files_examined': len(code_contexts),
        }
        self.last_diagnosis = result
        return result

    def _parse_errors(self, output: str) -> list:
        """从工具输出中解析错误信息"""
        errors = []
        for pattern, etype in self.ERROR_PATTERNS:
            for match in re.finditer(pattern, output):
                err = {'type': etype}
                if etype in ('python', 'node', 'generic'):
                    err['file'] = match.group(1)
                    err['line'] = int(match.group(2)) if len(match.groups()) > 1 else None
                elif etype in ('python_import', 'node_import'):
                    err['module'] = match.group(1)
                elif etype == 'syntax':
                    err['message'] = match.group(0)
                if err not in errors:
                    errors.append(err)
        return errors

    def _extract_files_from_text(self, text: str) -> set:
        """从文本中提取文件路径"""
        files = set()
        # Windows 路径
        for m in re.finditer(r'[A-Za-z]:\\[^\s"\'<>|]+\.\w+', text):
            files.add(m.group(0))
        # Unix 路径
        for m in re.finditer(r'(?:\./|\.\./|/)[^\s"\'<>|]+\.\w+', text):
            files.add(m.group(0))
        return files

    def _read_file_safely(self, fpath: str) -> Optional[str]:
        """安全读取文件, 处理相对路径和不存在的情况"""
        # 尝试直接路径
        candidates = [fpath]
        # 尝试工作目录下的相对路径
        if not os.path.isabs(fpath):
            candidates.append(os.path.join(self.work_dir, fpath))
        # 尝试去掉引号
        clean = fpath.strip('"\'')
        if clean != fpath:
            candidates.append(clean)
            candidates.append(os.path.join(self.work_dir, clean))

        for c in candidates:
            try:
                if os.path.exists(c) and os.path.isfile(c):
                    with open(c, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    # 限制大小, 防止大文件撑爆上下文
                    if len(content) > 8000:
                        content = content[:8000] + '\n... (文件过长, 已截断)'
                    return content
            except Exception:
                continue
        return None

    def _find_related_imports(self, code: str, current_file: str) -> list:
        """从代码中提取导入的模块, 找到对应的文件"""
        imports = []
        # Python import
        for m in re.finditer(r'(?:from|import)\s+([a-zA-Z_][\w.]*)', code):
            mod = m.group(1)
            mod_path = mod.replace('.', os.sep)
            # 尝试找对应的 .py 文件
            for ext in ['.py', '/__init__.py']:
                candidate = os.path.join(os.path.dirname(current_file), mod_path + ext)
                if os.path.exists(candidate):
                    imports.append(candidate)
                    break
        # Node require/import
        for m in re.finditer(r'(?:require|from)\s*[\'"]([^\'"]+)[\'"]', code):
            mod = m.group(1)
            if not mod.startswith('.'):
                continue  # 跳过 node_modules
            candidate = os.path.join(os.path.dirname(current_file), mod)
            for ext in ['.js', '.ts', '/index.js', '/index.ts']:
                if os.path.exists(candidate + ext):
                    imports.append(candidate + ext)
                    break
        return imports

    def _guess_related_files(self, output: str, tool_name: str, tool_args: dict) -> list:
        """没找到具体文件时, 猜测相关文件"""
        contexts = []
        # 如果是 code_exec, 代码本身就在参数里
        if tool_name == 'code_exec' and 'code' in tool_args:
            contexts.append({
                'file': '(内联代码)',
                'code': tool_args['code'][:5000],
                'related_imports': [],
            })
        # 如果是 linux_terminal, 检查命令里提到的文件
        elif tool_name == 'linux_terminal' and 'command' in tool_args:
            cmd = tool_args['command']
            files = self._extract_files_from_text(cmd)
            for f in list(files)[:3]:
                code = self._read_file_safely(f)
                if code:
                    contexts.append({'file': f, 'code': code, 'related_imports': []})
        # 扫描工作目录下最近修改的代码文件
        if not contexts:
            try:
                recent = []
                for root, dirs, files in os.walk(self.work_dir):
                    if any(skip in root for skip in ['.git', '__pycache__', 'node_modules', '.venv']):
                        continue
                    for f in files:
                        if f.endswith(('.py', '.js', '.ts', '.go', '.rs', '.java')):
                            fp = os.path.join(root, f)
                            recent.append((os.path.getmtime(fp), fp))
                recent.sort(reverse=True)
                for _, fp in recent[:3]:
                    code = self._read_file_safely(fp)
                    if code:
                        contexts.append({'file': fp, 'code': code[:3000], 'related_imports': []})
            except Exception:
                pass
        return contexts

    def to_context_string(self, diagnosis: dict = None) -> str:
        """把诊断结果格式化成可注入 LLM 上下文的字符串"""
        d = diagnosis or self.last_diagnosis
        if not d:
            return ""

        lines = ["\n【代码错误自动诊断 —— 上一步执行出错了, 系统已自动收集相关代码】"]

        if d['errors']:
            lines.append("\n错误分析:")
            for i, err in enumerate(d['errors'], 1):
                if err.get('file'):
                    lines.append(f"  {i}. 文件: {err['file']}" + (f":{err['line']}" if err.get('line') else ""))
                if err.get('module'):
                    lines.append(f"  {i}. 缺少模块: {err['module']}")
                if err.get('message'):
                    lines.append(f"  {i}. {err['message']}")

        if d['missing_modules']:
            lines.append(f"\n缺少依赖: {', '.join(d['missing_modules'])}")
            lines.append("  → 请检查是否需要安装依赖, 或者修正导入路径")

        if d['code_contexts']:
            lines.append(f"\n已自动读取 {d['files_examined']} 个相关文件:")
            for ctx in d['code_contexts']:
                lines.append(f"\n--- 文件: {ctx['file']} ---")
                lines.append(ctx['code'][:4000])
                if ctx.get('related_imports'):
                    lines.append(f"  (关联导入: {', '.join(ctx['related_imports'])})")

        lines.append("\n请根据以上代码上下文分析错误原因, 然后直接修正代码并重新执行。不要只说原因, 要给出修复后的完整代码。")
        return "\n".join(lines)
