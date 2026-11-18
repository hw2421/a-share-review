# -*- coding: utf-8 -*-
"""湖南人复盘 Excel 摄取：把用户上传的月度工作簿（每个交易日一个 sheet，
sheet 内嵌复盘长图）OCR 解析为 题材 / 涨停池（连板数、末次时间）数据。
输出 .excel_cache.json：{"themes": {...}, "pools": {...仅THS缺失日}, "overview": {...}}"""
import io
import json
import os
import re
import sys
import tempfile
import zipfile
import hashlib
import time as _time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd
from PIL import Image

UP = "/root/uploads"
OUT = os.path.dirname(os.path.abspath(__file__))
OCR_CACHE = os.path.join(OUT, ".xlsx_ocr_cache.json")
SEGH, OV = 1400, 90
WORKERS = 4          # 沙箱 CPU 配额仅 4 核（cgroup cpu.max=400000/100000）
INTRA_OP = 1

# 生成限制线程数的 RapidOCR 配置（默认 -1 = 用满全部核心，多进程下会互相踩踏）
CFG_PATH = os.path.join(OUT, ".rapidocr_cfg.yaml")


def make_cfg():
    import rapidocr_onnxruntime
    base = os.path.join(os.path.dirname(rapidocr_onnxruntime.__file__),
                        "config.yaml")
    txt = open(base, encoding="utf-8").read()
    txt = txt.replace(": &intra_nums -1", ": &intra_nums %d" % INTRA_OP)
    txt = txt.replace(": &inter_nums -1", ": &inter_nums 1")
    open(CFG_PATH, "w", encoding="utf-8").write(txt)


_ENGINE = None


def _init_worker():
    global _ENGINE
    from rapidocr_onnxruntime import RapidOCR
    _ENGINE = RapidOCR(config_path=CFG_PATH)


def _ocr_one(job):
    """子进程：OCR 一张图 -> (key, [lines])"""
    key, data = job
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(data)
    tmp.close()
    lines = []
    try:
        im = Image.open(tmp.name).convert("RGB")
        W, H = im.size
        y = 0
        while y < H:
            crop = im.crop((0, y, W, min(y + SEGH, H)))
            fn = tmp.name + ".seg.png"
            crop.save(fn)
            res, _ = _ENGINE(fn)
            if res:
                lines.extend(r[1].strip() for r in res)
            try:
                os.unlink(fn)
            except OSError:
                pass
            if y + SEGH >= H:
                break
            y += SEGH - OV
    except Exception as e:
        print("  OCR失败 %s: %s" % (key[:8], e), flush=True)
        lines = []
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return key, lines


