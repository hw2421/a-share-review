# -*- coding: utf-8 -*-
"""v54：明日交易机会推荐（盈利个股前置特征画像 + 历史符合实例）。

用户口径：
「复盘推荐的设置逻辑是根据模拟交易的战法，选取盈利的个股，对其前一日的市场情绪、
  题材阶段、个股情况进行总结提取特征，用于复盘推荐明日的可能性机会，
  给出推荐理由，以及历史符合推荐的实例。」

实现要点（相对 v51 的升级）：
1) 画像样本只取「盈利」的山顶洞人模拟买入（pnl > 0）——盈利即战法真正赚到钱的个股；
   对每笔盈利买入取其「前一日 T=B-1」的三维特征：
     · 市场情绪 = 市场阶段 + 涨停家数档 + 空间压制档
     · 题材阶段 = 该题材 money 阶段(启动/加强/反复/转弱) + 梯队高度档
     · 个股情况 = 该股在 T 日所属板位档 + 涨停形态 + 首封时间档
2) 三维特征分布按「盈利样本 vs 全部买入」对比，得到 lift（哪些特征是赚钱的高发特征），
   作为「总结提取特征」的结论；
3) 逐决策日 d（d 即「明日交易的前一日」），候选取 d 当日 2 板及以上、非一字的
   赚钱效应题材龙头，计算其 d 当日同样的三维特征；
4) 用「先精确三维 → 二维 → 一维 → 全样本」的逐级回退匹配历史画像，给出胜率 / 笔数 /
   均盈亏 / 超额，并生成可读的推荐理由；
5) 每只推荐股附「历史符合推荐的实例」：历史上赚过钱、且与本股特征同档的真实买入案例
   （日期 / 个股 / 题材 / 盈亏 / 板位），前端可点实例个股看其买入日 K 线。

一字板当日买不进，排除在推荐之外（与全站口径一致）。
"""
from collections import defaultdict


# ---------------- 分档工具 ----------------
def supp_cat(v):
    if v in (None, 0, 'None'):
        return '无压制'
    try:
        v = int(v)
    except (TypeError, ValueError):
        return '无压制'
    return '低压制' if v <= 4 else '高压制'


def zt_bucket(n):
    n = n or 0
    if n < 30:
        return '冰点<30'
    if n < 60:
        return '温和30-59'
    if n < 100:
        return '活跃60-99'
    return '过热≥100'


def seal_bucket(t):
    if not t:
        return '未知封板'
    try:
        return '秒板' if t <= '10:00' else ('早盘' if t <= '10:30' else
               ('盘中' if t <= '13:30' else '尾盘'))
    except (TypeError, ValueError):
        return '未知封板'


def board_tier(b):
    b = b or 0
    if b <= 1:
        return '首板/未入梯队'
    if b == 2:
        return '2板'
    if b == 3:
        return '3板'
    return '4板+'


def feat_sent(st):
    """市场情绪特征（某日）：阶段 + 涨停家数档 + 空间压制档。"""
    st = st or {}
    return (st.get('stage') or '未知') + '·' + zt_bucket(st.get('ztCount')) + '·' + supp_cat(st.get('suppress'))


def feat_theme(T, th, money, LF):
    """题材阶段特征（某日）：money 阶段 + 该题材梯队高度档。"""
    th = th or ''
    m = None
    for r in (money.get(T) or []):
        if r.get('theme') == th:
            m = r
            break
    stage = (m.get('stage') if m else None) or '未知'
    arr = (LF.get(T) or {}).get(th) or []
    mb = max((x.get('boards') or 1) for x in arr) if arr else 0
    lad = '高≥3板' if mb >= 3 else ('中2板' if mb == 2 else '弱<2板')
    return stage + '·' + lad


def find_lad_entry(code, dayarr):
    for x in (dayarr or []):
        if str(x.get('code')) == str(code):
            return x
    return None


def feat_stock(code, day, LF, boards=None, shape=None, t=None):
    """个股情况特征（某日）：板位档 + 涨停形态 + 首封时间档。
    优先取该股在该日 ladderFull 中的真实记录；无则回退传入值。"""
    entry = None
    for arr in (LF.get(day) or {}).values():
        entry = find_lad_entry(code, arr)
        if entry:
            break
    b = (entry.get('boards') if entry else None)
    if not b:
        b = boards or 0
    s = (entry.get('s') if entry else None) or shape or ''
    tm = (entry.get('t') if entry else None) or t or ''
    return board_tier(b) + '·' + (s or '普通') + '·' + seal_bucket(tm)


# ---------------- 画像索引 ----------------
def _add(idx, key, rec):
    idx[key].append(rec)


