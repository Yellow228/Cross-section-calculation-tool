"""启动打包好的 exe 并等它跑完自检（GUI 程序 PowerShell 不会等，故用 subprocess）。"""

import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE_DIR = os.path.join(ROOT, "dist", "断面计算工具")
EXE = os.path.join(EXE_DIR, "断面计算工具.exe")

out = []
env = dict(os.environ)
env["QT_QPA_PLATFORM"] = "offscreen"

try:
    p = subprocess.run([EXE, "--selftest", os.path.join(ROOT, "data")],
                       cwd=EXE_DIR, env=env, timeout=300,
                       capture_output=True, text=True)
    out.append(f"返回码 = {p.returncode}")
    if p.stdout.strip():
        out.append("stdout:\n" + p.stdout[-3000:])
    if p.stderr.strip():
        out.append("stderr:\n" + p.stderr[-3000:])
except subprocess.TimeoutExpired:
    out.append("超时：exe 未在 300 秒内结束")
except Exception as e:
    out.append(f"启动失败: {e}")

log = os.path.join(EXE_DIR, "_selftest.txt")
if os.path.exists(log):
    with open(log, encoding="utf-8") as f:
        out.append("\n--- _selftest.txt ---\n" + f.read())
else:
    out.append("\n未生成 _selftest.txt")

with open(os.path.join(ROOT, "_exe_check.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