def sheet_images(z):
    """{sheet名: [图片bytes]}，经 workbook→sheet→drawing→media 关系链解析"""
    names = set(z.namelist())
    wbxml = z.read("xl/workbook.xml").decode("utf-8", "ignore")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "ignore")
    rid2tgt = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    out = {}
    for m in re.finditer(r'<sheet[^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wbxml):
        nm, rid = m.group(1), m.group(2)
        tgt = rid2tgt.get(rid) or ""
        ws = tgt.split("/")[-1]
        relp = "xl/worksheets/_rels/%s.rels" % ws
        if relp not in names:
            continue
        sx = z.read(relp).decode("utf-8", "ignore")
        dm = re.search(r'Target="([^"]*drawings/[^"]+?)"[^>]*', sx)
        if not dm:
            continue
        dpath = os.path.normpath("xl/worksheets/" + dm.group(1)).replace("\\", "/")
        if dpath not in names:
            continue
        drelp = "xl/drawings/_rels/%s.rels" % dpath.split("/")[-1]
        if drelp not in names:
            continue
        dx = z.read(dpath).decode("utf-8", "ignore")
        dr = z.read(drelp).decode("utf-8", "ignore")
        rid2media = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', dr))
        imgs = []
        for em in re.finditer(r'r:embed="([^"]+)"', dx):
            mp = os.path.normpath("xl/drawings/" + (rid2media.get(em.group(1)) or "")).replace("\\", "/")
            if mp in names:
                imgs.append(z.read(mp))
        if imgs:
            out[nm] = imgs
    return out


def norm_seal(t):
    m = re.match(r"(\d{1,2}):(\d{2})", (t or "").strip())
    if not m:
        return "12:00:00"
    return "%02d:%s:00" % (int(m.group(1)), m.group(2))


def main():
    cache = {}
    if os.path.exists(OCR_CACHE):
        try:
            cache = json.load(open(OCR_CACHE, encoding="utf-8"))
        except Exception:
            cache = {}

    # THS 涨停池缓存（用于 2026 日期校准 + 判断哪些日期需要用 excel 补池）
    pool_cache = fd.load_pool_cache()
    ths_dates = set(pool_cache)

    # 同一个月常被重复上传（不同时间戳前缀，内容 md5 完全一致）→ 按年月去重，
    # 只取一份，避免 sheet_images 重复解析大文件、重复入队。
    _seen = {}
    for fn in sorted(os.listdir(UP)):
        m = re.match(r"\d+-(20\d{4})\.xlsx$", fn)
        if m and "~$" not in fn:
            _seen.setdefault(m.group(1), fn)
    books = sorted(_seen.values())
    # 支持按年份分批（YEARS=2024,2023），便于长时间 OCR 中断后续跑
    _ye = os.environ.get("YEARS", "").strip()
    if _ye:
        _want = set(x for x in re.split(r"[, ]+", _ye) if x)
        books = [f for f in books
                 if re.search(r"-(20\d{2})\d{2}\.xlsx$", f).group(1) in _want]
    print("工作簿(按月去重 %d 份): %s" % (
        len(books), [f.split('-')[-1] for f in books]), flush=True)
    # 结构：[(date, [img bytes...]), ...]
    jobs = []
    for fn in books:
        ym = re.search(r"-(20\d{2})(\d{2})\.xlsx$", fn)   # 只取文件名末尾年月，避免长数字前缀误匹配
        yy, mm = ym.group(1), ym.group(2)
        path = os.path.join(UP, fn)
        z = zipfile.ZipFile(path)
        sheets = sheet_images(z)
        for nm, imgs in sorted(sheets.items()):
            if not re.match(r"^\d{4}$", nm):
                continue
            if nm[:2] == mm:
                date = yy + mm + nm[2:4]
            else:
                date = yy + nm          # 兜底：工作簿年 + sheet 月日
            jobs.append((date, imgs))
        z.close()
    print("待处理 sheet(交易日) %d 个、图片 %d 张" % (
        len(jobs), sum(len(i) for _, i in jobs)), flush=True)

    # 每图一个任务（md5 缓存跳过已识别图）
    img_jobs = []                     # [(date, [key...]), ...] 按 sheet 保序
    todo = {}                         # key -> bytes（未缓存的）
    total = 0
    for date, imgs in jobs:
        keys = []
        for b in imgs:
            total += 1
            k = hashlib.md5(b).hexdigest()
            keys.append(k)
            if k not in cache:
                todo[k] = b
        img_jobs.append((date, keys))
    print("需 OCR %d/%d 张（缓存命中 %d）" % (
        len(todo), total, total - len(todo)), flush=True)

    t0 = _time.time()
    done_n = [0]
    if todo:
        make_cfg()
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        with ProcessPoolExecutor(max_workers=WORKERS, mp_context=ctx,
                                 initializer=_init_worker) as ex:
            futs = [ex.submit(_ocr_one, (k, b)) for k, b in todo.items()]
            for fut in as_completed(futs):
                k, lines = fut.result()
                cache[k] = lines
                done_n[0] += 1
                if done_n[0] % 20 == 0:
                    json.dump(cache, open(OCR_CACHE, "w", encoding="utf-8"),
                              ensure_ascii=False)
                    print("  ... %d/%d 张  %.0fs" % (done_n[0], len(todo),
                                                    _time.time() - t0),
                          flush=True)
    json.dump(cache, open(OCR_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    print("OCR 完成 %.0fs" % (_time.time() - t0), flush=True)

    # 按 sheet 顺序组装每交易日 OCR 行
    ocr_lines = {}
    for date, keys in img_jobs:
        parts = []
        for k in keys:
            parts.extend(cache.get(k, []))
        ocr_lines[date] = parts

    # 连板数校准映射（THS 覆盖日期用池递推口径校准）
    streaks = fd.compute_streaks(pool_cache)
    calib = {}
    for d in pool_cache:
        calib[d] = {s["code"]: {**s, "boards": streaks[d].get(s["code"], 1)}
                    for s in pool_cache[d]}

    # 增量：保留已解析年份（2025-2026 等），只补/刷新本次处理的年份。
    # 长时间 OCR 若中断，已完成年份的数据不会被下一批覆盖丢失。
    try:
        _prev = json.load(open(os.path.join(OUT, ".excel_cache.json"),
                               encoding="utf-8"))
    except Exception:
        _prev = {}
    themes = dict(_prev.get("themes") or {})
    pools = dict(_prev.get("pools") or {})
    overview = dict(_prev.get("overview") or {})
    hunan_boards = {}                     # 湖南人连板锚点（题材块+市场连板股块）
    n_fix = [0]                           # 版式兜底重解的天数
    # 非涨停块：炸板/跌停/昨涨停表现里的个股不是当日涨停，禁止入池与锚点
    POISON = {"涨停炸板", "炸板", "跌停", "跌停股", "昨日涨停表现"}
    for date in sorted(ocr_lines):
        lines = ocr_lines[date]
        if not lines:
            continue
        ov, blocks = fd.parse_article(lines, calib.get(date, {}))
        # 版式兜底：个别月份复盘图的"代码列在图右侧"，标准解析会把连板数全部读成首板，
        # 此时改走时间锚点解析重解一次
        if fd._layout_is_time_anchored(lines):
            ov, blocks = fd.parse_article(lines, calib.get(date, {}), True)
            n_fix[0] += 1
        for b in blocks:
            if b["name"] in POISON:
                continue
            for s in b.get("stocks", []):
                if s.get("hunan"):
                    _a = hunan_boards.setdefault(date, {})
                    if s["code"] not in _a:
                        _a[s["code"]] = s["boards"]
        # 题材（口径与主流程一致：涨停>=5 且 含连板）
        ts = []
        for b in blocks:
            if b["name"] in fd.NON_THEME:
                continue
            maxb = max([s["boards"] for s in b["stocks"]] or [0])
            if b["count"] >= 5 and maxb >= 2:
                b["maxB"] = maxb
                ts.append(b)
        if ts:
            themes[date] = ts
        overview[date] = ov
        # 池：仅 THS 缺失的日期用 excel 个股并集补（同股优先取湖南人标注的板数）
        if date not in ths_dates:
            stocks = {}
            for b in blocks:
                if b["name"] in POISON:
                    continue
                for s in b.get("stocks", []):
                    ex = stocks.get(s["code"])
                    if ex is not None:
                        # 已有湖南人标注的不覆盖；否则优先补上有标注的
                        if ex["_hb"] or not s.get("hunan"):
                            continue
                    seal = norm_seal(s.get("time"))
                    stocks[s["code"]] = {
                        "code": s["code"], "name": s["name"],
                        "boards": max(1, s.get("boards", 1)),
                        "reason": s.get("reason", ""),
                        "first_seal": seal,
                        "open_num": 0 if seal <= "09:26:00" else 1,
                        "turnover": 0, "chg": 0, "days": "",
                        "_hb": bool(s.get("hunan")),
                    }
            for s in stocks.values():
                s.pop("_hb", None)
            if stocks:
                pools[date] = list(stocks.values())

    print("版式兜底重解 %d 天" % n_fix[0], flush=True)
    print("题材日 %d 天；excel 补池 %d 天（%s ~ %s）" % (
        len(themes), len(pools), min(pools) if pools else "-",
        max(pools) if pools else "-"), flush=True)
    out = {"themes": themes, "pools": pools, "overview": overview}
    with open(os.path.join(OUT, ".excel_cache.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    # 连板锚点合并写入共享文件（restore_themes/update_daily 也会追加）
    hb_path = os.path.join(OUT, ".hunan_boards.json")
    try:
        merged = json.load(open(hb_path, encoding="utf-8"))
    except Exception:
        merged = {}
    merged.update(hunan_boards)
    with open(hb_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False)
    print("完成 -> .excel_cache.json（湖南人锚点 %d 天）" % len(merged), flush=True)


if __name__ == "__main__":
    main()