def compute(dates, days, ladder_full, ladders, review_summit, yizi, NAME=None, MONEY=None):
    NAME = NAME or {}
    MONEY = MONEY or {}
    didx = {d: i for i, d in enumerate(dates)}
    SD = {s['date']: s for s in (days or [])}

    # ---------- 1) 逐笔盈利交易：提取「前一日」三维特征 ----------
    trades = []          # 全部买入（用于胜率分母）
    profit = []          # 盈利买入（用于特征画像与历史实例）
    for x in review_summit:
        B = x.get('d')
        i = didx.get(B)
        if i is None or i == 0:
            continue
        T = dates[i - 1]
        th = x.get('theme') or ''
        code = str(x.get('code') or '')
        pnl = x.get('pnl')
        if pnl is None:
            continue
        rec = {
            'B': B, 'T': T, 'code': code, 'name': x.get('name') or NAME.get(code, ''),
            'theme': th, 'boards': x.get('boards') or 0, 'pnl': float(pnl),
        }
        rec['fs'] = feat_sent(SD.get(T))
        rec['ft'] = feat_theme(T, th, MONEY, ladder_full)
        rec['fk'] = feat_stock(code, T, ladder_full, x.get('boards'), x.get('shape'), x.get('t'))
        rec['key3'] = (rec['fs'], rec['ft'], rec['fk'])
        trades.append(rec)
        if rec['pnl'] > 0:
            profit.append(rec)

    base_win = round(sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100, 1) if trades else 0
    base_avg = round(sum(t['pnl'] for t in trades) / len(trades), 2) if trades else 0

    # 全样本 / 盈利样本 索引（逐级匹配用）
    ALL3, ALL2, ALL1S, ALL1T, ALL1K = (defaultdict(list) for _ in range(5))
    for t in trades:
        _add(ALL3, t['key3'], t)
        _add(ALL2, (t['fs'], t['ft']), t)
        _add(ALL1S, t['fs'], t)
        _add(ALL1T, t['ft'], t)
        _add(ALL1K, t['fk'], t)

    def stat(arr):
        if not arr:
            return None
        n = len(arr)
        win = sum(1 for t in arr if t['pnl'] > 0)
        return {'n': n, 'win': round(win / n * 100, 1),
                'avg': round(sum(t['pnl'] for t in arr) / n, 2), 'profitN': win}

    # ---------- 2) 盈利特征「总结提取」：各维度盈利占比 vs 全样本占比 + lift ----------
    def dist(dim):
        allc, prc = defaultdict(int), defaultdict(int)
        for t in trades:
            allc[t[dim]] += 1
        for t in profit:
            prc[t[dim]] += 1
        rows = []
        for k, n in allc.items():
            if n < 8:
                continue
            pa = round(prc[k] / len(profit) * 100, 1) if profit else 0
            aa = round(n / len(trades) * 100, 1)
            lift = round(pa / aa, 2) if aa else 0
            rows.append({'k': k, 'n': n, 'pn': prc[k], 'pct': pa, 'allpct': aa, 'lift': lift})
        rows.sort(key=lambda z: -z['pct'])
        return rows[:8]

    feature_profile = {
        'sent': dist('fs'), 'theme': dist('ft'), 'stock': dist('fk'),
        'nProfit': len(profit), 'nAll': len(trades),
        'baseWin': base_win,
    }

    def match(fs, ft, fk):
        """逐级回退：三维 → 二维(市场+题材) → 三维中的单维最优 → 全样本。"""
        arr, level, desc = ALL3.get((fs, ft, fk), []), '三维精确', '市场情绪+题材阶段+个股情况'
        if len(arr) < 5:
            arr, level, desc = ALL2.get((fs, ft), []), '二维(市场+题材)', '市场情绪+题材阶段'
        if len(arr) < 5:
            cands = [(ALL1S.get(fs, []), '市场情绪'), (ALL1T.get(ft, []), '题材阶段'),
                     (ALL1K.get(fk, []), '个股情况')]
            arr, lname = max(cands, key=lambda z: len(z[0]))
            level, desc = '一维(' + lname + ')', lname
        if len(arr) < 5:
            arr, level, desc = trades, '全样本', '全部买入基准'
        return arr, level, desc

    def instances(fs, ft, fk, limit=4):
        """历史符合推荐的实例：盈利且特征同档的真实买入。
        逐级回退：三维全匹配 → 二维(市场+题材) → 题材阶段 → 个股情况 → 任意盈利，
        保证每只推荐股都能给出「历史符合」的真实赚钱案例。"""
        def pack(t):
            return {'d': t['B'], 'code': t['code'], 'name': t['name'],
                    'theme': t['theme'], 'pnl': round(t['pnl'], 2), 'boards': t['boards']}
        seen = set()
        ins = []
        pools_order = [
            ALL3.get((fs, ft, fk), []),
            ALL2.get((fs, ft), []),
            ALL1T.get(ft, []),
            ALL1K.get(fk, []),
            ALL1S.get(fs, []),
            profit,
        ]
        for arr in pools_order:
            for t in sorted(arr, key=lambda z: (-z['pnl'], z['B'])):
                if t['pnl'] <= 0 or (t['B'], t['code']) in seen:
                    continue
                seen.add((t['B'], t['code']))
                ins.append(pack(t))
                if len(ins) >= limit:
                    return ins
        return ins

    # ---------- 3) 逐日推荐 ----------
    byDate = {}
    for d in dates:
        st = SD.get(d) or {}
        market = {
            'stage': st.get('stage'), 'suppress': st.get('suppress'),
            'supp': supp_cat(st.get('suppress')), 'ztCount': st.get('ztCount'),
            'spaceTop': st.get('spaceTop'),
        }
        fs_day = feat_sent(st)
        LF = ladder_full.get(d) or {}
        recs, seen = [], set()
        for th, arr in LF.items():
            g = (ladders.get(d) or {}).get(th) or {}
            for x in arr:
                if (x.get('boards') or 1) < 2:
                    continue
                code = str(x.get('code') or '')
                if not code or code in seen:
                    continue
                if x.get('s') == '一字' or code in (yizi.get(d) or set()):
                    continue
                seen.add(code)
                ft = feat_theme(d, th, MONEY, ladder_full)
                fk = feat_stock(code, d, ladder_full, x.get('boards'), x.get('s'), x.get('t'))
                matched, level, desc = match(fs_day, ft, fk)
                s = stat(matched)
                win = s['win'] if s else None
                avg = s['avg'] if s else None
                pn = s['profitN'] if s else 0
                edge = round(win - base_win, 1) if win is not None else None
                ins = instances(fs_day, ft, fk)
                logic = (
                    '【盈利特征·前一日(%s)】市场情绪=%s；题材阶段=%s；个股情况=%s；'
                    '【本股】%s[%s] %d板（%s封板 %s），所属「%s」当日涨停%d只/最高%d板；'
                    '【匹配画像】按%s（%s）匹配历史 %d 笔买入，其中盈利 %d 笔、胜率 %s%%'
                    '（全样本 %s%%）、均盈亏 %s%%；'
                    '【历史符合实例】%s。'
                ) % (
                    '今日', fs_day, ft, fk, x.get('name') or code, code, x.get('boards') or 0,
                    x.get('s') or '换手', x.get('t') or '—', th,
                    (g.get('count') or len(arr)),
                    (g.get('maxB') or max((y.get('boards') or 1) for y in arr)),
                    level, desc, (s['n'] if s else 0), pn,
                    ('%.1f' % win) if win is not None else '—',
                    ('%.1f' % base_win), ('%+.2f' % avg) if avg is not None else '—',
                    ('；'.join(o['name']+'('+o['d'][4:6]+'/'+o['d'][6:8]+' +'+
                              ('%.1f' % o['pnl'])+'%)' for o in ins[:3]) or '无同档盈利实例'),
                )
                recs.append({
                    'code': code, 'name': x.get('name') or NAME.get(code, ''),
                    'boards': x.get('boards') or 0, 'theme': th,
                    'shape': x.get('s') or '', 't': x.get('t') or '',
                    'thcnt': (g.get('count') or len(arr)),
                    'thmax': (g.get('maxB') or max((y.get('boards') or 1) for y in arr)),
                    'key': fs_day + '|' + ft + '|' + fk,
                    'fs': fs_day, 'ft': ft, 'fk': fk,
                    'level': level, 'win': win, 'avg': avg, 'n': (s['n'] if s else 0),
                    'pn': pn, 'edge': edge, 'logic': logic, 'instances': ins,
                })
        recs.sort(key=lambda z: (-(z['win'] if z['win'] is not None else -1),
                                 -(z['edge'] if z['edge'] is not None else -1), -z['boards']))
        byDate[d] = {'market': market, 'recs': recs}

    return {
        'featureProfile': feature_profile, 'byDate': byDate,
        'base': {'win': base_win, 'avg': base_avg, 'n': len(trades), 'nProfit': len(profit)},
        'rule': ('推荐逻辑：从山顶洞人模拟交易中只取「盈利」个股，提取其买入前一日的市场情绪'
                 '（阶段+涨停家数档+空间压制）、题材阶段（money阶段+梯队高度档）、个股情况'
                 '（板位档+涨停形态+首封时间档）三维特征；对决策日当日候选（2板及以上、非一字的'
                 '赚钱效应题材龙头）按同样三维逐级匹配历史画像（三维→二维→一维→全样本），'
                 '给出胜率/盈利笔数/均盈亏与推荐理由，并附历史上同档且真实盈利的买入实例。'
                 '「超额」=匹配画像胜率−全样本胜率；一字买不进已排除。'),
    }
