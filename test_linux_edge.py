import sys
sys.path.insert(0, '.')
from system.linux_embed import LinuxEmbed

le = LinuxEmbed()
le.exec('cd ~')

print('=== 边界情况测试 ===')

# 1. 环境变量不保持
le.exec('export MYVAR=hello')
r2 = le.exec('echo $MYVAR')
print(f'环境变量保持: {"FAIL" if "hello" in r2["stdout"] else "已知限制(不保持)"}')

# 2. 自定义 alias 不保持
le.exec('alias myls="ls -la"')
r4 = le.exec('myls')
print(f'自定义alias保持: {"FAIL" if r4["ok"] else "已知限制(不保持)"}')

# 3. cd - (返回上一个目录) 不保持
le.exec('cd /tmp')
le.exec('cd ~')
r5 = le.exec('cd -')
print(f'cd - : {"PASS" if r5["ok"] else "已知限制(OLDPWD不保持)"}')

# 4. cd 不带参数回 ~
le.exec('cd /tmp')
r6 = le.exec('cd')
print(f'cd 回~: {"PASS" if "home" in r6.get("cwd","") else "FAIL"}')

# 5. 绝对路径 cd
r7 = le.exec('cd /tmp')
print(f'cd /tmp: {"PASS" if "/tmp" in r7.get("cwd","") else "FAIL"}')

# 6. 相对路径文件操作
le.exec('cd ~')
le.exec('mkdir _edge && cd _edge && echo edge > edge.txt')
r8 = le.exec('cat edge.txt')
print(f'相对路径cat: {"PASS" if "edge" in r8["stdout"] else "FAIL"}')
le.exec('cd ~ && rm -rf _edge')

# 7. 内置 ll alias
r9 = le.exec('ll')
print(f'll alias: {"PASS" if r9["ok"] else "FAIL"}')

# 8. 连续 cd 多级
le.exec('cd ~')
le.exec('mkdir -p _a/b/c && cd _a/b/c')
r10 = le.exec('pwd')
print(f'多级cd: {"PASS" if "_a/b/c" in r10["stdout"] else "FAIL"}')
le.exec('cd ~ && rm -rf _a')

print()
print('=== 总结 ===')
print('能正常工作: 基础命令/目录操作/文件操作/管道/重定向/Python/Git/curl/ll alias')
print('已知限制(无状态shell): 环境变量不保持/自定义alias不保持/cd -不保持')
print('需要注意: sudo需要密码, 交互式命令(vim/top)不适用, 长时间运行会超时')
