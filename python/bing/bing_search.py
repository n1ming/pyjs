# -*- coding: utf-8 -*-
"""
Bing 搜索核心模块：封装国内版(cn.bing.com)与国际版(www.bing.com)的
免密钥搜索客户端，支持 HTML (结构化解析) 与 RSS (format=rss) 两种数据源。

实测结论(2026-10, 大陆直连 + 海外代理):
  1. 大陆 IP 直连 www.bing.com 会被 302 强制跳转 cn.bing.com(国内版);
     国际结果需要: (a) 海外 IP/代理访问 www.bing.com, 或 (b) cn.bing.com + ensearch=1。
  2. RSS( ?format=rss ) 两版都可用, 无需登录 cookie, 链接为明文直链;
     国际版不带 mkt 时 RSS 可能返回不相关结果(实测), 因此国际版必须 mkt=xx-XX。
  3. HTML 解析: li.b_algo -> h2>a(标题+链接) / b_caption>p(摘要+日期) /
     b_attribution>cite(显示URL)。国内版链接为直链, 国际版是 bing.com/ck/a 跳转
     链接, 需按 u=a1<base64url> 解码出真实 URL。
  4. 分页: Bing 当前对匿名会话每页固定 ~10 条, count/first/NRSLT 翻页参数实测
     均会重复返回第一页(浏览器/cookie/RSS 一致), 已在 meta.note 中说明。
"""
from __future__ import annotations

import base64
import html as html_mod
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from urllib.parse import quote, urlencode, urlparse

import requests

try:
    from curl_cffi import requests as _creq  # 浏览器 TLS/HTTP2 指纹模拟(高级语法必需)
    _HAS_CURL_CFFI = True
except ImportError:
    _creq = None
    _HAS_CURL_CFFI = False

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0"
)
# UA 家族实测决定翻页能力: Firefox UA 的匿名会话走传统 snrjson XHR 翻页(可用);
# Chrome UA 匿名会话被服务端降级(first 参数永远返回第一页)。故默认 Firefox UA。
# 国际版(www.bing.com)的语法门需要 Edge 全家桶: Edge UA + sec-ch-ua 三件套 + sec-ms-gec
# (v2 token, 与 Edge 版本绑定, 从真实 Edge 复制; 时效宽松数小时以上)。三者必须配对。
EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
)



# ---------------------------------------------------------------- tools
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# 常见日期样式: 2023年11月9日 / Nov 9, 2023 / 9-11-2023 ...
DATE_RE = re.compile(
    r"(\d{4}年\d{1,2}月\d{1,2}日"
    r"|\d{1,2}月\d{1,2}日"
    r"|\d+\s*(?:天|小时|分钟|周|个月|秒)前"
    r"|[A-Z][a-z]{2,8}\s+\d{1,2},\s*\d{4}"
    r"|\d{1,2}\s+[A-Z][a-z]{2,8}\s+\d{4}"
    r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
)


def _unesc(text: str) -> str:
    return html_mod.unescape(text or "")


def _clean_html_text(fragment: str) -> str:
    # 标签删空串: 保留 <strong>Python</strong>基础 -> Python基础 无多余空格
    txt = _TAG_RE.sub("", _unesc(fragment))
    return _WS_RE.sub(" ", txt).strip()


def _decode_ck_link(url: str) -> str:
    """解码国际版 bing.com/ck/a?...u=a1<base64url> 跳转链接为真实 URL。
    非 ck 链接原样返回。"""
    if not url or "bing.com/ck/a" not in url:
        return url
    m = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", url)
    if not m:
        return url
    b64 = m.group(1)
    b64 += "=" * (-len(b64) % 4)
    try:
        real = base64.urlsafe_b64decode(b64).decode("utf-8", errors="replace")
        return real or url
    except Exception:
        return url


def _strip_date(snippet: str):
    """从摘要头部提取日期并剥离, 常见格式 '2023年11月9日 · xxx'"""
    m = DATE_RE.search(snippet)
    if not m:
        return snippet, None
    date = m.group(1)
    rest = (snippet[: m.start()] + snippet[m.end():]).strip()
    rest = re.sub(r"^[\s·•」，,]+", "", rest)
    return rest or snippet, date


