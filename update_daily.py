# -*- coding: utf-8 -*-
"""每日自动更新（三段式重试）：17:30 首次、19:00 重试、21:00 末次；成功即跳过后续。
用法：python3 update_daily.py [--slot 1|2|3]
流程：
  1) 增量抓同花顺涨停池（最近 8 个自然日，缓存命中跳过）
  2) 拉淘股吧@湖南人最新文章（OCR），更新湖南人口径题材
  3) build_merged.py 重建两年数据（含压制股日K、赚钱效应筛选）
  4) 发布静态站点
判定：涨停池已有当日数据（或当天为周末）且发布成功 = 成功；否则失败。
失败时在页面顶部显示红色告警，并在 .update_status.json 记录，供下一次重试判断。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import fetch_data as fd

LOG = lambda m: print("[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M"), m),
                      flush=True)

STATUS_F = os.path.join(OUT, ".update_status.json")
SLOT_NEXT = {1: "17:30", 2: "19:00", 3: "21:00", 4: None, 5: None}
SLOT_NAME = {1: "16:00 首次", 2: "17:30 重试1", 3: "19:00 重试2", 4: "21:00 末次",
             5: "湖南人主贴即抓"}


def today_theme_src(today):
    """今日题材来源：tgb/weibo = 湖南人主贴；ztpool = 同花顺兜底；空 = 无。"""
    try:
        d = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
        return (d.get("themeSource") or {}).get(today, "") or ""
    except Exception:
        return ""


def load_status():
    try:
        return json.load(open(STATUS_F, encoding="utf-8"))
    except Exception:
        return {}


def save_status(st):
    with open(STATUS_F, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def publish():
    # v34：站点已改为「单端口应用」模式（app.py 直接服务本目录），构建产物写盘即生效。
    # 若此时再调 publish.js --language static，会把线上应用覆盖回静态托管，必须跳过。
    if os.environ.get("SKIP_PUBLISH") == "1":
        LOG("发布：跳过（站点由应用进程直接服务本地文件）")
        return True, "skipped: served by app"
    r = subprocess.run(["node", "/root/.codebuddy/skills/sites/scripts/publish.js",
                        "--dir", OUT, "--language", "static"],
                       capture_output=True, text=True, timeout=240)
    ok = '"verified":true' in (r.stdout or "")
    return ok, (r.stdout or r.stderr or "")[-300:]


def write_alert(msg):
    """把失败提示写进 data.json / data.js（页面顶部红色横幅）后重新发布"""
    try:
        p = os.path.join(OUT, "data.json")
        data = json.load(open(p, encoding="utf-8"))
        data["alert"] = msg
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        import datajs
        datajs.write_obj(data, OUT)
        return publish()[0]
    except Exception as e:
        LOG("写告警失败：%s" % e)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=1, choices=[1, 2, 3, 4, 5])
    ap.add_argument("--force", action="store_true",
                    help="跳过「今天已完成 / 已生成」判断，强制重跑（湖南人主贴即抓用）")
    _a = ap.parse_args()
    slot, force = _a.slot, _a.force
    now = datetime.datetime.now()
    today = now.strftime("%Y%m%d")
    st = load_status()

    # 幂等：今天已成功更新过 → 后续重试任务直接跳过。
    # v33：但若今日题材仍是同花顺兜底（ztpool）或缺失，说明湖南人主贴还没抓到，
    # 不能算「完成」，要继续补抓。
    if st.get("date") == today and st.get("ok") and not force:
        _ts = today_theme_src(today)
        if _ts in ("tgb", "weibo"):
            LOG("今日 %s 已完成更新（%s，题材来源 %s），本次 %s 跳过"
                % (today, st.get("doneAt", ""), _ts, SLOT_NAME[slot]))
            return 0
        LOG("今日 %s 已更新但题材来源为「%s」（非湖南人主贴），继续补抓"
            % (today, _ts or "缺失"))

    # 心跳：任务有没有真正执行过，看这个文件（排查「定时任务跑了但没产出」）
    try:
        with open(os.path.join(OUT, ".update_heartbeat"), "w", encoding="utf-8") as f:
            f.write("%s slot=%d\n" % (now.strftime("%Y-%m-%d %H:%M:%S"), slot))
    except Exception:
        pass

    # v31：今天的数据若已在 15:30 之后由手动刷新 / 前序任务生成，本段直接跳过，
    # 不再重复抓取与重建（盘中生成的当日数据不完整，不在此列）。
    try:
        meta = json.load(open(os.path.join(OUT, ".site_meta.json"), encoding="utf-8"))
        upd = str(meta.get("updated") or "")
        if (meta.get("latest") == today and upd[:10] == now.strftime("%Y-%m-%d")
                and upd[11:16] >= "15:30" and not force):
            _ts = today_theme_src(today)
            if _ts in ("tgb", "weibo"):
                LOG("今日数据已生成（%s，截至 %s，题材来源 %s），本次 %s 跳过"
                    % (upd, meta.get("latest"), _ts, SLOT_NAME[slot]))
                save_status({"date": today, "ok": True, "latest": meta.get("latest"),
                             "doneAt": upd, "slot": slot, "errors": [], "skipped": True})
                return 0
            LOG("今日数据已生成（%s），但题材来源为「%s」，继续补抓湖南人题材"
                % (upd, _ts or "缺失"))
    except Exception:
        pass

    LOG("===== 第 %d 段（%s）开始 =====" % (slot, SLOT_NAME[slot]))
    errors = []
    pool_cache = {}
    published = False

    # ---- 1) 增量抓涨停池 ----
    try:
        pool_cache = fd.load_pool_cache()
        last = max(pool_cache) if pool_cache else "20260101"
        start = (datetime.datetime.strptime(last, "%Y%m%d")
                 - datetime.timedelta(days=6)).strftime("%Y%m%d")
        LOG("抓取涨停池 %s 至今 ..." % start)
        new = fd.collect_range(start)
        pc = dict(pool_cache)
        pc.update({d: v for d, v in new.items() if v})
        fd.save_pool_cache(pc)
        pool_cache = pc
        LOG("涨停池共 %d 天（最新 %s）" % (len(pc), max(pc) if pc else "-"))
    except Exception as e:
        errors.append("涨停池抓取：%s" % e)
        LOG("涨停池抓取失败：%s" % e)

    # ---- 2) 湖南人题材：微博纯文本优先，淘股吧文章 OCR 兜底 ----
    try:
        data = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
        dates = set(data["dates"])
        _pl = {d: v for d, v in pool_cache.items() if d in dates and v}
        try:
            _hb = json.load(open(os.path.join(OUT, ".hunan_boards.json"),
                                 encoding="utf-8"))
        except Exception:
            _hb = {}
        _stk = fd.compute_streaks(_pl, {d: v for d, v in _hb.items() if d in _pl})
        for _d in _pl:
            for s in _pl[_d]:
                s["boards"] = _stk[_d].get(s["code"], 1)
        pool_map = {d: {s["code"]: s for s in _pl[d]} for d in dates if d in _pl}

        # 2.1 微博文本（纯文本，无需 OCR；覆盖最近一个月内每个交易日）
        bf_path = os.path.join(OUT, ".theme_backfill.json")
        try:
            bf = json.load(open(bf_path, encoding="utf-8"))
        except Exception:
            bf = {}
        try:
            import backfill_from_weibo as BW
            posts = {p["date"]: p for p in BW.list_posts() if not p["noon"]}
            recent = sorted(pool_map)[-6:]
            n_wb = 0
            for d in recent:
                if (bf.get(d) or {}).get("themes"):
                    continue
                p = posts.get(d)
                if not p:
                    continue
                txt = BW.post_text(p["url"])
                if not txt:
                    continue
                ts = BW.pick(BW.wt.theme_blocks(txt))
                if not ts:
                    continue
                bf[d] = {"themes": ts, "src": p["url"], "via": "weibo",
                         "postTime": p["time"]}
                n_wb += 1
                LOG("  微博题材 %s：%s"
                    % (d, [(t["name"], t["count"], t["maxB"]) for t in ts][:6]))
            if n_wb:
                with open(bf_path, "w", encoding="utf-8") as f:
                    json.dump(bf, f, ensure_ascii=False)
            LOG("微博题材补 %d 天（累计 %d 天）" % (n_wb, len(bf)))
        except Exception as e:
            LOG("微博题材跳过：%s" % e)

        # 2.2 淘股吧文章 OCR（兜底：微博无当日复盘时）
        # v33：只对最近 3 个交易日做 OCR 兜底。此前取最近 30 篇，主贴刚发时
        # 会把十几篇历史文章逐一 OCR（每篇多图），单次跑十几分钟，容易撞平台超时。
        _recent3 = set(sorted(pool_map)[-3:])
        arts = sorted([a for a in fd.tgb_articles() if a["date"] in _recent3],
                      key=lambda a: a["date"])
        LOG("湖南人文章 %d 篇待检查" % len(arts))
        td = data.get("themeDetail", {})
        ts_src = data.get("themeSource", {})
        n_new = 0
        for k, a in enumerate(arts, 1):
            if a["date"] not in pool_map:
                continue
            if (bf.get(a["date"]) or {}).get("themes"):   # 微博已补到，省掉 OCR
                continue
            img_urls = fd.article_images(a["url"])
            lines = []
            for u in img_urls:
                lines.extend(fd.ocr_image(u))
            if not lines:
                continue
            d = fd.table_date(lines, a["date"])
            if d not in pool_map:
                prev = [x for x in sorted(pool_map) if x <= d]
                if not prev:
                    continue
                d = prev[-1]
            ov, blocks = fd.parse_article(lines, pool_map[d])
            hb_path = os.path.join(OUT, ".hunan_boards.json")
            try:
                hb_all = json.load(open(hb_path, encoding="utf-8"))
            except Exception:
                hb_all = {}
            for b in blocks:
                if b["name"] in ("涨停炸板", "炸板", "跌停", "跌停股", "昨日涨停表现"):
                    continue
                for s in b.get("stocks", []):
                    if s.get("hunan"):
                        hb_all.setdefault(d, {})[s["code"]] = s["boards"]
            with open(hb_path, "w", encoding="utf-8") as f:
                json.dump(hb_all, f, ensure_ascii=False)
            ts = []
            for b in blocks:
                if b["name"] in fd.NON_THEME:
                    continue
                max_bs = max([s["boards"] for s in b["stocks"]] or [0])
                if b["count"] >= 5 and max_bs >= 2:
                    b["maxB"] = max_bs
                    ts.append(b)
            if not ts:
                continue
            old_names = {t["name"] for t in td.get(d, [])}
            new_names = {t["name"] for t in ts}
            if old_names == new_names and ts_src.get(d) == "tgb":
                continue
            td[d] = ts
            ts_src[d] = "tgb"
            n_new += 1
            LOG("  题材更新 %s：%s" % (d, [(t["name"], t["count"]) for t in ts][:6]))
            time.sleep(1.0)
        data["themeDetail"] = td
        data["themeSource"] = ts_src
        with open(os.path.join(OUT, "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        LOG("湖南人题材更新 %d 天" % n_new)
    except Exception as e:
        errors.append("湖南人题材：%s" % e)
        LOG("题材更新失败：%s" % e)

    # ---- 3) 合并两年数据 ----
    try:
        # v31：与「页面手动刷新」共用构建锁，避免两个构建同时写 data.js
        import fcntl
        _blk = open(os.path.join(OUT, ".build.lock"), "w")
        fcntl.flock(_blk, fcntl.LOCK_EX)
        # v30：日常增量更新跳过全历史日K刷新（由「日K刷新·每30分钟」任务负责），
        # 避免 396MB 缓存重复加载把构建拖过平台定时任务的超时上限。
        env = dict(os.environ, SKIP_KLINES="1")
        r = subprocess.run([sys.executable, os.path.join(OUT, "build_merged.py")],
                           capture_output=True, text=True, timeout=1800, env=env)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        LOG("合并：%s" % (tail or (r.stderr or "")[-200:]))
        if r.returncode != 0:
            errors.append("合并失败：%s" % (r.stderr or "")[-200:])
    except Exception as e:
        errors.append("合并异常：%s" % e)
        LOG("合并异常：%s" % e)

    # ---- 3.5) 口径自检（空间龙头 / 空间高度 / 空间压制 / 突破 是否自洽） ----
    try:
        r = subprocess.run([sys.executable, os.path.join(OUT, "verify_space.py")],
                           capture_output=True, text=True, timeout=300)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        if r.returncode != 0:
            errors.append("口径自检未通过：%s" % last[:120])
            LOG("⚠️ 口径自检：%s" % last)
        else:
            LOG("口径自检通过")
    except Exception as e:
        LOG("口径自检跳过：%s" % e)

    # ---- 4) 发布 ----
    published, out = publish()
    LOG("发布：%s" % ("成功 " + out.strip() if published else "失败 " + out))
    if not published:
        errors.append("发布失败")

    # ---- 5) 结果判定 ----
    latest = max(pool_cache) if pool_cache else ""
    weekday = datetime.date.today().weekday()
    ok = (not errors) and published and (latest >= today or weekday >= 5)
    st = {"date": today, "ok": ok, "latest": latest,
          "doneAt": now.strftime("%Y-%m-%d %H:%M"), "slot": slot,
          "errors": errors}
    save_status(st)

    if ok:
        LOG("✅ 第 %d 段更新成功（数据截至 %s）" % (slot, latest))
        return 0

    # 失败：给出下一步/最终告警
    nxt = SLOT_NEXT.get(slot)
    if nxt:
        msg = "数据更新失败（%s），将于 %s 自动重试" % (now.strftime("%m-%d %H:%M"), nxt)
        LOG("⚠️ " + msg)
    else:
        msg = ("数据更新四次均失败（16:00 / 17:30 / 19:00 / 21:00），数据仍为 %s，"
               "请手动检查涨停池接口或湖南人复盘文章" % (latest or "旧数据"))
        LOG("❌ " + msg)
    write_alert(msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
