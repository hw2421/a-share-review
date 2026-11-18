# -*- coding: utf-8 -*-
"""把 verify_space.py 的 12.3 段（v25~v27 旧战法校验）整体替换为 v29 校验。"""
import io

p = "/workspace/a-share-review/verify_space.py"
s = io.open(p, encoding="utf-8").read()
lines = s.split("\n")

# 定位：12.3 段起始 到 「---- 13 历史交易日历」
a = next(i for i, l in enumerate(lines) if l.strip().startswith("# ---- 12.3 "))
b = next(i for i, l in enumerate(lines) if l.strip().startswith("# ---- 13 历史交易日历"))
print("替换行区间 %d ~ %d（共 %d 行）" % (a + 1, b, b - a))

NEW = r'''    # ---- 12.3 v29：三战法口径（全新逻辑：空间突破 / 试错龙头 / 补涨）----
    # 独立复算：同花顺日K + 题材梯队 + 赚钱效应验证，不依赖 strategies 自证
    LFV = D.get("ladderFull") or {}
    V23 = D.get("v23stat") or {}
    MV = D.get("money") or {}
    BE = D.get("boardEarn") or {}
    stage_full = {x["date"]: x for x in days}
    DIDXV = {d: i for i, d in enumerate(dates)}
    KBV = {}
    for _fp in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
        for _c, _bars in json.load(open(_fp, encoding="utf-8")).items():
            _rec = KBV.setdefault(str(_c), {})
            for _b in _bars:
                if _b and len(_b) >= 5:
                    _rec[_b[0]] = _b

    def _bar(c, d):
        return (KBV.get(str(c)) or {}).get(d)

    def _pv(c, d):
        rec = KBV.get(str(c)); i = DIDXV.get(d)
        if not rec or i is None:
            return None
        for j in range(i - 1, -1, -1):
            b = rec.get(dates[j])
            if b and len(b) >= 3 and b[2]:
                return b[2]
        return None

    def _lim(c, name=""):
        if "ST" in (name or "").upper():
            return .05
        if str(c).startswith(("30", "688")):
            return .20
        if str(c).startswith(("8", "4", "92")):
            return .30
        return .10

    def _touch(c, d, name=""):
        b, pv = _bar(c, d), _pv(c, d)
        return bool(b and pv and b[4] / pv - 1 >= _lim(c, name) - .006)

    def _highopen(c, d, name=""):
        """高开但不是涨停开盘。"""
        b, pv = _bar(c, d), _pv(c, d)
        if not b or not pv:
            return False
        lp = round(pv * (1 + _lim(c, name)), 2)
        return b[1] > pv * 1.0001 and b[1] < lp - .001

    def _yizi(c, d, name=""):
        b, pv = _bar(c, d), _pv(c, d)
        return bool(b and pv and b[1] == b[2] == b[3] == b[4]
                    and b[2] / pv - 1 >= _lim(c, name) - .006)

    def _break_ok(T):
        """上一空间龙头断板日无大长阴、次日无跌停（与 strategies 同口径独立复算）。"""
        for s0 in (stage_full.get(T, {}).get("suppressStocks") or []):
            c, nm = str(s0.get("code") or ""), s0.get("name", "")
            bd = str(s0.get("breakDate") or "")
            if not c or not bd or bd > T:
                continue
            b, pv = _bar(c, bd), _pv(c, bd)
            if not b or not pv:
                continue
            chg = b[2] / pv - 1
            entity = (b[1] - b[2]) / pv
            if chg <= -.07 or (b[2] < b[1] and entity >= .05):
                return False, "%s 断板日大长阴" % nm
            i = DIDXV.get(bd)
            if i is None or i + 1 >= len(dates) or dates[i + 1] > T:
                continue
            d2 = dates[i + 1]
            b2, pv2 = _bar(c, d2), _pv(c, d2)
            if b2 and pv2 and b2[2] / pv2 - 1 <= -(_lim(c, nm) - .006):
                return False, "%s 断板次日跌停" % nm
        return True, "断板温和"

    def _barr(d, th):
        return (LFV.get(d) or {}).get(th) or []

    def _money_ok(B, th):
        if not any(r.get("theme") == th for r in (MV.get(B) or [])):
            return False, "未登当日赚钱效应榜"
        rec = (BE.get(B) or {}).get(th)
        if not rec or not rec.get("ok"):
            return False, "板块赚钱效应验证未过"
        return True, "梯队 %s" % (rec.get("ladder") or "")

    def _assist(B, th, mx):
        return any(2 <= (x.get("boards") or 1) < mx for x in _barr(B, th))

    def _cand_max(B, th):
        """当日该题材候选最高板（逐笔 + 机会池，含炸板/一字）。"""
        vals = [t.get("boards") or 1 for m in RV_METHODS
                for t in RVALL.get(m, []) if t["d"] == B and (t.get("theme") or "") == th]
        vals += [o.get("boards") or 1 for o in (CH.get(B) or [])
                 if (o.get("theme") or "") == th]
        return max(vals) if vals else 0

    n_v29 = 0
    for m in RV_METHODS:
        for t in RVALL.get(m, []):
            B = t["d"]
            i = DIDXV.get(B)
            if i is None or i == 0:
                err("v29 %s %s 买入日无前一交易日" % (m, B)); n_v29 += 1; continue
            T = dates[i - 1]
            th = t.get("theme") or ""
            code, name = t["code"], t.get("name") or ""
            # ① 买入日必须盘中冲击涨停（封板或炸板）
            if not (t.get("sealed") or t.get("burst")):
                err("v29 %s %s %s 买入日既未封板也无炸板标记" % (m, B, name)); n_v29 += 1
            if not _touch(code, B, name):
                err("v29 %s %s %s 日K未触板" % (m, B, name)); n_v29 += 1
            # ② 高开但不是涨停开盘
            if not _highopen(code, B, name):
                err("v29 %s %s %s 非「高开但不是涨停开盘」" % (m, B, name)); n_v29 += 1
            # ③ 一字买不进不得进逐笔
            if _yizi(code, B, name):
                err("v29 %s %s %s 一字涨停却进入逐笔" % (m, B, name)); n_v29 += 1
            # ④ 三战法共同前置：板块赚钱效应 + 梯队有助攻
            okm, mdesc = _money_ok(B, th)
            if not okm:
                err("v29 %s %s %s 板块「%s」%s" % (m, B, name, th, mdesc)); n_v29 += 1
            arrB = _barr(B, th)
            mxB = max((x.get("boards") or 1) for x in arrB) if arrB else 0
            if mxB < 3 or not _assist(B, th, mxB):
                err("v29 %s %s %s 板块「%s」梯队/助攻不足（最高 %d 板）"
                    % (m, B, name, th, mxB)); n_v29 += 1
            # ⑤ 分战法条件
            cmx = _cand_max(B, th)
            if m == "spaceDragon":
                st = stage_full.get(T, {})
                N = st.get("suppress") or 0
                if N < 2:
                    err("v29 战法一 %s %s 复盘日无 ≥2 板空间压制" % (B, name)); n_v29 += 1
                if (t.get("boards") or 0) != N + 1:
                    err("v29 战法一 %s %s 非 N+1 板（板位 %s / 压制 %s）"
                        % (B, name, t.get("boards"), N)); n_v29 += 1
                bok, bdesc = _break_ok(T)
                if not bok:
                    err("v29 战法一 %s %s 断板杀跌禁入未拦：%s" % (B, name, bdesc)); n_v29 += 1
                if "突破买点" not in str(t.get("why") or ""):
                    err("v29 战法一 %s %s why 缺「突破买点」留痕" % (B, name)); n_v29 += 1
            elif m == "trialLead":
                if (stage_full.get(T, {}).get("stage") or "") != "节点":
                    err("v29 战法二 %s %s 复盘日阶段非节点（%s）"
                        % (B, name, stage_full.get(T, {}).get("stage"))); n_v29 += 1
                if (t.get("boards") or 0) < 3:
                    err("v29 战法二 %s %s 买入板位不足 3 板" % (B, name)); n_v29 += 1
                if cmx and (t.get("boards") or 0) < cmx:
                    err("v29 战法二 %s %s 非当日板块最高板龙头（%s vs %s）"
                        % (B, name, t.get("boards"), cmx)); n_v29 += 1
                if "节点" not in str(t.get("why") or ""):
                    err("v29 战法二 %s %s why 缺「节点」留痕" % (B, name)); n_v29 += 1
            elif m == "fillUp":
                # 当日该题材存在「符合战法一/二但一字买不进」的龙头（触发补涨）
                trig = [o for o in (CH.get(B) or [])
                        if (o.get("theme") or "") == th
                        and o.get("m") in ("spaceDragon", "trialLead")
                        and o.get("yizi") and "触发补涨" in str(o.get("result") or "")]
                if not trig:
                    err("v29 战法三 %s %s 板块「%s」无一字龙头触发补涨" % (B, name, th)); n_v29 += 1
                if (t.get("boards") or 0) < 2:
                    err("v29 战法三 %s %s 助攻股不足 2 板" % (B, name)); n_v29 += 1
                if cmx and (t.get("boards") or 0) >= cmx:
                    err("v29 战法三 %s %s 买了龙头而非助攻股（%s vs %s）"
                        % (B, name, t.get("boards"), cmx)); n_v29 += 1
                if "补涨" not in str(t.get("why") or ""):
                    err("v29 战法三 %s %s why 缺「补涨」留痕" % (B, name)); n_v29 += 1
            else:
                err("v29 已删除战法「%s」出现在逐笔（%s %s）" % (m, B, name)); n_v29 += 1

    # ⑥ v23stat 自洽
    if V23.get("cand") != (V23.get("fire") or 0) + (V23.get("yizi") or 0) + (V23.get("noKline") or 0):
        err("v29 v23stat 自洽失败：候选 %s ≠ 参与 %s + 一字 %s + 无日K %s"
            % (V23.get("cand"), V23.get("fire"), V23.get("yizi"), V23.get("noKline"))); n_v29 += 1
    for m in RV_METHODS:
        if (V23.get("byM") or {}).get(m) != len(RVALL.get(m, [])):
            err("v29 v23stat %s 笔数与逐笔不符（%s vs %s）"
                % (m, (V23.get("byM") or {}).get(m), len(RVALL.get(m, [])))); n_v29 += 1
    # ⑦ META / 文案：仅三战法、无历史战法残留
    if set(meta.keys()) != set(RV_METHODS):
        err("v29 reviewMeta 战法集合不符：%s" % sorted(meta.keys())); n_v29 += 1
    uni29 = (meta.get("spaceDragon") or {}).get("uni") or ""
    for kw in ("v29", "高开但不是涨停开盘", "板块当日有赚钱效应", "低吸/半路/打板",
               "N+1 板", "昨日节点", "改买板块 2 板+ 换手助攻股", "盘中 −7% 无条件止损",
               "已清空此前全部战法口径"):
        if kw not in uni29:
            err("v29 统一口径 uni 缺关键字「%s」" % kw); n_v29 += 1
    buy_all = " ".join((meta.get(m) or {}).get("buy") or "" for m in RV_METHODS)
    for bad_kw in ("凹凸曼", "黑盒", "示范龙头", "v27 门槛", "首次符合条件", "连续 ≥3 日登"):
        if bad_kw in buy_all:
            err("v29 战法买入文案残留已删除口径「%s」" % bad_kw); n_v29 += 1
    for m, kw in (("spaceDragon", "断板日没有大长阴杀跌"),
                  ("trialLead", "博弈成为新的空间龙头"),
                  ("fillUp", "改买板块助攻换手连板股")):
        if kw not in ((meta.get(m) or {}).get("buy") or ""):
            err("v29 战法 %s 买入文案缺「%s」" % (m, kw)); n_v29 += 1
    # ⑧ boardEarn 与 money_effect.compute 独立复算一致
    import money_effect as ME
    EFFV = ME.compute(dates, LFV, MV, KBV)
    n_be_bad = 0
    for (bd, nm), v in EFFV.items():
        got = (BE.get(bd) or {}).get(nm)
        if not got or bool(got.get("ok")) != bool(v["ok"]):
            err("v29 boardEarn %s「%s」与独立复算不一致" % (bd, nm)); n_be_bad += 1
    n_v29 += n_be_bad
    print("v29 三战法口径：%d 笔逐笔全部复核（板块赚钱效应/断板温和/高开非涨停开盘/一字/助攻）；"
          "boardEarn %d 题材日独立复算一致（异常 %d）；候选 %s / 参与 %s / 一字 %s / 无日K %s；口径异常 %d"
          % (sum(len(RVALL.get(m, [])) for m in RV_METHODS), len(EFFV), n_be_bad,
             V23.get("cand"), V23.get("fire"), V23.get("yizi"), V23.get("noKline"), n_v29))

'''

