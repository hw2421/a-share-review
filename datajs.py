# -*- coding: utf-8 -*-
"""统一写 data.json / data.js（所有会落盘主包的脚本都应调用本模块）。

线上静态托管是 python http.server，不做 gzip：全期主包若无压缩会原样传输十几 MB，
手机端首屏很慢。因此 data.js 自包含 pako inflate + zlib 压缩串，浏览器同步解压出
window.REVIEW（约 200ms），体积约为明文的 1/4；明文格式仍可正常读取，只是失去压缩。

回退：设置环境变量 NOCOMPRESS=1，或项目缺少 .pako_inflate.min.js 时，
自动退化为明文 window.REVIEW=...，功能完全等价，便于调试。
"""
import base64
import hashlib
import json
import os
import re
import zlib

PAKO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    ".pako_inflate.min.js")


def _atomic_write(path, text):
    """先写临时文件再原子替换：构建期间页面请求不会读到半截 data.js。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _write_meta(js_text, out_dir, ver):
    """给服务端 /api/status 用的轻量元信息（不用读 15MB 的 data.json）。"""
    latest, updated = "", ""
    try:
        obj = json.loads(js_text)
        ds = obj.get("dates") or []
        latest = ds[-1] if ds else ""
        updated = obj.get("updated") or ""
    except Exception:
        pass
    _atomic_write(os.path.join(out_dir, ".site_meta.json"),
                  json.dumps({"ver": ver, "latest": latest, "updated": updated},
                             ensure_ascii=False))


def _write_version(js_text, out_dir):
    """v30：写 version.js 数据指纹，供页面「刷新」回源比对。
    排除 updated / klinesAt 这类纯时间戳字段，只有真实数据变化才换指纹。
    放在 write_data 里统一处理，保证 build_merged / refresh_klines / 告警改写
    等所有会重写 data.js 的路径都不会出现「数据变了但指纹没变」。
    """
    try:
        obj = json.loads(js_text)
        # sort_keys=True：dict 的插入顺序可能受进程 hash 随机化影响（如由 set
        # 构造的映射），只归一 key 顺序才能得到跨次构建稳定的指纹。
        src = json.dumps({k: v for k, v in obj.items()
                          if k not in ("updated", "klinesAt")},
                         ensure_ascii=False, separators=(",", ":"),
                         sort_keys=True)
    except Exception:
        src = js_text
    ver = hashlib.md5(src.encode("utf-8")).hexdigest()[:12]
    _atomic_write(os.path.join(out_dir, "version.js"),
                  'window.DATA_VER="' + ver + '";')
    _write_meta(js_text, out_dir, ver)
    return ver


def write_data(js_text, out_dir):
    """js_text 为已序列化的紧凑 JSON 字符串；同时写 data.json / data.js / version.js。"""
    _atomic_write(os.path.join(out_dir, "data.json"), js_text)
    path = os.path.join(out_dir, "data.js")
    raw_n = len(js_text.encode("utf-8"))
    if os.environ.get("NOCOMPRESS") or not os.path.exists(PAKO):
        _atomic_write(path, "window.REVIEW=" + js_text + ";")
        ver = _write_version(js_text, out_dir)
        return "明文 %.2f MB（未压缩） ver=%s" % (os.path.getsize(path) / 1048576, ver)
    pack = base64.b64encode(zlib.compress(js_text.encode("utf-8"), 9)).decode("ascii")
    # UMD 尾部带 sourceMappingURL 注释，会让浏览器去请求不存在的 .map，去掉
    umd = re.sub(r"//[#@]\s*sourceMappingURL=.*$", "",
                 open(PAKO, encoding="utf-8").read(), flags=re.M)
    boot = (
        ";(function(){try{"
        "var P=" + json.dumps(pack) + ";"
        "var bin=atob(P),u=new Uint8Array(bin.length);"
        "for(var i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);"
        "window.REVIEW=JSON.parse(new TextDecoder().decode(pako.inflate(u)));"
        "}catch(e){document.title='DATAJS_ERR:'+e.message;throw e}})();"
    )
    _atomic_write(path, umd + boot)
    ver = _write_version(js_text, out_dir)
    return "zlib 压缩 %.2f MB -> %.2f MB ver=%s" % (raw_n / 1048576,
                                                    os.path.getsize(path) / 1048576,
                                                    ver)


def write_obj(obj, out_dir):
    """给只持有 dict 的维护脚本用：紧凑序列化后交给 write_data。"""
    return write_data(json.dumps(obj, ensure_ascii=False,
                                 separators=(",", ":")), out_dir)
