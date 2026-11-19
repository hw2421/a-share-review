# -*- coding: utf-8 -*-
"""v51：明日交易机会推荐（前置条件统计）。

用户口径：「统计符合交易复盘所有股票前一天对应的市场条件及题材条件、包括空间压制
作为前置条件，放到今日复盘决策首个显示模块作为明日交易机会推荐对应个股给我并显示
推荐逻辑」。

实现：
1) 历史画像 profile：遍历 review.summit 全部山顶洞人买入案例，取「买入日前一交易日」
   的市场条件（市场阶段 stage、空间压制 suppress 档、涨停家数 ztCount）与题材条件
   （该股题材在前一交易日是否有连板梯队），三元组归类统计笔数 / 胜率 / 平均盈亏，
   得到「具备什么前置条件 → 次日买入赚钱效应如何」的画像。
2) 按日推荐 byDate：对每个决策日 d（d 即「明日交易的前一日」），市场条件取 d 当日，
   候选个股取当日 ladder_full 中「2 板及以上、非一字（买得进）」的赚钱效应题材龙头，
   其题材梯队条件取 d 当日该题材强度；用三元组匹配历史画像，给出胜率 / 笔数 / 均盈亏
   与可解释的推荐逻辑文字。

一字板当日买不进，排除在推荐之外（与全站口径一致）。
"""
from collections import defaultdict


def supp_cat(v):
    """空间压制档位：None/0=无压制，1~4=低压制，>=5=高压制。"""
    if v in (None, 0, 'None'):
        return '无压制'
    try:
        v = int(v)
    except (TypeError, ValueError):
        return '无压制'
    return '低压制(≤4板)' if v <= 4 else '高压制(≥5板)'


def lad_cat(g):
    """题材梯队档：前一交易日该题材最高板>=2 视为有连板梯队。"""
    if not g:
        return '题材无/弱梯队'
    mb = g.get('maxB') or 0
    if not mb and g.get('boards'):
        mb = max(g['boards'])
    return '题材有连板梯队' if mb >= 2 else '题材无/弱梯队'


def compute(dates, days, ladder_full, ladders, review_summit, yizi, NAME=None):
    NAME = NAME or {}
    didx = {d: i for i, d in enumerate(dates)}
    SD = {s['date']: s for s in (days or [])}

    # ---------- 1) 历史前置条件画像 ----------
    groups = defaultdict(list)
    used = 0
    for x in review_summit:
        d = x.get('d')
        i = didx.get(d)
        if i is None or i == 0:
            continue
        pd = dates[i - 1]
        st = SD.get(pd) or {}
        stage = st.get('stage') or '未知'
        sc = supp_cat(st.get('suppress'))
        th = x.get('theme') or ''
        g = (ladders.get(pd) or {}).get(th)
        lc = lad_cat(g)
        pnl = x.get('pnl')
        if pnl is None:
            continue
        groups[(stage, sc, lc)].append(float(pnl))
        used += 1

    profile = []
    for (stage, sc, lc), arr in groups.items():
        n = len(arr)
        win = sum(1 for p in arr if p > 0)
        avg = sum(arr) / n
        profile.append({
            'stage': stage, 'supp': sc, 'lad': lc, 'n': n,
            'win': round(win / n * 100, 1), 'avg': round(avg, 2),
            'key': stage + '/' + sc + '/' + lc,
        })
    # 笔数多的排前，便于前端默认展示最有代表性的组合
    profile.sort(key=lambda z: -z['n'])
    pmap = {p['key']: p for p in profile}
    # 全局基准（全部买入案例）
    allpnl = [p for arr in groups.values() for p in arr]
    base_win = round(sum(1 for p in allpnl if p > 0) / len(allpnl) * 100, 1) if allpnl else 0
    base_avg = round(sum(allpnl) / len(allpnl), 2) if allpnl else 0

    # ---------- 2) 按日明日机会推荐 ----------
    byDate = {}
    for d in dates:
        st = SD.get(d) or {}
        stage = st.get('stage') or '未知'
        sc = supp_cat(st.get('suppress'))
        market = {
            'stage': stage, 'suppress': st.get('suppress'), 'supp': sc,
            'ztCount': st.get('ztCount'), 'spaceTop': st.get('spaceTop'),
        }
        LF = ladder_full.get(d) or {}
        recs = []
        seen = set()
        for th, arr in LF.items():
            g = (ladders.get(d) or {}).get(th) or {}
            lc = lad_cat(g)
            thmax = g.get('maxB') or (max(g['boards']) if g.get('boards') else 0) or 0
            thcnt = g.get('count') or len(arr)
            for x in arr:
                if (x.get('boards') or 1) < 2:
                    continue
                code = str(x.get('code') or '')
                if not code or code in seen:
                    continue
                # 一字当日买不进 → 排除
                if x.get('s') == '一字' or code in (yizi.get(d) or set()):
                    continue
                seen.add(code)
                key = stage + '/' + sc + '/' + lc
                pf = pmap.get(key)
                win = pf['win'] if pf else None
                avg = pf['avg'] if pf else None
                pn = pf['n'] if pf else 0
                # 推荐强度：历史胜率相对基准的超额
                edge = round(win - base_win, 1) if win is not None else None
                logic = (
                    '【前置条件】买入前一日(%s)市场=%s、空间压制=%s、题材梯队=%s；'
                    '【本股】%s[%s] %d板（%s封板 %s），所属「%s」当日涨停%d只/最高%d板，%s；'
                    '【历史画像】同类前置条件 %d 笔，胜率 %s%%（全样本 %s%%），均盈亏 %s%%。'
                ) % (
                    '今日', stage, str(st.get('suppress')) + '板' if st.get('suppress') else '无压制',
                    lc, x.get('name') or code, code, x.get('boards') or 0,
                    x.get('s') or '换手', x.get('t') or '—', th, thcnt, thmax,
                    '有连板梯队' if lc == '题材有连板梯队' else '无连板梯队',
                    pn, ('%.1f' % win) if win is not None else '—',
                    ('%.1f' % base_win), ('%+.2f' % avg) if avg is not None else '—',
                )
                recs.append({
                    'code': code, 'name': x.get('name') or NAME.get(code, ''),
                    'boards': x.get('boards') or 0, 'theme': th, 'shape': x.get('s') or '',
                    't': x.get('t') or '', 'thcnt': thcnt, 'thmax': thmax,
                    'key': key, 'win': win, 'avg': avg, 'n': pn, 'edge': edge,
                    'logic': logic,
                })
        # 按历史胜率降序、板数降序（高胜率先推）
        recs.sort(key=lambda z: (-(z['win'] if z['win'] is not None else -1), -z['boards']))
        byDate[d] = {'market': market, 'recs': recs}

    return {
        'profile': profile, 'byDate': byDate,
        'base': {'win': base_win, 'avg': base_avg, 'n': used},
        'rule': ('前置条件=买入前一日的市场阶段 / 空间压制档 / 题材连板梯队档；'
                 '历史画像取 review.summit 全部山顶洞人买入案例，按同样前置条件归类统计胜率与均盈亏；'
                 '明日机会候选=决策日当日 2 板及以上、非一字的赚钱效应题材龙头，'
                 '市场与题材条件取决策日当日（即「明日交易的前一日」）口径。'
                 '空间压制：无压制/低压制(≤4板)/高压制(≥5板)；一字买不进已排除。'),
    }
