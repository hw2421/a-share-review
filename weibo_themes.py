# -*- coding: utf-8 -*-
"""解析新浪微博「TGB湖南人」的涨停复盘纯文本 -> 题材块（无需 OCR）。

微博正文格式（HTML 去标签后）：
  【10.12星期一】 湖南人涨停复盘 "涨停金额:..亿 ... 连板7家"
  【市场连板股】10只 54亿  代码 名称 末次时间 连板数 原因
  002187 广百股份 11:28 4 大消费
  【锂电池】9只 120亿  代码 名称 末次时间 连板数 原因
  002454 松芝股份 9:30 1.60% 新能源汽车
"""
import re

HEAD = ("代码", "名称")


def split_blocks(text):
    """按【xx】切块 -> [(name, head_line, body_text)]"""
    parts = re.split(r"【([^】]{1,12})】", text)
    out = []
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i].strip(), parts[i + 1]))
    return out


def block_head_stats(head):
    """'9只 120亿 代码 名称 ...' -> (count, amount)"""
    m = re.search(r"(\d{1,3})\s*只\s*([\d.]+)?\s*亿?", head)
    if not m:
        m2 = re.search(r"(\d{1,3})\s*只", head)
        return (int(m2.group(1)) if m2 else 0), 0
    cnt = int(m.group(1))
    amt = float(m.group(2)) if m.group(2) else 0
    return cnt, amt


ROW = re.compile(
    r"(\d{6})\s*"                          # 代码
    r"([^\s\d]{2,8}[ABab]?)\s*"            # 名称（不以数字开头）
    r"(\d{1,2}:\d{2})\s*"                  # 末次时间
    r"(\d{1,2}(?:\s*/\s*\d{1,2}\s*天\s*\d{1,2}\s*板)?)\s+"   # 连板数（可 3/7天4板）
    r"(.*?)"                               # 原因
    r"(?=\d{6}\s|【|<|\n|$)")


def parse_rows(body):
    rows = []
    for m in ROW.finditer(body):
        code, name, tm, boards, reason = m.groups()
        if name in HEAD:
            continue
        reason = re.split(r"[；;]", reason.strip())[0].strip()
        reason = re.sub(r"<[^>]*>", "", reason).strip()
        n = int(re.match(r"\d{1,2}", boards).group(0))
        rows.append({"code": code, "name": name, "time": tm,
                     "boards": n, "reason": reason})
    return rows


def theme_blocks(text):
    """微博全文 -> 题材块列表（含市场连板股块）"""
    blocks = []
    for name, body in split_blocks(text):
        head, _, rest = body.partition("名称")     # 跳过表头
        if not _ or "时间" not in head:
            # 没有表头：整段当 body（部分微博省略表头）
            head, rest = body, body
        cnt, amt = block_head_stats(head)
        rows = parse_rows(rest)
        if not rows:
            continue
        blocks.append({"name": name, "count": cnt or len(rows),
                       "amount": amt, "stocks": rows})
    return blocks