lines[a:b] = NEW.rstrip("\n").split("\n")
s = "\n".join(lines)
# RV_METHODS 改为 v29 三战法
s = s.replace('RV_METHODS = ("spaceDragon", "trialLead", "demoLead")',
              'RV_METHODS = ("spaceDragon", "trialLead", "fillUp")')
# 头部注释更新
s = s.replace(" 12. 五战法复盘  逐笔自洽：战法一买入日必须突破、价格/卖出日存在、盈亏可复算、\n"
              "                 卖出原因合法、封板标记齐全；并校验 K线骷髅头与题材前龙状态",
              " 12. 三战法复盘  逐笔自洽：价格/卖出日存在、盈亏可复算、卖出原因合法、\n"
              "                 封板标记齐全；并校验 K线骷髅头与题材前龙已移除")
s = s.replace(" 12.2 机会池    chances 含一字买不进/炸板、逐笔是其子集、非突破战法距压制≥2板",
              " 12.2 机会池    chances 含一字买不进/炸板、逐笔是其子集")
s = s.replace(" 12.3 v18 口径   连续一字开盘标的已剔除、凹凸曼均为次日参与、空间差按买入当日",
              " 12.3 v29 口径   三战法全新逻辑独立复算：板块赚钱效应验证、断板温和、"
              "高开非涨停开盘、一字不进逐笔、N+1板/节点龙头/一字触发补涨；"
              "boardEarn 与 money_effect.compute 全量一致；META 无历史战法残留")
io.open(p, "w", encoding="utf-8").write(s)
print("done")
