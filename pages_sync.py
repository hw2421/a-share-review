# -*- coding: utf-8 -*-
"""把最新站点数据同步到 GitHub（Pages 随 main 分支自动重新发布）。
用法：python3 pages_sync.py          # 有变化才提交推送
      python3 pages_sync.py --force  # 无变化也推一次
令牌从本地 .gh_token 读取（不入库）。

v119 防复发：push 成功后不再「一推了之」，而是回源核验 GitHub Pages 是否
真的把本次 commit 部署上线；若 90 秒内仍未部署（GitHub Pages 偶发跳过构建），
做一次空提交强制再推，触发重新构建。核验结果写入 .pages_sync_status.json，
便于「线上到底更没更新」一眼可查。
"""
import base64
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))
TOKEN_F = os.path.join(OUT, ".gh_token")
STATUS_F = os.path.join(OUT, ".pages_sync_status.json")
REPO = "hw2421/a-share-review"
TRACK = ["index.html", "data.js", "version.js", "README.md",
         ".nojekyll", "klines/"]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=OUT, timeout=kw.pop("timeout", 600), **kw)


def save_status(**kw):
    try:
        obj = {}
        if os.path.exists(STATUS_F):
            obj = json.load(open(STATUS_F, encoding="utf-8"))
        obj.update(kw)
        obj["at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tmp = STATUS_F + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATUS_F)
    except Exception:
        pass


def local_head():
    r = run(["git", "rev-parse", "HEAD"])
    return (r.stdout or "").strip()


def api_builds(tok, per_page=5):
    """调 GitHub Pages builds API，返回 build 列表；失败返回 None。"""
    url = "https://api.github.com/repos/%s/pages/builds?per_page=%d" % (REPO, per_page)
    req = urllib.request.Request(url, headers={
        "Authorization": "token " + tok,
        "Accept": "application/vnd.github+json",
        "User-Agent": "a-share-review-pages-sync",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception:
        return None


def local_ver():
    """本地 version.js 的 DATA_VER 指纹。"""
    try:
        s = open(os.path.join(OUT, "version.js"), encoding="utf-8").read()
        m = __import__("re").search(r'DATA_VER\s*=\s*["\']([^"\']+)["\']', s)
        return m.group(1) if m else ""
    except Exception:
        return ""


def online_ver():
    """回源线上 Pages 的 version.js 指纹（禁缓存）；不可用返回 None。"""
    url = "https://hw2421.github.io/a-share-review/version.js?ts=%d" % int(time.time()*1000)
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            s = r.read().decode("utf-8", "ignore")
        m = __import__("re").search(r'DATA_VER\s*=\s*["\']([^"\']+)["\']', s)
        return m.group(1) if m else None
    except Exception:
        return None


def wait_deployed(tok, target_ver, tries=6, interval=15):
    """轮询直到线上 Pages 的 version.js 指纹 == 本地指纹（真正部署上线）。
    用内容比对而非 builds API 的 commit：API 的 sha 在 push/构建并发时会滞后
    （实测 11-26 push 7f6f732 后 API 仍报 e776eaba 但线上已是新文件）。
    返回 True/False/None（None=网络不可用，无法核验）。"""
    for i in range(tries):
        ov = online_ver()
        if ov is None:
            return None
        if ov == target_ver:
            return True
        if i < tries - 1:
            time.sleep(interval)
    return False


def push(tok):
    auth = "Authorization: Basic " + base64.b64encode(
        ("hw2421:%s" % tok).encode()).decode()
    p = run(["git", "-c", "http.extraHeader=" + auth, "push", "origin", "main"],
            timeout=900)
    return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()


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
        save_status(result="no-change")
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
    ok, msg = push(tok)
    print("push %s：%s" % ("成功" if ok else "失败", msg.splitlines()[-1][:160] if msg else ""))
    if not ok:
        save_status(result="push-failed", detail=msg[-200:], ver=ver)
        return 1

    # v119 防复发：回源比对线上 version.js 指纹，确认 Pages 真的部署了本次数据
    head = local_head()
    my_ver = ver or local_ver()
    diag = "HEAD %s / ver %s" % (head[:7], my_ver)
    dep = wait_deployed(tok, my_ver)
    if dep is True:
        print("Pages 部署核验通过：线上指纹 %s" % my_ver)
        save_status(result="deployed", head=head[:7], ver=my_ver, detail=diag)
        return 0
    if dep is None:
        print("Pages 部署核验跳过（线上暂不可达），本次不做强制重推")
        save_status(result="verify-unavailable", head=head[:7], ver=my_ver,
                    detail=diag + "；线上 version.js 不可达")
        return 0

    # 推了但线上指纹仍不是最新 → 空提交强制再推一次，触发重新构建
    print("⚠️ 线上指纹未更新到 %s，空提交强制重推触发构建" % my_ver)
    run(["git", "commit", "--allow-empty", "-m", "chore: retrigger GitHub Pages build"])
    ok2, msg2 = push(tok)
    if not ok2:
        print("强制重推失败：%s" % (msg2.splitlines()[-1][:160] if msg2 else ""))
        save_status(result="retrigger-push-failed", head=head[:7], ver=my_ver,
                    detail=diag + "；" + msg2[-160:])
        return 1
    head2 = local_head()
    dep2 = wait_deployed(tok, my_ver, tries=6, interval=15)
    detail = diag + "；空提交重推 %s" % head2[:7]
    if dep2 is True:
        print("强制重推后部署核验通过：线上指纹 %s" % my_ver)
        save_status(result="retrigger-deployed", head=head2[:7], ver=my_ver, detail=detail)
        return 0
    # 线上暂未更新或不可达：交由前端版本自检兜底，不当作致命失败
    print("重推后 Pages 暂未确认部署，前端版本自检将兜底")
    save_status(result="retrigger-pending", head=head2[:7], ver=my_ver,
                detail=detail + "（等待 Pages 构建 / 线上不可达）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
