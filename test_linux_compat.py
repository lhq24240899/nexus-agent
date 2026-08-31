"""
Linux 终端命令兼容性测试
自动跑一遍常见命令, 找出哪些在无状态 shell 下有问题
"""
import sys
sys.path.insert(0, '.')
from system.linux_embed import LinuxEmbed

le = LinuxEmbed()
results = []

def test(name, command, expect_keyword=None, expect_in_stdout=True, cwd_after=None):
    """执行命令并检查结果"""
    r = le.exec(command, timeout=10)
    ok = r.get("ok", False)
    stdout = r.get("stdout", "")
    stderr = r.get("stderr", "")
    cwd = r.get("cwd", "")

    passed = ok
    if expect_keyword:
        if expect_in_stdout:
            passed = passed and (expect_keyword in stdout)
        else:
            passed = passed and (expect_keyword not in stdout)
    if cwd_after:
        passed = passed and (cwd_after in cwd)

    status = "PASS" if passed else "FAIL"
    results.append((status, name, command, stdout[:80], stderr[:80], cwd))
    print(f"[{status}] {name}: {command}")
    if not passed:
        print(f"       stdout: {stdout[:120]}")
        print(f"       stderr: {stderr[:120]}")
        print(f"       cwd: {cwd}")
    return passed

print("=" * 60)
print("Linux 终端命令兼容性测试")
print("=" * 60)

# 重置到 ~
le.exec("cd ~")

# === 基础命令 ===
print("\n--- 基础命令 ---")
test("pwd", "pwd", expect_keyword="/home/")
test("whoami", "whoami", expect_keyword="nexus")
test("ls", "ls")
test("ls -la", "ls -la")
test("echo", "echo hello", expect_keyword="hello")
test("uname", "uname -a", expect_keyword="Linux")

# === 目录操作 (核心: cwd 保持) ===
print("\n--- 目录操作 (cwd 保持) ---")
le.exec("cd ~")
test("mkdir", "mkdir _test_dir")
test("cd into dir", "cd _test_dir", cwd_after="_test_dir")
test("pwd after cd", "pwd", expect_keyword="_test_dir")
test("ls in dir", "ls")
test("cd back", "cd ..", cwd_after="nexus")
test("rmdir", "rmdir _test_dir")

# === 文件操作 ===
print("\n--- 文件操作 ---")
le.exec("cd ~")
test("write file", "echo 'test content' > _test_file.txt")
test("cat file", "cat _test_file.txt", expect_keyword="test content")
test("cp file", "cp _test_file.txt _test_copy.txt")
test("mv file", "mv _test_copy.txt _test_renamed.txt")
test("rm file", "rm _test_file.txt _test_renamed.txt")

# === 管道和重定向 ===
print("\n--- 管道和重定向 ---")
test("pipe", "ls / | grep bin", expect_keyword="bin")
test("redirect", "echo 'pipe test' > _pipe.txt && cat _pipe.txt", expect_keyword="pipe test")
le.exec("rm -f _pipe.txt")

# === 多命令组合 ===
print("\n--- 多命令组合 ---")
test("&& chain", "mkdir _chain && cd _chain && pwd", expect_keyword="_chain")
le.exec("cd ~ && rmdir _chain")
test("; chain", "echo first; echo second", expect_keyword="first")

# === 文本处理 ===
print("\n--- 文本处理 ---")
test("grep", "echo 'hello world' | grep hello", expect_keyword="hello")
test("wc", "echo 'one two three' | wc -w", expect_keyword="3")
test("head", "ls / | head -3")

# === 网络 ===
print("\n--- 网络 ---")
test("curl", "curl -s --max-time 5 https://example.com | head -5", expect_keyword="html")

# === Python ===
print("\n--- Python ---")
test("python3", "python3 -c 'print(1+1)'", expect_keyword="2")

# === Git ===
print("\n--- Git ---")
test("git version", "git --version", expect_keyword="git")

# === 已知不支持的命令 (应该报错但不崩溃) ===
print("\n--- 已知限制 (应该优雅失败) ---")
test("vim (interactive)", "vim --version | head -1")  # vim --version 可以跑
test("timeout", "sleep 2", )  # 应该在超时内完成

# === 清理 ===
print("\n--- 清理测试文件 ---")
le.exec("cd ~ && rm -rf _test_dir _test_file.txt _test_copy.txt _test_renamed.txt _pipe.txt _chain")

# === 汇总 ===
print("\n" + "=" * 60)
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
print(f"结果: {passed} 通过, {failed} 失败, 共 {len(results)} 项")
if failed:
    print("\n失败项:")
    for status, name, cmd, out, err, cwd in results:
        if status == "FAIL":
            print(f"  - {name}: {cmd}")
            print(f"    stderr: {err}")
print("=" * 60)
