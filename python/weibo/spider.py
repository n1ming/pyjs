#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博访客登录 + 用户数据抓取（简洁可运行版）
依赖: pip install requests pycryptodome
"""

import requests
from Crypto.PublicKey import RSA
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.Util.Padding import pad
import base64
import os
import json


# ================= 加密函数（直接内置，无需额外导入） =================
def hybrid_encrypt_js_style(plaintext: str, rsa_pub_key_bytes: bytes) -> str:
    """混合加密：AES-128-CBC + RSA-OAEP-SHA256"""
    # 1. 生成 AES-128 密钥 & IV
    aes_key = os.urandom(16)
    aes_iv = os.urandom(16)

    # 2. RSA-OAEP 加密 (Key + IV)
    rsa_key = RSA.import_key(rsa_pub_key_bytes)
    cipher_rsa = PKCS1_OAEP.new(rsa_key, hashAlgo=SHA256)
    rsa_ciphertext = cipher_rsa.encrypt(aes_key + aes_iv)

    # 3. AES-CBC 加密数据
    cipher_aes = AES.new(aes_key, AES.MODE_CBC, aes_iv)
    plaintext_bytes = plaintext.encode('utf-8')
    aes_ciphertext = cipher_aes.encrypt(pad(plaintext_bytes, AES.block_size))

    # 4. 拼接并整体 Base64 编码（对齐 JS 逻辑）
    combined = b"01" + rsa_ciphertext + b"02" + aes_ciphertext
    b64_combined = base64.b64encode(combined).decode('ascii')

    return "01" + b64_combined


# ================= RSA 公钥（从 JS x 数组还原） =================
RSA_PUB_KEY = bytes([
    1849 + -1801 * 1, 1251 + 34 * -33, 4 * -1973 + 5178 + -17 * -169,
    -2951 * -3 + -2888 * 1 + -97 * 61, 13, -1885 + -1891 * -1,
    -901 * -9 + 5337 + -13437 * 1, -2042 * 4 + 2631 + 5579,
    -8523 + 4601 * 1 + -6 * -676, 72, -8553 + 4789 * -2 + 3653 * 5,
    -78 * -46 + 49 * -158 + 4401, -7491 + 566 * 12 + -89 * -8,
    -2403 * 1 + 770 + -19 * -86, -15 * 663 + -584 + 10530,
    -3870 + -49 * -79, -141 * 3 + 7993 + 5 * -1513, -9744 + -1712 * -1 + 8032,
    -7540 + 397 * 19, 129, 2 * 643 + -511 * 11 + 4476, 0,
    163 * 57 + -7964 + -1279, -1 * -7957 + -4923 * -1 + -12751,
    1 * -5736 + -8681 + -2 * -7277, -500 + 57 * -163 + 9793 * 1,
    129, -1864 * 5 + 691 * -13 + -4 * -4608, 2 * 4443 + -4386 + 90 * -50,
    -3 * 239 + -303 + 1200, -63 * 128 + -1 * 9610 + 17923,
    -168 * 2 + -3960 + -4397 * -1, -1022 + -1 * 8663 + 9759,
    23 * -193 + 4379 + -7 * -41, 9308 + -5041 * -1 + -14102,
    501 + -3 * 93, -4314 + 64 * 71, -4734 + 12 * 526 + 518 * -3,
    607 * 10 + 2069 * -1 + -3781, 627 * 13 + 38 * 233 + 3 * -5665,
    4083 * -1 + -4506 + 8738, 183, -1 * 7945 + -1 * -410 + -3833 * -2,
    1 * -7873 + -4543 + 5 * 2516, -1 * 9059 + 1247 + 7997,
    4247 + -1409 * 3, -1 * 877 + -98 * 34 + 125 * 35,
    -1 * 491 + -22 * 353 + 7 * 1203, -1 * -538 + -6341 + 5917,
    9329 + 1 * -424 + -8747, -3386 + -30 * 66 + 5437,
    -6165 + 1 * 7785 + 1 * -1574, -5060 * 1 + -843 * 3 + 7740,
    -1797 * 4 + -10 * 269 + -55 * -181, 11 * -8 + 5184 + -5025,
    -1 * -1769 + 6020 + -7563, 8484 + -71 * -69 + 3340 * -4,
    -767 + 5710 * -1 + 6555, -5717 * 1 + -1752 + -48 * -157,
    -7016 + 1 * 7193, -28 * -239 + -1644 + 686 * -7,
    7388 * -1 + 3262 + 4323, 249, -1 * 8347 + 51 + 8509,
    1695 + -1 * 9770 + 4057 * 2, -13578 + -271 * -51,
    1776 * 2 + 4391 * -2 + 5285, 1 * 7816 + -6741 + -1037,
    -4 * -1885 + -1797 + 1 * -5631, 2499 + 8501 * 1 + -3661 * 3,
    1788 + -1 * -1219 + -3 * 981, 4057 * 1 + -6338 + 302 * 8,
    155, 2 * 33 + 7103 * 1 + -7060, 1 * 7355 + 282 + -7587,
    8478 + -8713 * 1 + -42 * -10, 1627 + 1 * -9739 + 8173,
    21, -6982 + 1 * 7669 + -582, 2678 * -1 + -2433 + 5217 * 1,
    1 * 6031 + 8503 + -14289, -2207 + 337 * -12 + 6399,
    5527 + -904 * 4 + -1 * 1699, 58 * -87 + -1 * -1997 + 794 * 4,
    -5765 * -1 + 22 + -5780, -1 * -6618 + 6702 + 3 * -4434,
    4546 + -14 * -407 + -10017, -3582 * -2 + 8152 + -15061,
    -1 * -4397 + -2 * 2071 + 215 * -1, 56 * -19 + 1847 * -2 + -4957 * -1,
    -6833 + 3 * 2358, -79 * 119 + -2280 + 11746,
    -1174 * 7 + 692 + -3 * -2579, -1931 * -3 + -207 * -47 + -15355,
    7 * -886 + -1063 + 7450, -21 * 303 + -115 * 77 + 15450,
    -3203 * 2 + -422 + 6833, -131 * -65 + -4403 + -3926,
    -1 * 1345 + -1 * 6242 + -96 * -81, 1 * -7975 + -2675 + -10895 * -1,
    -1 * 2119 + -505 * 3 + 1 * 3693, -1 * -5009 + 2504 + -7352,
    4 * 2084 + 358 + -8480, 70 * 1 + 813 * -1 + -7 * -113,
    160, 4527 + -1 * 4276, 21, -10 * 605 + -3889 + 1433 * 7,
    1 * -9064 + 5078 + 4173, -3877 * -1 + 8359 * 1 + -12064,
    -1661 * -1 + -3 * 1669 + 1 * 3429, -1 * -8719 + -2950 + -5617,
    2788 + 1 * 389 + -3166, -5113 + 5101 * 1 + 1 * 97,
    -7117 + -47 * -57 + -22 * -205, -1 * 7210 + 8 * -823 + 13831,
    -26 * 60 + 8923 + -3613 * 2, -290 * -1 + 4307 + 2 * -2255,
    2 * -4201 + 79 * 1 + 8427, 5 * -59 + 358 + 0,
    2855 * -3 + 1 * 8875 + -271 * 1, 292 * -18 + -9396 + 14738,
    -101 * 47 + 4147 + 606, -79 + -1 * -229, 1912 + 33 * 13 + -2257,
    9062 + 1 * 133 + -9189, -14 * 521 + 2 * -1147 + 257 * 38,
    1693 * -5 + -1 * -8101 + 593, -874 * -2 + -4945 + 201 * 17,
    -9 * -785 + 5453 + -12374, 6896 + 142 * 17 + 21 * -437,
    949 * -1 + 5417 + -4337, 701 + 849 * -9 + 7152,
    5 * 654 + -2520 + -703, -681 + 20 * 41, -6112 + -7 * 1288 + -20 * -768,
    4 * 1906 + -7848 + 409, 129 * -4 + -3506 + 4214,
    -39 * 91 + -4142 + 236 * 33, -7567 + -62 * 1 + 7718,
    -13118 + 1 * 13255, -2312 + -34 * -73, -8013 + -120 * -32 + 4314,
    -2789 * 1 + 1165 + -1663 * -1, 964 + -11 * 649 + 2 * 3097,
    -9498 + 3163 * -3 + -2384 * -8, -1436 * 5 + 8588 + -9 * 156,
    -10 * -320 + -7114 + -7 * -581, -5403 + 4 * 1226 + 737,
    -2 * 4113 + -4757 + 13058, -2 * -2722 + -6880 + 1529,
    2083 + -1840 * 1, -3839 + 1033 * -3 + -3517 * -2,
    59 * 16 + -281 * 11 + 2353, 3 * -349 + -2986 + -821 * -5,
    -10 * 125 + 1 * 9166 + -251 * 31, -7248 + 41 * 179, 2,
    -2 * -2063 + -3365 + -758, 8834 + -1 * 2785 + -6048,
    4635 + -3 * 3163 + -6 * -809, -958 + 4 * 722 + -1 * 1929
])


# ================= 主流程 =================
def main():
    # 🔐 指纹数据（你的原始数据）
    fp_data = """{"fp":{"0":"1.2.1","1":{"s":1,"v":false},"2":{"s":1,"v":[]},"3":{"s":1,"v":"5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"},"4":{"s":1,"v":"TypeError: Cannot read properties of null (reading '0')\\n    at W (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:14066)\\n    at Object.NiOqR (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:3108)\\n    at https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:29253\\n    at Array.map (<anonymous>)\\n    at je (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:29193)\\n    at Object.Qhlex (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:5250)\\n    at Ie (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:25296)\\n    at Object.NsuAP (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:4977)\\n    at Pe (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:24928)\\n    at Module.Ve [as get] (https://passport.sinaimg.cn/js/fp/1.2.1.umd.js:1:24667)"},"5":{"s":1,"v":33},"6":{"s":1,"v":"function bind() { [native code] }"},"7":{"s":1,"v":[["zh-CN"]]},"8":{"s":1,"v":true},"9":{"s":1,"v":false},"10":{"s":1,"v":true},"11":{"s":1,"v":5},"12":{"s":-1,"e":""},"13":{"s":1,"v":"20030107"},"14":{"s":1,"v":100},"15":{"s":1,"v":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"},"16":{"s":1,"v":{"vendor":"WebKit","renderer":"WebKit WebGL"}},"17":{"s":1,"v":"[object External]"},"18":{"s":1,"v":{"ow":1918,"oh":1030,"iw":631,"ih":911}},"19":{"s":1,"v":"chrome"},"20":{"s":1,"v":"chromium"},"21":{"s":1,"v":true},"22":{"s":1,"v":true},"23":{"s":1,"v":{"ots":false,"mtp":0,"mmtp":-1}}},"bh":{"mt":[],"kt":{"down":0,"up":0}},"meta":{"isTraceKeyboard":false,"isTraceMouse":false}}"""

    # 1️⃣ 加密指纹数据
    print("🔐 加密指纹数据...")
    encrypted = hybrid_encrypt_js_style(fp_data, RSA_PUB_KEY)

    # 2️⃣ 创建 Session（自动管理 Cookie）
    session = requests.Session()

    # 3️⃣ Step 1: 获取 rid
    print("📤 请求 rid...")
    headers_bd = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://visitor.passport.weibo.cn",
        "priority": "u=1, i",
        "referer": "https://visitor.passport.weibo.cn/",
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "sec-fetch-storage-access": "active",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    }

    try:
        resp1 = session.post(
            "https://passport.weibo.com/sso/bd",
            headers=headers_bd,
            data={"data": encrypted, "from": "pc-visitor"},
            timeout=15
        )
        resp1.raise_for_status()
        rid = resp1.json()["data"]["rid"]
        print(f"✅ 获取 rid: {rid}")
    except Exception as e:
        print(f"❌ Step1 失败: {e}")
        print(f"响应: {resp1.text[:300] if 'resp1' in locals() else 'N/A'}")
        return

    # 4️⃣ Step 2: 生成 visitor cookie
    print("🍪 生成 visitor cookie...")
    headers_gen = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "max-age=0",
        "content-type": "application/x-www-form-urlencoded",
        "if-modified-since": "0",
        "origin": "https://visitor.passport.weibo.cn",
        "priority": "u=1, i",
        "referer": "https://visitor.passport.weibo.cn/visitor/visitor?entry=sinawap&a=enter&url=https%3A%2F%2Fm.weibo.cn%2Fu%2F2867360410&domain=.weibo.cn&sudaref=&ua=php-sso_sdk_client-0.6.36&_rand=1776920963.8434",
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    }

    try:
        resp2 = session.post(
            "https://visitor.passport.weibo.cn/visitor/genvisitor2",
            headers=headers_gen,
            cookies={"WEIBOCN_FROM": "1110006030"},
            data={
                "cb": "visitor_gray_callback",
                "ver": "20250916",
                "tid": "",
                "from": "weibo",
                "webdriver": "false",
                # "rid": rid,
                "return_url": "https://m.weibo.cn/u/2867360410"
            },
            timeout=15
        )
        resp2.raise_for_status()
        print(f"✅ Cookie 已保存: {dict(session.cookies)}")
    except Exception as e:
        print(f"❌ Step2 失败: {e}")
        print(f"响应: {resp2.text[:300] if 'resp2' in locals() else 'N/A'}")
        return

    # 5️⃣ Step 3: 获取用户数据
    print("📥 获取用户微博数据...")
    headers_api = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9",
        "mweibo-pwa": "1",
        "priority": "u=1, i",
        "referer": "https://m.weibo.cn/u/2867360410",
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
        "x-xsrf-token": "3edc5f"  # ⚠️ 如失效需从页面动态提取
    }

    try:
        resp3 = session.get(
            "https://m.weibo.cn/api/container/getIndex",
            headers=headers_api,
            params={
                "type": "uid",
                "value": "2867360410",
                "containerid": "1076032867360410",
                "page": "2"
            },
            timeout=15
        )
        resp3.raise_for_status()

        # 打印结果
        print("\n" + "=" * 60)
        print("📊 响应结果:")
        print("=" * 60)
        result = resp3.json()
        print(json.dumps(result, ensure_ascii=False, indent=2)[:2000] + "...")
        print("=" * 60)

        # 简单解析
        if result.get("ok") == 1 and "data" in result:
            cards = result["data"].get("cards", [])
            print(f"✅ 获取成功！微博数量: {len(cards)}")
            if cards:
                print(f"📝 第一条微博: {cards[0].get('mblog', {}).get('text', '')[:100]}...")
        else:
            print(f"⚠️ API 返回异常: {result.get('msg', 'unknown')}")

    except Exception as e:
        print(f"❌ Step3 失败: {e}")
        print(f"响应: {resp3.text[:500] if 'resp3' in locals() else 'N/A'}")

    finally:
        session.close()


if __name__ == "__main__":
    main()