@dataclass
class BingResult:
    rank: int
    title: str
    url: str                       # 一律还原后的真实 URL
    raw_link: str = ""             # Bing 原始 href(可能是 ck 跳转链接)
    snippet: str = ""
    display_url: str = ""          # b_attribution cite
    source: str = ""               # 域名(展示层用)
    date: Optional[str] = None
    pub_date: Optional[str] = None # 仅 RSS 源时存在
    engine: str = ""               # cn / global
    protocol: str = ""             # html / rss

    def as_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- client
class BingSearchError(Exception):
    pass


class BingSearch:
    """Bing 搜索客户端 (国内版/国际版)。

    用法:
        bs = BingSearch("cn")                       # 国内版(直连即可)
        bs = BingSearch("global")   # 代理读环境变量 BING_PROXY
        results = bs.search("python 教程", count=10)
    """

    CN = "cn"
    GLOBAL = "global"

    def __init__(
        self,
        engine: str = CN,
        proxy: Optional[str] = None,
        timeout: int = 25,
        mkt: Optional[str] = None,
        language: Optional[str] = None,
        auto_bootstrap: bool = True,
        verify_proxy: bool = True,
        cookies: Optional[str] = None,
        gec_token: Optional[str] = None,
    ):
        self.engine = (engine or self.CN).lower()
        if self.engine not in (self.CN, self.GLOBAL):
            raise ValueError("engine 必须是 'cn' 或 'global'")
        self.timeout = timeout
        # 国内版默认直连即可; 国际版默认读环境变量 BING_PROXY(海外出口代理)
        self.proxy = proxy if proxy is not None else (
            (os.environ.get("BING_PROXY") or None) if self.engine == self.GLOBAL else None
        )
        if not self.proxy and self.engine == self.GLOBAL and verify_proxy:
            pass  # 允许海外机器直连, 由 _bootstrap 时自然检测
        self._mkt = mkt                      # None => engine 默认
        self._gec_token = gec_token or os.environ.get("BING_GEC_TOKEN")  # 国际版语法门
        self._language = language            # Accept-Language
        self._bootstrap_done = auto_bootstrap is False  # 惰性
        if _HAS_CURL_CFFI:
            # 浏览器指纹会话: 高级语法(site: 等)仅对浏览器 TLS/HTTP2 指纹的会话生效
            self.session = _creq.Session(impersonate="firefox133")
        else:
            self.session = requests.Session()
            self.session.headers.update(self._build_headers())
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
        if _HAS_CURL_CFFI:
            self.session.headers.update(self._build_headers())
        if cookies:
            self.set_cookies(cookies)

    # -------------------------------------------------- cookie inject
    def set_cookies(self, cookies) -> None:
        """注入 Cookie (用于登录态翻页实验/复用浏览器会话)。

        支持三种形式:
          - dict
          - "K1=V1; K2=V2" 字符串 (浏览器 F12 -> Application -> Cookies 复制)
          - "@/path/cookies.txt" 文件路径 (文件内容为上述字符串, 或 Netscape cookie 导出)
        """
        if isinstance(cookies, str):
            raw = cookies
            if raw.startswith("@"):
                import io as _io
                raw = _io.open(raw[1:], encoding="utf-8").read().strip()
            if raw.startswith("#") or "\t" in raw:
                # Netscape cookies.txt 格式
                jar = {}
                for line in raw.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        jar[parts[5]] = parts[6]
                cookies = jar
            else:
                jar = {}
                for part in raw.split(";"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        k = k.strip()
                        if k:
                            jar[k] = v.strip()
                cookies = jar
        self.session.cookies.update(cookies)

    # -------------------------------------------------- config helpers
    def _base(self) -> str:
        return "https://cn.bing.com" if self.engine == self.CN else "https://www.bing.com"

    def _default_mkt(self) -> str:
        return "zh-CN" if self.engine == self.CN else "en-US"

    def _accept_language(self) -> str:
        if self._language:
            return self._language
        return "zh-CN,zh;q=0.9" if self.engine == self.CN else "en-US,en;q=0.9"

    def _build_headers(self) -> Dict[str, str]:
        h = {
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self._accept_language(),
            # 不自定义 Accept-Encoding: requests 按已装压缩库自动协商,
            # 若强行声明 br 而本地无 brotli 库, 响应将变成未解压乱码
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        # 国际版(www.bing.com)有 Akamai/语法门: sec-ch-ua 三件套 + sec-ms-gec 必需,
        # 缺任一组高级语法(site: 等)会被服务端静默丢弃。token 时效宽松(数小时+)。
        if self.engine == self.GLOBAL:
            # Edge client hints 必须与 Edge UA 配对(服务端校验一致性)
            h["User-Agent"] = EDGE_UA
            h["sec-ch-ua"] = '"Microsoft Edge";v="155", "Chromium";v="155", "Not(A:Brand";v="24"'
            h["sec-ch-ua-mobile"] = "?0"
            h["sec-ch-ua-platform"] = '"Windows"'
            gec = os.environ.get("BING_GEC_TOKEN") or self._gec_token
            if gec:
                h["sec-ms-gec"] = gec
                h["sec-ms-gec-version"] = "2-155.0.4283.62"
        return h

    # -------------------------------------------------- session warmup
    def bootstrap(self, force: bool = False) -> None:
        """访问搜索首页一次, 获取 MUID/_EDGE_S/SRCH* 等会话 cookie。
        对 RSS 与 HTML 的首屏稳定性有帮助(实测 cn 版无 cookie 也能出结果)。"""
        if self._bootstrap_done and not force:
            return
        try:
            if self.engine == self.CN:
                self.session.get(f"{self._base()}/", timeout=self.timeout)
            else:
                # 国际版直接探测 search(验证代理可达 / 未被强制 302)
                r = self.session.get(
                    f"{self._base()}/search?q=hello&format=rss&count=1",
                    timeout=self.timeout,
                )
                # 若被 302 到 cn.bing.com => 当前出口是大陆 IP(代理未生效)
                if urlparse(r.url).hostname and "cn.bing.com" in str(r.url):
                    raise BingSearchError(
                        "国际版出口 IP 被判为大陆(www.bing.com 302 -> cn.bing.com), "
                        "需要代理(海外出口)后才能获取国际结果"
                    )
            self._bootstrap_done = True
        except requests.RequestException as e:
            raise BingSearchError(f"会话引导失败: {e}") from e

    # -------------------------------------------------- search core
    def search(
        self,
        query: str,
        count: int = 10,
        protocol: str = "html",
        ensearch: Optional[bool] = None,   # cn 版强制国际结果开关
        safe_search: Optional[str] = None, # "off"/"moderate"/"strict"
        first: Optional[int] = None,       # 保留翻页语义, 见 meta.note
    ) -> Dict:
        """
        返回:
            {
              "query", "engine", "protocol", "count", "total_count"(HTML 页面顶栏统计),
              "results": [BingResult...], "meta": {...}
            }
        """
        if not query or not query.strip():
            raise ValueError("query 不能为空")
        query = query.strip()
        protocol = protocol.lower()
        if protocol not in ("html", "rss"):
            raise ValueError("protocol 必须是 html 或 rss")

        params: Dict[str, str] = {"q": query}
        mkt = self._mkt or self._default_mkt()
        params["mkt"] = mkt
        if self._language:
            pass
        if self.engine == self.GLOBAL:
            # 国际版 RSS/HTML 实测必须显式 setlang/max 或 mkt 才稳定返回对应市场
            params["setlang"] = mkt.split("-")[0]
        if self.engine == self.CN and ensearch:
            params["ensearch"] = "1"
            params.pop("mkt", None)
        if safe_search:
            params["safeSearch"] = safe_search
        if protocol == "rss":
            params["format"] = "rss"
        count = max(1, min(count, 50))
        params["count"] = str(count)
        params["first"] = str(first if first is not None else 1)

        self.bootstrap()

        # 高级语法(site:/引号短语/intitle: 等)要求会话已"激活"(SRCHHPGUSR 带
        # HV/HVE=本会话 Salt), 否则 SERP 是降级池——语法被忽略、结果为通用池。
        # 普通查询无需激活(省 2 个请求); 翻页通道(snrjson)会自激活但会丢语法。
        if first and first > 1 and protocol == "html":
            pass  # snrjson 分支内部自带激活
        elif protocol == "html" and self._has_syntax(query):
            t0 = time.time()
            act = self._activate_and_search(query, mkt, ensearch)
            if act is not None and act[0]:
                results, total = act
                if self.engine == self.GLOBAL:
                    results = self._global_fix_links(results)
                for r_ in results:
                    r_.snippet, r_.date = _strip_date(r_.snippet)
                    r_.source = urlparse(r_.url).hostname or ""
                if len(results) > count:
                    results = results[:count]
                took = round((time.time() - t0) * 1000)
                return {
                    "query": query, "engine": self.engine, "protocol": "html",
                    "count": len(results), "total_count": total, "results": results,
                    "meta": {"took_ms": took, "mkt": mkt,
                             "ensearch": bool(ensearch) and self.engine == self.CN,
                             "channel": "html-activated",
                             "note": "语法查询走激活通道(首搜取 Salt -> 拼 HV/HVE -> rdr 跳转后解析激活 SERP)"},
                }
            # 激活通道空结果/失败: Bing 对语法处理随时间窗漂移, 换 RSS 通道重试
            # (实测同一时刻 html 0 条时 rss 仍能正确命中语法, 如 intitle:)
            try:
                rp = dict(params)
                rp["format"] = "rss"
                rp["first"] = "1"
                rp.pop("count", None)
                rr = self._request(f"{self._base()}/search?{urlencode(rp)}", protocol="rss")
                results, total = self._parse_rss(rr.text, engine=self.engine, ensearch=ensearch)
                if results:
                    if self.engine == self.GLOBAL:
                        results = self._global_fix_links(results)
                    for r_ in results:
                        r_.snippet, r_.date = _strip_date(r_.snippet)
                        r_.source = urlparse(r_.url).hostname or ""
                    if len(results) > count:
                        results = results[:count]
                    took = round((time.time() - t0) * 1000)
                    return {
                        "query": query, "engine": self.engine, "protocol": "rss",
                        "count": len(results), "total_count": total, "results": results,
                        "meta": {"took_ms": took, "mkt": mkt,
                                 "ensearch": bool(ensearch) and self.engine == self.CN,
                                 "channel": "rss-fallback",
                                 "note": "HTML 激活通道空结果 -> 自动回退 RSS 通道(Bing 语法处理随时间窗/通道漂移)"},
                    }
            except (BingSearchError, requests.RequestException):
                pass
            # 都失败则落入普通 document 通道(可能也空)

        url = f"{self._base()}/search?{urlencode(params)}"
        t0 = time.time()

        # page>=2: Bing 2026 真实翻页通道是 XHR snrjson 端点(浏览器会话实验证实):
        #   /search?...&first=N&FORM=PERE&ajaxnorecss=1&sid=<_SS里的SID>&format=snrjson&jsoncbid=0
        # 带 sid -> 200 返回第 N 页; 不带 sid -> 403; 同 URL 走 document 请求 -> 永远第一页
        if first and first > 1 and protocol == "html":
            results, total, note = self._page_via_snrjson(query, first, ensearch)
            took = round((time.time() - t0) * 1000)
            if self.engine == self.GLOBAL:
                results = self._global_fix_links(results)
            for r_ in results:
                r_.snippet, r_.date = _strip_date(r_.snippet)
                r_.source = urlparse(r_.url).hostname or ""
            if len(results) > count:
                results = results[:count]
            return {
                "query": query, "engine": self.engine, "protocol": "html",
                "count": len(results), "total_count": total, "results": results,
                "meta": {"took_ms": took, "mkt": mkt,
                         "ensearch": bool(ensearch) and self.engine == self.CN,
                         "request_url": url, "first_param": str(first),
                         "channel": "snrjson-xhr", "note": note},
            }

        resp = self._request(url, protocol=protocol)
        took = round((time.time() - t0) * 1000)

        if protocol == "rss":
            results, total = self._parse_rss(resp.text, engine=self.engine, ensearch=ensearch)
        else:
            results, total = self._parse_html(resp.text, engine=self.engine, ensearch=ensearch)
        # Bing 每页固定 ~10 条, 本地按 count 截断以保证接口语义一致
        if len(results) > count:
            results = results[:count]

        if self.engine == self.GLOBAL:
            results = self._global_fix_links(results)

        for r in results:
            r.snippet, r.date = _strip_date(r.snippet)
            r.source = urlparse(r.url).hostname or ""

        return {
            "query": query,
            "engine": self.engine,
            "protocol": protocol,
            "count": len(results),
            "total_count": total,
            "results": results,
            "meta": {
                "took_ms": took,
                "mkt": mkt,
                "ensearch": bool(ensearch) and self.engine == self.CN,
                "request_url": url,
                "first_param": params["first"],
                "channel": "html",
                "note": "page=1 走标准 HTML; page>=2 自动走 snrjson XHR 通道(需会话 _SS cookie, 参见 _page_via_snrjson)",
            },
        }

    # -------------------------------------------------- session activation
    _SYNTAX_RE = re.compile(
        r'(site:|filetype:|ext:|contains:|feed:|related:|ip:|location:'
        r'|language:|prefer:|inbody:|intitle:|intext:|")', re.I)

    @staticmethod
    def _has_syntax(query: str) -> bool:
        """查询是否使用了高级语法(需要 HVE 激活的会话才能正确执行)"""
        return bool(BingSearch._SYNTAX_RE.search(query))

    def _activate_and_search(self, query: str, mkt: str, ensearch: Optional[bool] = None):
        """语法查询专用: 激活会话并返回激活后 SERP 的解析结果。

        流程(= 复刻前端 BD 模块 + 激活渲染):
          1. GET form=QBLH -> 拿 _G.Salt(=HVE) 与 IG(=rdig), 接收服务端
             Set-Cookie 下发的 SRCHHPGUSR(含 V= blob)
          2. 拼 SRCHHPGUSR += &HV=<unix秒>&HVE=<Salt>   (Salt 必须是本会话
             自己渲染的, 跨会话搬运会被拒)
          3. GET form=QBLH&rdr=1&rdrig=<IG>  -> 此响应即"激活后第一页 SERP",
             直接解析返回。激活前 SERP 是降级池(sb_count~44,700, site: 被忽略),
             激活后为真实索引(site: 9/9 命中, sb_count 降至真实总数)。
        激活失败(Salt/IG 缺失)返回 None, 调用方回退普通通道。
        """
        q = quote(query)
        base = self._base()
        mkt_q = f"&mkt={mkt}&setlang={mkt.split('-')[0]}" if self.engine == self.GLOBAL else ""
        try:
            r1 = self.session.get(
                f"{base}/search?q={q}&form=QBLH{mkt_q}",
                headers=self._page_headers(f"{base}/"),
                timeout=self.timeout,
            )
            html1 = r1.text
            msalt = re.search(r'Salt:\\?"(CfDJ8[^"\\]{100,})', html1)
            mig = (re.search(r'IG:"?([0-9A-F]{32})', html1)
                   or re.search(r'[?&]IG=([0-9A-F]{32})', html1)
                   or re.search(r'IG=([0-9A-F]{32})', html1))
            if not (msalt and mig):
                return None
            salt, ig = msalt.group(1), mig.group(1)
            cur = self.session.cookies.get("SRCHHPGUSR") or ""
            if "HVE=" not in cur:
                newval = (f"{cur}&HV={int(time.time())}&HVE={salt}" if cur else
                          f"SRCHLANG=zh-Hans&HV={int(time.time())}&HVE={salt}")
                self.session.cookies.set("SRCHHPGUSR", newval, domain=".bing.com", path="/")
            r2 = self.session.get(
                f"{base}/search?q={q}&form=QBLH{mkt_q}&rdr=1&rdrig={ig}",
                headers=self._page_headers(f"{base}/search?q={q}&form=QBLH"),
                timeout=self.timeout,
            )
            results, total = self._parse_html(r2.text, engine=self.engine, ensearch=ensearch)
            return results, total
        except requests.RequestException:
            return None

    # -------------------------------------------------- pagination via XHR
    _JSON_UNESC = (
        ("\\/", "/"),
        ('\\"', '"'),
    )

    def _page_via_snrjson(self, query: str, first: int, ensearch: Optional[bool] = None):
        """page>=2 的真实翻页: 纯 HTTP 复刻 Bing 前端"点击第2页"的完整链路。

        机制(frx 浏览器会话实验 + 纯 HTTP 复现证实):
          1. SERP HTML 里服务端下发 _G.Salt(CfDJ8 blob) 与 IG(=rdig)
          2. 前端 BD 模块把 HV=<unix秒>&HVE=<Salt> 写进 SRCHHPGUSR cookie 并
             以 rdr=1&rdrig=<IG> 自跳转 —— 缺这步服务端永远回第一页
          3. 真正的第 N 页是 XHR: /search?...&first=N&FORM=PERE&ajaxnorecss=1
             &sid=<_SS里的SID>&format=snrjson&jsoncbid=0  (缺 sid -> 403)
          4. 响应是多段 window.parent.AjaxCB[0]({...}), 结果 HTML 在某段的
             Content 字段里(转义形态), 反转义后按 b_algo 解析
        """
        q = quote(query)
        base = self._base()
        # 国际版必须显式 mkt/setlang(实测不带会返回不相关结果); cn 版默认 zh-CN
        mkt = self._mkt or self._default_mkt()
        mkt_q = f"&mkt={mkt}&setlang={mkt.split('-')[0]}"

        # 1) 首搜(搜索框形态): 拿 Salt / IG / FPIG / 服务端 SRCHHPGUSR(含 V= blob)
        r1 = self.session.get(
            f"{base}/search?q={q}&form=QBLH{mkt_q}",
            headers=self._page_headers(f"{base}/"),
            timeout=self.timeout,
        )
        html1 = r1.text
        m = re.search(r'Salt:\\?"(CfDJ8[^"\\]{100,})', html1)
        salt = m.group(1) if m else ""
        mig = (re.search(r'IG:"?([0-9A-F]{32})', html1)
               or re.search(r'[?&]IG=([0-9A-F]{32})', html1)
               or re.search(r'IG=([0-9A-F]{32})', html1))
        ig = mig.group(1) if mig else ""
        mcvid = re.search(r'cvid=([0-9A-F]{32})', r1.url)
        cvid = mcvid.group(1) if mcvid else uuid.uuid4().hex.upper()
        mfpig = re.search(r'FPIG=([0-9A-F]{32})', html1)
        fpig = mfpig.group(1) if mfpig else ig

        # 2) 复刻 BD 模块: SRCHHPGUSR += &HV=<unix秒>&HVE=<Salt>
        ss = self.session.cookies.get("_SS") or ""
        msid = re.search(r'SID=([0-9A-F]+)', ss)
        sid = msid.group(1) if msid else ""
        if not (salt and ig and sid):
            raise BingSearchError(
                f"snrjson 翻页要素缺失: salt={'Y' if salt else 'N'} ig={'Y' if ig else 'N'} sid={'Y' if sid else 'N'}")
        cur = self.session.cookies.get("SRCHHPGUSR") or ""
        if "HVE=" not in cur:
            newval = f"{cur}&HV={int(time.time())}&HVE={salt}" if cur else \
                     f"SRCHLANG=zh-Hans&HV={int(time.time())}&HVE={salt}"
            self.session.cookies.set("SRCHHPGUSR", newval, domain=".bing.com", path="/")

        # 3) rdr 自跳转(JS 的 location.href += '&rdr=1&rdrig=<IG>')
        self.session.get(
            f"{base}/search?q={q}&form=QBLH{mkt_q}&rdr=1&rdrig={ig}",
            headers=self._page_headers(f"{base}/search?q={q}&form=QBLH"),
            timeout=self.timeout,
        )

        # 4) XHR snrjson 翻页
        u = (f"{base}/search?q={q}&sp=-1&lq=0&pq={q}&sc=0-0&qs=n&sk=&cvid={cvid}"
             f"&rdr=1&rdrig={ig}&FPIG={fpig}&first={first}&FORM=PERE{mkt_q}"
             f"&ajaxnorecss=1&sid={sid}&format=snrjson&jsoncbid=0")
        r = self.session.get(u, headers=self._page_headers(
            f"{base}/search?q={q}&form=QBLH&cvid={cvid}&rdr=1&rdrig={ig}"), timeout=self.timeout)
        body = r.text
        for a, b in self._JSON_UNESC:
            body = body.replace(a, b)

        algos = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', body, re.S)
        results: List[BingResult] = []
        rank = 0
        for b in algos:
            mm = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
            if not mm:
                continue
            raw = _unesc(mm.group(1))
            title = _clean_html_text(mm.group(2))
            if not title:
                continue
            rank += 1
            msnip = re.search(r'b_caption[^>]*>\s*<p[^>]*>(.*?)</p>', b, re.S)
            snippet = _clean_html_text(msnip.group(1)) if msnip else ""
            mcite = re.search(r"<cite[^>]*>([^<]+)</cite>", b)
            results.append(BingResult(
                rank=rank, title=title, url=_decode_ck_link(raw), raw_link=raw,
                snippet=snippet,
                display_url=_clean_html_text(mcite.group(1)) if mcite else "",
                engine=self.engine, protocol="html",
            ))
        mtot = re.search(r"(\d[\d,]*)\s*(?:条结果|results)", body)
        note = (f"page>=2 走 snrjson XHR 通道(本会话 Salt/HV/HVE 会话绑定, "
                f"first={first}); 每次翻页前都会重新渲染取 Salt, 勿缓存会话间复用")
        return results, (mtot.group(1) if mtot else None), note

    def _page_headers(self, referer: str) -> Dict[str, str]:
        h = dict(self.session.headers)
        h.update({
            "Referer": referer,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        })
        return h

    # -------------------------------------------------- network
    def _request(self, url: str, protocol: str = "html") -> requests.Response:
        for attempt in range(2):
            try:
                resp = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    headers={
                        "Referer": f"{self._base()}/",
                    },
                )
                if resp.status_code == 200:
                    # 编码兜底: Bing 始终 utf-8
                    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1",):
                        resp.encoding = "utf-8"
                    return resp
                if resp.status_code in (403, 429):
                    time.sleep(1 + attempt)
                    continue
                raise BingSearchError(f"Bing 返回 {resp.status_code}: {resp.reason}")
            except requests.RequestException as e:
                if attempt == 1:
                    raise BingSearchError(f"请求失败: {e}") from e
                time.sleep(1)
        raise BingSearchError("请求重试仍失败")

    # -------------------------------------------------- parsing: HTML
    def _parse_html(
        self, html: str, engine: str, ensearch: Optional[bool] = None
    ):
        blocks = _split_algo_blocks(html)
        total = self._extract_total(html)
        results: List[BingResult] = []
        rank = 0
        for b in blocks:
            m = re.search(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", b, re.S)
            if not m:
                continue
            raw = _unesc(m.group(1))
            title = _clean_html_text(m.group(2))
            if not title:
                continue
            rank += 1
            # 摘要: 优先 b_caption>p, 次选 p.*?
            msnip = re.search(r'b_caption[^>]*>\s*<p[^>]*>(.*?)</p>', b, re.S) \
                    or re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
            snippet = _clean_html_text(msnip.group(1)) if msnip else ""
            mdate = re.search(r'<span class="news_dt"[^>]*>([^<]+)</span>', b)
            date = _clean_html_text(mdate.group(1)) if mdate else None
            mcite = re.search(r"<cite[^>]*>([^<]+)</cite>", b)
            display_url = _clean_html_text(mcite.group(1)) if mcite else ""
            display_url = display_url.replace(" › ", "/").replace("›", "/")
            results.append(
                BingResult(
                    rank=rank,
                    title=title,
                    url=_decode_ck_link(raw),
                    raw_link=raw,
                    snippet=snippet,
                    display_url=display_url,
                    date=date,
                    engine=engine,
                    protocol="html",
                )
            )
        # en 结果: 若块内是 ck 链接则已解码
        return results, total

    @staticmethod
    def _extract_total(html: str) -> Optional[str]:
        m = re.search(r'(\d[\d,\s]*)\s*(?:条结果|results)', html)
        if m:
            return m.group(1).replace(" ", "")
        return None

    # -------------------------------------------------- parsing: RSS
    def _parse_rss(self, xml_text: str, engine: str, ensearch: Optional[bool] = None):
        try:
            root = ET.fromstring(_lead_strip(xml_text))
        except ET.ParseError as e:
            raise BingSearchError(f"RSS 解析失败: {e}; 前200字符: {xml_text[:200]!r}") from e
        ch = root.find("channel")
        if ch is None:
            return [], None
        results: List[BingResult] = []
        for idx, item in enumerate(ch.findall("item"), 1):
            title = _clean_html_text((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            desc = _clean_html_text(item.findtext("description") or "")
            pub = (item.findtext("pubDate") or "").strip() or None
            # RSS 的 description 通常直接与 title 重复一部分; 保留完整摘要
            results.append(
                BingResult(
                    rank=idx,
                    title=title or link,
                    url=_decode_ck_link(link),
                    raw_link=link,
                    snippet=desc,
                    pub_date=pub,
                    engine=engine,
                    protocol="rss",
                )
            )
        return results, None

    # -------------------------------------------------- global helper
    def _global_fix_links(self, results: List[BingResult]) -> List[BingResult]:
        for r in results:
            if "bing.com" in urlparse(r.url).netloc:
                dec = _decode_ck_link(r.url)
                if "bing.com/ck/a" not in dec:
                    r.url = dec
                else:
                    r.url = ""  # 无法解码的 ck 链
        return results


def _lead_strip(text: str) -> str:
    # 去掉 XML 前 BOM / 前导空白
    return text.lstrip("\ufeff\r\n\t ")


def _split_algo_blocks(html: str) -> List[str]:
    # 更宽松的块切分: 以 <li class="b_algo" 开头, 直到下一个 b_algo / b_ / </ol>
    parts = re.split(r'(?=<li class="b_?[^"]*(?:b_)?")', html)
    out = []
    for p in parts:
        if p.startswith('<li class="b_algo"') or p.startswith('<li class="b_algo '):
            out.append(p)
    return out


class BingMultiEngine:
    """同时查询两版并对比(独立会话, 各自 cookie/proxy)。"""

    def __init__(self, cn_proxy=None, global_proxy=None):
        self.clients = {
            "cn": BingSearch("cn", proxy=cn_proxy),
            "global": BingSearch("global", proxy=global_proxy),
        }

    def compare(self, query: str, count: int = 10, protocol: str = "html") -> Dict:
        out = {}
        for name, client in self.clients.items():
            res = client.search(query, count=count, protocol=protocol)
            out[name] = {
                "count": res["count"],
                "total_count": res["total_count"],
                "meta": res["meta"],
                "results": [r.as_dict() for r in res["results"]],
            }
        cn_urls = {r["url"] for r in out["cn"]["results"]}
        gl_urls = {r["url"] for r in out["global"]["results"]}
        out["overlap"] = sorted(cn_urls & gl_urls)
        out["cn_only"] = len(cn_urls - gl_urls)
        out["global_only"] = len(gl_urls - cn_urls)
        return out


# ---------------------------------------------------------------- CLI
def _cli():
    import argparse, json, sys
    p = argparse.ArgumentParser("bing_search CLI")
    p.add_argument("query")
    p.add_argument("--engine", default="cn", choices=["cn", "global"])
    p.add_argument("--protocol", default="html", choices=["html", "rss"])
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--ensearch", action="store_true", help="国内版强制国际结果")
    p.add_argument("--proxy", default=None)
    p.add_argument("--cookie", default="", help="Cookie 注入: 'K1=V1; K2=V2' 或 @cookies.txt 文件")
    p.add_argument("--json", action="store_true", help="纯 JSON 输出")
    p.add_argument("--dump", default="", help="把 JSON 存到这个文件")
    a = p.parse_args()
    bs = BingSearch(a.engine, proxy=a.proxy, cookies=(a.cookie or None))
    data = bs.search(a.query, count=a.count, protocol=a.protocol, ensearch=a.ensearch)
    payload = {
        **{k: v for k, v in data.items() if k not in ("results",)},
        "results": [r.as_dict() if isinstance(r, BingResult) else r for r in data["results"]],
    }
    if a.dump:
        import io
        with io.open(a.dump, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    if a.json or a.dump:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    else:
        print(f"[{a.engine}] {a.query} -> {payload['count']} 条  ({payload['meta']['took_ms']}ms)")
        for r in payload["results"]:
            print(f"{r['rank']:>2}. {r['title'][:48]}")
            print(f"     {r['url'][:80]}")
            print(f"     {r['snippet'][:90]}")
            if r.get("date"):
                print(f"     [date] {r['date']} | [source] {r['source']}")
            print()


if __name__ == "__main__":
    _cli()
