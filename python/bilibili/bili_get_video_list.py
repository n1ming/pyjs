#!/usr/bin/env python3

import hashlib
import json
import logging
import math
import random
import re
import sys
import time
import base64
import urllib.parse
from typing import Optional, Tuple
import requests

logger = logging.getLogger("bili_space_api")

# ============ 配置常量 ============

# WebGL 指纹 (macOS Edge/Chrome 默认值)
WEBGL = {
    "renderer": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
    "unmaskedVendor": "Google Inc. (Apple)",
    "unmaskedRenderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
}

DC = {"A": 2, "B": 3, "C": 4}

# WBI mixin key 索引表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
    ),
    "Origin": "https://space.bilibili.com",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
}

# ============ 工具函数 ============

def _get_mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _bili_encode_strip(s: str) -> str:
    """strip 版 base64: 去掉末尾 2 字符"""
    return base64.b64encode(s.encode("utf-8")).decode()[:-2]


def _enc_wbi_params(params: dict, mixin_key: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    对齐 wbi.js T$e:
    - 对 string value 过滤 !'()*
    - encodeURIComponent 编码 key 和 value
    - 按 key 字母序排序
    返回 (query_string, w_rid) — w_rid 为 None 时不签名
    """
    parts = []
    for k in sorted(params):
        v = params[k]
        if isinstance(v, str):
            v = re.sub(r"[!'()*]", '', v)
        if v is not None:
            parts.append(urllib.parse.quote(k, safe='') + '=' + urllib.parse.quote(str(v), safe=''))
    query = '&'.join(parts)
    if mixin_key:
        return query, _md5(query + mixin_key)
    return query, None


# ============ dm_* 设备指纹 ============

def _transform_wh(w: int, h: int, a: int, b: int, c: int) -> list:
    z = math.floor(0x72 * random.random())
    return [int(a * w + a * h + b * z), int(c * w - h + z), z]


def _transform_of(sl: int, st: int, a: int, b: int, c: int) -> list:
    z = math.floor(0x202 * random.random())
    return [int(b * sl + a * st + z), int(c * sl - c * st + a * z), z]


def _gen_dm_img_inter(win_w=1432, win_h=250, scroll_l=0, scroll_t=0,
                      a=DC["A"], b=DC["B"], c=DC["C"]) -> dict:
    wh = _transform_wh(win_w, win_h, a, b, c)
    of = _transform_of(scroll_l, scroll_t, a, b, c)
    return {"ds": [], "wh": wh, "of": of}


def _generate_dm_params(renderer=None, unmasked_vendor=None, unmasked_renderer=None,
                        win_w=1432, win_h=250) -> dict:
    renderer = renderer or WEBGL["renderer"]
    unmasked_vendor = unmasked_vendor or WEBGL["unmaskedVendor"]
    unmasked_renderer = unmasked_renderer or WEBGL["unmaskedRenderer"]

    combined = unmasked_renderer + unmasked_vendor
    inter = _gen_dm_img_inter(win_w, win_h)

    return {
        "dm_img_list": "[]",
        "dm_img_str": _bili_encode_strip(renderer),
        "dm_cover_img_str": _bili_encode_strip(combined),
        "dm_img_inter": json.dumps(inter, separators=(",", ":")),
    }


# ============ 核心类 ============

class BiliSpaceAPI:
    """B站空间视频列表 API 封装

    Usage:
        api = BiliSpaceAPI(cookie="你的Cookie")
        data = api.get_videos("456664753", pn=1)
    """

    def __init__(self):
        self.cookie = ''
        self.check_and_auto_get_cookie()

    # ---- 内部 ----

    def check_and_auto_get_cookie(self):
        """纯协议更新cookie（无浏览器依赖）：
        - finger/spi 接口获取 buvid3 / buvid4
        - 首页 Set-Cookie 补充 b_nut（buvid3 以 spi 下发的为准，
          首页直接下发的 buvid3 未经过指纹激活，实测会被 412 风控）
        """
        logging.info("正在通过 spi 接口纯协议获取cookie...")
        try:
            s = requests.Session()
            s.headers.update({"User-Agent": HEADERS["User-Agent"]})
            spi = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=15).json()
            if spi.get("code") != 0:
                raise RuntimeError(f"spi 接口返回异常: {spi}")
            buvid3 = spi["data"]["b_3"]
            buvid4 = spi["data"]["b_4"]

            b_nut = ""
            try:
                home = s.get("https://www.bilibili.com/", timeout=15)
                b_nut = home.cookies.get("b_nut", "")
            except Exception as e:
                logging.warning("获取 b_nut 失败(可忽略): %s", e)

            parts = [f"buvid3={buvid3}", f"buvid4={buvid4}"]
            if b_nut:
                parts.append(f"b_nut={b_nut}")
            self.cookie = "; ".join(parts)
            logging.info("cookie更新为：%s", self.cookie)
        except Exception as e:
            logging.error(e)
            logging.warning("纯协议获取失败，请手动更新")
            self.cookie = input("请输入cookie:").strip()


    def _get_wbi_keys(self) -> Tuple[str, str]:
        """从 nav 接口获取 img_key / sub_key"""
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={
                "Cookie": self.cookie,
                "User-Agent": HEADERS["User-Agent"],
                "Referer": "https://www.bilibili.com/",
            },
            timeout=15,
        )
        data = resp.json()
        wbi = data.get("data", {}).get("wbi_img", {})
        img_url = wbi.get("img_url", "")
        sub_url = wbi.get("sub_url", "")
        if not img_url or not sub_url:
            raise RuntimeError(f"无法获取 wbi_img: {data}")
        return (
            img_url.split("/")[-1].split(".")[0],
            sub_url.split("/")[-1].split(".")[0],
        )

    def _build_and_sign(self, mid: str, pn: int) -> str:
        """构建参数、签名，返回完整 URL"""
        img_key, sub_key = self._get_wbi_keys()
        mixin_key = _get_mixin_key(img_key + sub_key)

        dm = _generate_dm_params()
        params = {
            "pn": str(pn),
            "ps": "40",
            "tid": "0",
            "special_type": "",
            "order": "pubdate",
            "mid": str(mid),
            "index": "0",
            "keyword": "",
            "order_avoided": "true",
            "platform": "web",
            "web_location": "333.1387",
            **dm,
            "wts": str(int(time.time())),
        }

        sorted_query, w_rid = _enc_wbi_params(params, mixin_key)
        return f"https://api.bilibili.com/x/space/wbi/arc/search?{sorted_query}&w_rid={w_rid}"

    def _request_once(self, mid: str, pn: int) -> dict:
        """单次请求"""
        url = self._build_and_sign(mid, pn)

        headers = {**HEADERS, "Cookie": self.cookie}
        headers.setdefault("Referer", f"https://space.bilibili.com/{mid}/upload/video")

        resp = requests.get(url, headers=headers, timeout=15)

        try:
            return resp.json()
        except json.JSONDecodeError:
            resp.encoding = "utf-8"
            raise RuntimeError(f"非 JSON 响应: {resp.text[:300]}")

    # ---- 公开接口 ----

    def get_videos(self, mid: str, pn: int = 1, max_retries: int = 100) -> dict:
        """获取空间视频"""
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                data = self._request_once(mid, pn)
                code = data.get("code")

                if code == 0:
                    data["attempt"] = attempt
                    return data

                last_error = f"code={code} message={data.get('message')}"
                logger.warning("attempt %d/%d: %s", attempt, max_retries, last_error)

            except Exception as e:
                last_error = str(e)
                logger.warning("attempt %d/%d: %s", attempt, max_retries, last_error)

            if attempt < max_retries:
                time.sleep(1.0)

        raise RuntimeError(f"重试 {max_retries} 次后仍失败: {last_error}")


# ============ 便捷函数 ============

def fetch_space_videos(mid: str, pn: int = 1,
                       max_retries: int = 100) -> dict:

    api = BiliSpaceAPI()
    return api.get_videos(mid, pn, max_retries)


# ============ CLI ============

if __name__ == "__main__":
    import os  # noqa: E402 (已在顶部 import)

    logging.basicConfig(level=logging.INFO)

    mid = "385670211"
    pn = 1

    try:
        result = fetch_space_videos(mid=mid, pn=pn)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
