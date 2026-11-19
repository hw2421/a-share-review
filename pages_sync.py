# -*- coding: utf-8 -*-
"""把最新站点数据同步到 GitHub（Pages 随 main 分支自动重新发布）。
用法：python3 pages_sync.py          # 有变化才提交推送
      python3 pages_sync.py --force  # 无变化也推一次
令牌从本地 .gh_token 读取（不入库）。
"""
import os
import subprocess
import sys

OUT = os.path.dirname(os.path.abspath(__file__))
TOKEN_F = os.path.join(OUT, ".gh_token")
TRACK = ["index.html", "data.js", "version.js", "README.md",
         ".nojekyll", "klines/"]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=OUT, timeout=kw.pop("timeout", 600), **kw)


def main():
    force = "--force" in sys.argv
    if not os.path.exists(TOKEN_F):
        print("缺少 .gh_token，跳过同步")
        return 0
    tok = open(TOKEN_F).read().strip()
    st = run(["git", "status", "--porcelain", "--"] + TRACK)
    changed = [l for l in (st.stdout or "").splitlines() if l.strip()]
    if not changed and not force:
        print("站点无变化，无需同步")
        return 0
    print("变更 %d 个文件" % len(changed))
    run(["git", "add", "--"] + TRACK)
    ver = ""
    try:
        ver = open(os.path.join(OUT, "version.js")).read().strip()
    except Exception:
        pass
    c = run(["git", "commit", "-m", "data: 站点数据同步 %s" % ver])
    if c.returncode != 0 and not force:
        print("commit：%s" % (c.stdout or c.stderr).strip()[:120])
        return 0
    auth = "Authorization: Basic " + __import__("base64").b64encode(
        ("hw2421:%s" % tok).encode()).decode()
    p = run(["git", "-c", "http.extraHeader=" + auth, "push", "origin", "main"],
            timeout=900)
    ok = p.returncode == 0
    print("push %s：%s" % ("成功" if ok else "失败",
                           (p.stdout or p.stderr).strip().splitlines()[-1][:160]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
