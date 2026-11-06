#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器人行业资讯看板 · 数据抓取脚本
------------------------------------
用途：从公开 RSS / 新闻检索源抓取机器人相关资讯，按三个板块分类写入 data.json。
板块：domestic(国内新闻) / overseas(海外新闻) / bigtech(大厂重点) / jokes(近期段子，手动维护)

原则：
1) 只使用真实存在的公开来源，每条保留原文链接与发布时间；
2) 抓取失败的源会在 data.json 的 source_report 中如实标记，不给页面填假数据；
3) jokes 板块不由脚本生成，脚本每次运行会原样保留已有段子。

用法：
    python fetch_news.py                 # 抓取 + 翻译英文条目 + 写入 data.json / data.js
    python fetch_news.py --no-translate  # 不调用翻译，英文摘要保留原文
    python fetch_news.py --max 60        # 每个板块最多保留条数
"""

import argparse
import gzip
import hashlib
import html
import json
import zlib
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(BASE_DIR, "data.json")
DATA_JS = os.path.join(BASE_DIR, "data.js")
CACHE_FILE = os.path.join(BASE_DIR, "translate_cache.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "daemon.log")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
CST = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 关键词 ----
# 机器人相关词（标题级判定：命中即可视为在讲机器人）
ROBOT_ZH = ["机器人", "人形", "具身智能", "具身", "机械臂", "灵巧手", "四足", "机器狗",
            "外骨骼", "协作机器人", "工业机器人", "服务机器人", "减速器", "伺服",
            "丝杠", "电子皮肤", "遥操作", "世界模型", "VLA", "宇树", "优必选", "智元",
            "银河通用", "傅利叶", "众擎", "星动纪元", "逐际动力", "开普勒", "松延动力",
            "乐聚", "擎朗", "云深处"]
ROBOT_EN = ["robot", "robots", "robotic", "robotics", "humanoid", "humanoids", "embodied",
            "manipulation", "manipulator", "quadruped", "locomotion", "exoskeleton",
            "boston dynamics", "optimus", "figure ai", "unitree", "agility robotics",
            "physical intelligence", "teleoperation", "actuator", "gripper"]

# 强相关词（摘要级判定：摘要里出现这些词才算真在讲机器人，
# 避免「某篇文章顺带提一句机器人」被算作机器人内容）
STRONG_ZH = ["人形机器人", "具身智能", "机器人本体", "灵巧手", "机械臂", "四足机器人",
             "机器狗", "减速器", "伺服", "谐波减速器", "关节模组", "电子皮肤", "遥操作",
             "外骨骼", "协作机器人", "工业机器人", "服务机器人", "机器人量产",
             "机器人供应链", "具身大脑"]
STRONG_EN = ["humanoid", "humanoids", "robotics", "robotic", "embodied", "manipulation",
             "manipulator", "quadruped", "locomotion", "exoskeleton", "teleoperation",
             "gripper", "actuator", "boston dynamics", "optimus", "figure ai", "unitree",
             "agility robotics", "physical intelligence", "1x technologies"]

# 「机器人大厂」集合：既有科技大厂，也有头部机器人公司
# 命中 + 机器人相关性，才归入「大厂重点」
ROBOT_COMPANIES = {
    "OpenAI": ["openai", "altman", "奥尔特曼"],
    "特斯拉": ["特斯拉", "tesla", "optimus", "擎天柱"],
    "英伟达": ["英伟达", "nvidia", "黄仁勋", "isaac", "groot"],
    "谷歌 DeepMind": ["deepmind", "gemini robotics", "谷歌", "google"],
    "Meta": ["meta", "facebook"],
    "宇树 Unitree": ["宇树", "unitree"],
    "优必选 UBTECH": ["优必选", "ubtech"],
    "智元 AgiBot": ["智元", "agibot"],
    "Figure": ["figure ai", "figure robotics", "figure 机器人", "figure"],
    "Boston Dynamics": ["boston dynamics", "波士顿动力"],
    "Agility": ["agility robotics", "agility"],
    "1X": ["1x technologies", "1x tech", "1x"],
    "Physical Intelligence": ["physical intelligence", "pi0", "π0"],
}

# 官方博客通道：公司名由来源决定，正文里不一定会再提一次
SOURCE_COMPANY = {"openai": "OpenAI", "nvidia": "英伟达", "deepmind": "谷歌 DeepMind"}

# ---------------------------------------------------------------- 抓取源 ----
def bing(query):
    # 注意：带 cc/setlang 参数时 Bing 会返回 HTML 反爬页，只保留最简参数
    q = urllib.parse.quote(query, safe="")
    return f"https://www.bing.com/news/search?q={q}&format=rss"

def gnews(query, hl="zh-CN", gl="CN", ceid="CN:zh-Hans"):
    q = urllib.parse.quote(query)
    return (f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}")

SOURCES = [
    # ---- 国内新闻 ----
    {"id": "qbitai", "name": "量子位", "board": "domestic", "lang": "zh",
     "url": "https://www.qbitai.com/feed", "filter": "zh_title"},
    {"id": "leiphone", "name": "雷峰网", "board": "domestic", "lang": "zh",
     "url": "https://www.leiphone.com/feed", "filter": "zh_title"},
    {"id": "ithome", "name": "IT之家", "board": "domestic", "lang": "zh",
     "url": "https://www.ithome.com/rss/", "filter": "zh_title"},
    {"id": "tmtpost", "name": "钛媒体", "board": "domestic", "lang": "zh",
     "url": "https://www.tmtpost.com/rss", "filter": "zh_title"},
    {"id": "bing-cn-1", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("人形机器人"), "filter": "zh"},
    {"id": "bing-cn-2", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("机器人产业链"), "filter": "zh"},
    {"id": "bing-cn-3", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("具身智能"), "filter": "zh"},
    {"id": "bing-cn-4", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("优必选 机器人"), "filter": "zh"},
    {"id": "bing-cn-5", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("机器人 融资"), "filter": "zh"},
    {"id": "bing-cn-6", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("机器人 量产"), "filter": "zh"},
    {"id": "bing-cn-7", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("外骨骼 机器人"), "filter": "zh"},

    # ---- 海外新闻 ----
    {"id": "ieee", "name": "IEEE Spectrum Robotics", "board": "overseas", "lang": "en",
     "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss", "filter": None},
    {"id": "techcrunch", "name": "TechCrunch Robotics", "board": "overseas", "lang": "en",
     "url": "https://techcrunch.com/category/robotics/feed/", "filter": None},
    {"id": "verge", "name": "The Verge", "board": "overseas", "lang": "en",
     "url": "https://www.theverge.com/rss/index.xml", "filter": "en_title"},
    {"id": "bing-en-1", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("humanoid robot"), "filter": "en"},
    {"id": "bing-en-2", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("Figure AI humanoid robot"), "filter": "en"},
    {"id": "bing-en-3", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("Boston Dynamics robot"), "filter": "en_title"},
    {"id": "bing-en-4", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("robotics startup funding"), "filter": "en"},
    {"id": "bing-en-5", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("robotics industry humanoid deployment"), "filter": "en"},
    {"id": "bing-en-6", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("Atlas robot Boston Dynamics"), "filter": "en_title"},
    {"id": "bing-en-7", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("robotics warehouse automation"), "filter": "en"},

    # ---- 大厂重点 ----
    {"id": "openai", "name": "OpenAI News", "board": "bigtech", "lang": "en",
     "url": "https://openai.com/news/rss.xml", "filter": "robot_only"},
    {"id": "nvidia", "name": "NVIDIA Blog", "board": "bigtech", "lang": "en",
     "url": "https://blogs.nvidia.com/feed/", "filter": "robot_only"},
    {"id": "deepmind", "name": "Google DeepMind Blog", "board": "bigtech", "lang": "en",
     "url": "https://deepmind.google/blog/rss.xml", "filter": "robot_only"},
    {"id": "bing-bt-1", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("OpenAI 机器人"), "filter": "robot_only"},
    {"id": "bing-bt-1b", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("OpenAI 具身智能"), "filter": "robot_only"},
    {"id": "bing-bt-2", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("OpenAI robotics"), "filter": "robot_only"},
    {"id": "bing-bt-3", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("英伟达 机器人"), "filter": "robot_only"},
    {"id": "bing-bt-4", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("特斯拉 Optimus 人形机器人"), "filter": "robot_only"},
    {"id": "bing-bt-5", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("Google DeepMind robotics"), "filter": "robot_only"},
    {"id": "bing-bt-6", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("Meta robotics embodied AI"), "filter": "robot_only"},
    {"id": "bing-bt-7", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("OpenAI Figure 机器人"), "filter": "robot_only"},
    {"id": "bing-bt-8", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("NVIDIA Isaac GR00T humanoid"), "filter": "robot_only"},
    # 头部机器人公司（新增通道）
    {"id": "bing-bt-9", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("宇树科技 Unitree"), "filter": "robot_only"},
    {"id": "bing-bt-10", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("优必选 人形机器人"), "filter": "robot_only"},
    {"id": "bing-bt-11", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("智元机器人 AgiBot"), "filter": "robot_only"},
    {"id": "bing-bt-12", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("Figure AI humanoid"), "filter": "robot_only"},
    {"id": "bing-bt-13", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("Boston Dynamics Atlas"), "filter": "robot_only"},
    {"id": "bing-bt-14", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("Agility Robotics Digit"), "filter": "robot_only"},
    {"id": "bing-bt-15", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("1X Technologies humanoid NEO"), "filter": "robot_only"},
    {"id": "bing-bt-16", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("Physical Intelligence robot"), "filter": "robot_only"},
]

# ---------------------------------------------------------------- 工具 ----
def fetch(url, timeout=25, retries=1):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    raw = gzip.decompress(raw)
                elif "deflate" in enc:
                    raw = zlib.decompress(raw, zlib.MAX_WBITS | 32)
                return raw.decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.2)
    raise last_err


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def shorten(text, limit):
    text = strip_html(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 尽量断在句末
    for sep in ["。", "！", "？", ". ", "；", "; "]:
        idx = cut.rfind(sep)
        if idx > limit * 0.55:
            return cut[: idx + len(sep)].strip()
    return cut.rstrip(" ,，、;；") + "…"


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


NS = {"dc": "http://purl.org/dc/elements/1.1/",
      "content": "http://purl.org/rss/1.0/modules/content/",
      "atom": "http://www.w3.org/2005/Atom",
      "media": "http://search.yahoo.com/mrss/"}


def nodes_text(node, paths):
    for path in paths:
        el = node.find(path, NS)
        if el is not None:
            txt = "".join(el.itertext()).strip()
            if txt:
                return txt
    return ""


def parse_feed(xml_text):
    """解析 RSS 2.0 / Atom，返回统一结构的条目列表。"""
    root = ET.fromstring(xml_text.strip().lstrip("\ufeff"))
    items = []
    entries = root.findall(".//item")
    is_atom = False
    if not entries:
        entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        is_atom = True
    for node in entries:
        if is_atom:
            title = nodes_text(node, ["{http://www.w3.org/2005/Atom}title"])
            link_el = node.find("{http://www.w3.org/2005/Atom}link[@rel='alternate']", NS)
            if link_el is None:
                link_el = node.find("{http://www.w3.org/2005/Atom}link", NS)
            link = link_el.get("href", "") if link_el is not None else ""
            date_raw = nodes_text(node, ["{http://www.w3.org/2005/Atom}published",
                                         "{http://www.w3.org/2005/Atom}updated"])
            desc = nodes_text(node, ["{http://www.w3.org/2005/Atom}summary",
                                     "{http://www.w3.org/2005/Atom}content"])
            media_name = ""
        else:
            title = nodes_text(node, ["title"])
            link = nodes_text(node, ["link"]) or nodes_text(node, ["guid"])
            date_raw = nodes_text(node, ["pubDate", "dc:date", "atom:updated"])
            desc = nodes_text(node, ["description", "content:encoded"])
            # Bing RSS 带 <News:Source>原始媒体名；Google News RSS 带 <source>
            media_name = strip_html(nodes_text(node, ["{*}Source", "source"]))
        desc = strip_html(desc)
        items.append({
            "title": strip_html(title),
            "link": link.strip(),
            "published_raw": date_raw,
            "summary_raw": desc,
            "media": media_name,
        })
    return items


def clean_media(name):
    """Bing 偶尔返回媒体标识符（如 'cj'、'kucoin'），这类不作来源展示。"""
    m = re.sub(r"\s+", " ", name or "").strip()
    if not m or len(m) <= 2:
        return ""
    if m.lower() in {"cj", "molihua", "kucoin", "theaiinsider", "news.bjd", "msn"}:
        return ""
    return m


def unwrap_bing(link):
    """Bing RSS 的 link 是 apiclick 跳转，取出 url= 参数得到原文直链。"""
    if "bing.com/news/apiclick" not in link:
        return link
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    return qs.get("url", [link])[0]


def unwrap_gnews_title(title):
    """Google News 标题尾部带 ' - 媒体名'，去掉。"""
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()


def match_keywords(text, words):
    low = text.lower()
    return [w for w in words if w.lower() in low]


ASCII_ALIAS = re.compile(r"^[a-z0-9][a-z0-9 .+-]*$")


def alias_hit(low_text, alias):
    """英文别名按词边界匹配，避免 figure/metadata 之类的误命中。"""
    if ASCII_ALIAS.match(alias):
        return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", low_text)
    return alias in low_text


def bigtech_hit(text):
    """返回命中的「机器人大厂」标准名（可能多个）。"""
    low = (text or "").lower()
    hits = []
    for name, keys in ROBOT_COMPANIES.items():
        if any(alias_hit(low, k) for k in keys):
            hits.append(name)
    return hits


def norm_title(t):
    t = t.lower()
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", t)
    return t


# 与机器人无关的技术名词，先清掉再判定，避免 robots.txt / RPA 之类误命中
FALSE_POSITIVE = re.compile(
    r"robots\.txt|robotic process automation|robots meta|robotframework|robot framework",
    re.I)


def prep_text(text):
    return FALSE_POSITIVE.sub(" ", text or "")


def en_word_hit(low, term):
    """英文按词边界匹配，robotic 不会命中 robotics 之外的脏词。"""
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", low) is not None


def is_robot_related(text, lang):
    low = prep_text(text).lower()
    if lang == "zh":
        return bool(match_keywords(low, ROBOT_ZH))
    return any(en_word_hit(low, w) for w in ROBOT_EN)


def is_robot_strong(text, lang):
    """摘要级强相关判定：必须是机器人本体/核心零部件/具身智能这类词。"""
    low = prep_text(text).lower()
    if lang == "zh":
        return bool(match_keywords(low, STRONG_ZH))
    return any(en_word_hit(low, w) for w in STRONG_EN)


# ------------------------------------------------------------ 信息核对 ----
# 同一事件在不同独立站点出现 → 已交叉印证；只有一家 → 单一来源未核实
CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")
SIM_STOP = {"the", "a", "an", "and", "of", "for", "to", "in", "on", "with", "is",
            "are", "its", "it", "this", "that", "as", "at", "by", "from", "new",
            "says", "say", "will", "be", "has", "have", "after", "over", "into"}
VERIFY_THRESHOLD = 0.30      # 标题相似度阈值（经人工核对该站点数据集调校）
MAX_VERIFY_SOURCES = 5


def title_units(text):
    """标题指纹：中文取 2 字窗口，英文取词，用于判断是否同一事件。"""
    low = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower())
    units = set()
    zh = "".join(CJK_CHAR.findall(low))
    for i in range(len(zh) - 1):
        units.add(zh[i:i + 2])
    for w in re.findall(r"[a-z0-9]+", low):
        if len(w) >= 2 and w not in SIM_STOP:
            units.add(w)
    return units


def title_sim(a, b):
    ua, ub = title_units(a), title_units(b)
    if not ua or not ub:
        return 0.0
    return len(ua & ub) / len(ua | ub)


def site_of(url):
    try:
        host = urllib.parse.urlparse(url or "").netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    return host[4:] if host.startswith("www.") else host


def event_clusters(records):
    """按标题指纹把同一事件的条目聚成一组（速览与信息核对共用同一套聚类口径）。"""
    groups = []
    for rec in sorted(records, key=lambda r: r["published"], reverse=True):
        key_text = rec["title"] + " " + (rec.get("title_original") or "")
        best, best_sim = None, 0.0
        for g in groups:
            sim = title_sim(key_text, g["key"])
            if sim > best_sim:
                best, best_sim = g, sim
        if best is not None and best_sim >= VERIFY_THRESHOLD:
            best["items"].append(rec)
        else:
            groups.append({"key": key_text, "items": [rec]})
    return groups


def attach_verification(records):
    """给每条资讯附上「信息核对」信息：同事件的其他独立来源与原文链接。

    说明：只统计真实抓到的条目，按站点域名判独立；没有第二家就如实标单一来源。
    """
    for g in event_clusters(records):
        sources, seen = [], set()
        for rec in sorted(g["items"], key=lambda r: r["published"]):
            host = site_of(rec["url"])
            if not host or host in seen:
                continue
            seen.add(host)
            name = rec["source"] or host
            if name in ("Bing 新闻检索", "Bing News"):
                name = host          # 检索通道没解析出媒体名时，用原站域名更可核对
            sources.append({"name": name, "url": rec["url"],
                            "published": rec["published"], "host": host})
        sources = sources[:MAX_VERIFY_SOURCES]
        level = "cross" if len(sources) >= 2 else "single"
        for rec in g["items"]:
            own = site_of(rec["url"])
            ordered = ([s for s in sources if s["host"] == own]
                       + [s for s in sources if s["host"] != own])
            rec["verify"] = {"level": level, "count": len(sources),
                             "sources": ordered}


# ------------------------------------------------------------ 本轮速览 ----
# 规则式速览：事件聚类 → 打分（多家印证 / 时效 / 硬信息 / 本体与大厂）→ 3-6 条要点。
# 要点只从真实条目的标题与摘要派生，链接直接取自这些条目，不生成任何新事实。
HARD_ZH = ["量产", "量产线", "交付", "下线", "投产", "产线", "工厂", "出货",
           "订单", "签约", "中标", "融资", "轮融资", "亿元", "IPO", "上市",
           "财报", "营收", "发布", "首发", "开售", "预售", "落地", "试点",
           "扩产", "合作", "收购", "降价", "涨价"]
HARD_EN = ["mass production", "production line", "factory", "gigafactory",
           "deploy", "deployment", "rollout", "launch", "unveil", "funding",
           "raises", "raised", "series a", "series b", "series c", "ipo",
           "order", "orders", "shipping", "deliver", "delivery", "contract",
           "partnership", "acquisition", "acquire", "revenue", "earnings",
           "commercial"]
BODY_ZH = ["人形机器人", "机器人本体", "灵巧手", "四足机器人", "机器狗",
           "具身智能", "机械臂"]
BODY_EN = ["humanoid", "humanoids", "dexterous", "quadruped", "manipulation",
           "embodied"]

# 同一家大厂的同一类事件只留一条，避免速览被同一件事占满
TOPIC_GROUPS = {
    "ipo": ["ipo", "上市", "纳斯达克", "goes public", "publicly listed",
            "stock market", "listing"],
    "funding": ["融资", "funding", "raises", "raised", "series a", "series b", "series c"],
    "massprod": ["量产", "mass production", "production line", "下线", "交付",
                 "deliver", "deployment", "rollout", "出货", "订单", "产线"],
    "launch": ["发布", "launch", "unveil", "首发", "开售", "预售"],
    "earnings": ["财报", "营收", "revenue", "earnings", "亏损", "销量"],
    "policy": ["政策", "监管", "标准", "regulation", "standard"],
}

SUMMARY_MAX_POINTS = 6
SUMMARY_LINK_LIMIT = 4


def fresh_hours(published, now):
    """条目距本轮抓取时间的时长（小时）；时间解析失败按最旧处理。"""
    try:
        dt = datetime.fromisoformat(published)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return max(0.0, (now - dt).total_seconds() / 3600.0)
    except Exception:  # noqa: BLE001
        return 24 * 7


def pick_rep(items):
    """优先挑中文标题条目做要点，再按发布时间取最新。"""
    zh = [r for r in items if CJK_CHAR.search(r.get("title") or "")]
    pool = zh or items
    return max(pool, key=lambda r: r.get("published") or "")


def build_links(items, limit=SUMMARY_LINK_LIMIT):
    """要点对应的原文链接：同一站点只取一条，按发布时间倒序。"""
    links, seen = [], set()
    for rec in sorted(items, key=lambda r: r.get("published") or "", reverse=True):
        host = site_of(rec.get("url"))
        if not rec.get("url") or host in seen:
            continue
        seen.add(host)
        links.append({"title": rec.get("title") or "", "url": rec["url"],
                      "source": rec.get("source") or host,
                      "published": rec.get("published") or "", "host": host})
        if len(links) >= limit:
            break
    return links


def score_cluster(items, now):
    """给一个事件聚类打分：cross 印证 > 硬信息 > 时效 > 本体/大厂。"""
    verify = (items[0].get("verify") or {}) if items else {}
    level = verify.get("level") or "single"
    count = int(verify.get("count") or 1)
    latest = max(items, key=lambda r: r.get("published") or "")
    hours = fresh_hours(latest.get("published"), now)
    score = 0.0
    if level == "cross":
        score += 4.0 + min(1.5, 0.5 * max(0, count - 2))
    score += max(0.0, 2.0 - hours / 12.0)
    title_low = (latest.get("title") or "").lower()
    all_low = " ".join(((r.get("title") or "") + " " + (r.get("summary") or "")).lower()
                       for r in items)
    if any(w.lower() in title_low for w in HARD_ZH + HARD_EN):
        score += 2.5
    elif any(w.lower() in all_low for w in HARD_ZH + HARD_EN):
        score += 1.0
    if any(w.lower() in title_low for w in BODY_ZH + BODY_EN):
        score += 1.0
    if latest.get("companies"):
        score += 1.2
    if latest.get("board") == "bigtech":
        score += 0.6
    return score, latest, level, count


def topic_tags(text):
    """标题/摘要命中的事件类型，用于同一公司同类事件的去重。"""
    low = (text or "").lower()
    tags = set()
    for tag, words in TOPIC_GROUPS.items():
        if any(w in low for w in words):
            tags.add(tag)
    return tags


def build_rule_points(selected, now, limit=SUMMARY_MAX_POINTS):
    """把本轮条目聚成事件并挑出要点；返回按重要性排序的 3-6 条。"""
    scored = []
    for g in event_clusters(selected):
        items = g["items"]
        if not items:
            continue
        score, latest, level, count = score_cluster(items, now)
        scored.append((score, items, pick_rep(items), level, count))
    scored.sort(key=lambda x: (x[0], x[2].get("published") or ""), reverse=True)

    points, seen_reps, seen_co_topics = [], [], {}
    for score, items, rep, level, count in scored:
        if len(points) >= limit:
            break
        text = (rep.get("title") or "").strip()
        if not text:
            continue
        # 同一事件换标题重复出现：按标题相似度再挡一层
        if any(title_sim(text, t) >= 0.28 for t in seen_reps):
            continue
        # 同一家大厂的同一类事件（如 Agility 上市被多家写成多条）只留一条；
        # 公司名取整个聚类的并集，避免代表条目标题没点名公司时漏判
        cos = sorted({c for r in items for c in (r.get("companies") or [])})
        tag_text = " ".join((r.get("title") or "") + " " + (r.get("summary") or "")
                            for r in items)
        tags = topic_tags(tag_text)
        if tags and cos:
            if any(tags & seen_co_topics.get(c, set()) for c in cos):
                continue
            for c in cos:
                seen_co_topics[c] = seen_co_topics.get(c, set()) | tags

        detail = (rep.get("summary") or "").strip()
        if detail and not CJK_CHAR.search(detail):
            detail = ""                     # 未翻译的英文摘要不混进中文要点
        if detail and norm_title(detail)[:16] == norm_title(text)[:16]:
            detail = ""
        detail = shorten(detail, 140)
        points.append({
            "text": text,
            "detail": detail,
            "level": level,
            "count": count,
            "board": rep.get("board") or "",
            "source": rep.get("source") or "",
            "published": rep.get("published") or "",
            "companies": rep.get("companies") or [],
            "links": build_links(items),
        })
        seen_reps.append(text)
    return points


def http_post_json(url, payload, headers, timeout=45):
    """最小可用的 JSON POST（仅用于可选的 LLM 总结，避免引入第三方依赖）。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw)
        return json.loads(raw.decode("utf-8", "replace"))


def llm_analysis(cfg, summary):
    """可选的 LLM 分析：只喂本轮精选条目，失败由调用方降级回规则式。"""
    llm = cfg.get("llm") or {}
    model = (llm.get("model") or "").strip()
    if not model:
        raise RuntimeError("config.llm.model 未配置")
    env_name = (llm.get("api_key_env") or "ROBOT_LLM_API_KEY").strip()
    key = (os.environ.get(env_name) or llm.get("api_key") or "").strip()
    if not key:
        raise RuntimeError(f"未提供 API key（环境变量 {env_name} 为空）")
    base = (llm.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    url = base + "/chat/completions"

    lines = [f"本轮概况：{summary['headline']}", "精选条目："]
    for i, p in enumerate(summary["points"], 1):
        tags = "多家印证" if p["level"] == "cross" else "单一来源"
        src = "、".join(l.get("source") or l.get("host") or "" for l in p["links"]) or p["source"]
        lines.append(f"{i}. [{tags}] {p['text']}")
        if p.get("detail"):
            lines.append(f"   摘要：{p['detail']}")
        lines.append(f"   来源：{src}（{p.get('published', '')[:16]}）")

    messages = [
        {"role": "system", "content":
         "你是机器人产业研究员。只能依据用户给出的条目撰写分析，"
         "不得编造条目以外的事件、公司、数字或链接。"
         "请用 5-8 句中文写一段连贯分析，讲清本轮主线、趋势变化与值得注意的信号；"
         "不要逐条复述标题，不要输出网址，不要使用条目中没有的专有名词。"},
        {"role": "user", "content": "\n".join(lines) + "\n\n请基于以上条目写分析。"},
    ]
    data = http_post_json(url, {
        "model": model, "messages": messages,
        "temperature": 0.3, "max_tokens": 900,
    }, {"Content-Type": "application/json",
        "Authorization": "Bearer " + key},
        timeout=int(llm.get("timeout") or 45))

    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    text = re.sub(r"https?://\S+", "", text).strip()
    if len(text) < 20:
        raise RuntimeError("模型返回内容为空")
    return shorten(text, 1200)


def build_summary(selected, now, cfg, counts=None, verbose=True):
    """生成本轮速览：规则式为默认；LLM 仅在 enabled + 有 key 时补充，失败自动降级。"""
    counts = counts or {}
    cross_n = len([r for r in selected if (r.get("verify") or {}).get("level") == "cross"])
    headline = (f"本轮收录 {len(selected)} 条（国内 {counts.get('domestic', 0)} / "
                f"海外 {counts.get('overseas', 0)} / 大厂 {counts.get('bigtech', 0)}），"
                f"{cross_n} 条获多家独立站点交叉印证。")
    llm = cfg.get("llm") or {}
    summary = {
        "mode": "rule",
        "generated_at": now.isoformat(),
        "generated_label": now.strftime("%Y-%m-%d %H:%M"),
        "headline": headline,
        "analysis": "",
        "points": build_rule_points(selected, now),
        "stats": {
            "items": len(selected),
            "cross": cross_n,
            "single": len(selected) - cross_n,
            "clusters": len(event_clusters(selected)),
        },
        "llm": {
            "enabled": bool(llm.get("enabled")),
            "used": False,
            "provider": llm.get("provider") or "",
            "model": llm.get("model") or "",
            "error": "",
        },
    }

    if not llm.get("enabled"):
        return summary
    if not summary["points"]:
        summary["llm"]["error"] = "本轮无可用于总结的条目"
        return summary
    try:
        text = llm_analysis(cfg, summary)
        summary["analysis"] = text
        summary["mode"] = "llm"
        summary["llm"]["used"] = True
        if verbose:
            print(f"      速览：已用 {summary['llm']['model']} 生成分析段", flush=True)
    except Exception as e:  # noqa: BLE001
        # 失败一律降级回规则式；错误只记录类型与简短原因，不含 key
        summary["llm"]["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        if verbose:
            print(f"      速览：LLM 调用失败，降级为规则式（{summary['llm']['error']}）",
                  flush=True)
    return summary


# ---------------------------------------------------------------- 翻译 ----
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def translate(text, cache):
    """调用 Google 翻译公开接口把英文转中文；失败返回 None。"""
    text = text.strip()
    if not text:
        return ""
    key = hashlib.md5(text.encode("utf-8")).hexdigest()
    if key in cache:
        return cache[key]
    # 接口单次长度限制，截断到 1500 字符
    q = text[:1500]
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=auto&tl=zh-CN&dt=t&q=" + urllib.parse.quote(q))
    try:
        raw = fetch(url, timeout=10, retries=0)
        data = json.loads(raw)
        zh = "".join(seg[0] for seg in data[0] if seg and seg[0])
        zh = zh.strip()
        if zh:
            cache[key] = zh
            return zh
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------- 主流程 ----
def collect(verbose=True):
    now = datetime.now(CST)
    horizon = now - timedelta(days=60)
    records, report, seen_urls, seen_titles = [], [], set(), set()
    # 大厂通道口径统计：进入判定的条数 / 判定为非机器人被剔除 / 机器人相关但不属大厂被分流
    stats = {"bt_raw": 0, "bt_dropped": 0, "bt_demoted": 0}

    for src in SOURCES:
        status = {"id": src["id"], "name": src["name"], "board": src["board"],
                  "url": src["url"], "status": "ok", "count": 0, "raw": 0, "error": ""}
        try:
            xml_text = fetch(src["url"])
            entries = parse_feed(xml_text)
        except Exception as e:  # noqa: BLE001
            status["status"] = "fail"
            status["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            report.append(status)
            if verbose:
                print(f"  [FAIL] {src['name']:<26} {status['error']}")
            continue

        status["raw"] = len(entries)
        for ent in entries:
            title = ent["title"]
            link = ent["link"]
            if not title or not link:
                continue
            if src["id"].startswith("gnews"):
                title = unwrap_gnews_title(title)
            link = unwrap_bing(link)
            if not link.startswith("http"):
                continue

            dt = parse_date(ent["published_raw"])
            if dt is None:
                continue
            dt = dt.astimezone(CST)
            if dt < horizon or dt > now + timedelta(hours=6):
                continue

            is_gnews = src["id"].startswith("gnews")
            is_bing = src["id"].startswith("bing")
            media = clean_media(ent.get("media", ""))
            if is_gnews:
                title = unwrap_gnews_title(title)
                # 去掉描述里重复的标题与媒体名，只留真正有信息量的片段
                if media and ent["summary_raw"].endswith(media):
                    ent["summary_raw"] = ent["summary_raw"][: -len(media)].strip()
                if ent["summary_raw"].startswith(title):
                    ent["summary_raw"] = ent["summary_raw"][len(title):].strip()
                if len(norm_title(ent["summary_raw"])) <= len(norm_title(title)):
                    ent["summary_raw"] = ""

            blob = f"{title} {ent['summary_raw']}"
            if src["board"] == "bigtech":
                stats["bt_raw"] += 1
            mode = src["filter"]
            if mode == "zh":                       # 标题或摘要命中中文机器人词
                keep = is_robot_related(blob, "zh")
            elif mode == "zh_title":               # 媒体 RSS：标题必须命中，避免正文提一句就误收
                keep = is_robot_related(title, "zh")
            elif mode == "en":
                keep = is_robot_related(blob, "en")
            elif mode == "en_title":
                keep = is_robot_related(title, "en")
            elif mode == "robot_only":             # 大厂源：任何语种，但必须与机器人相关
                keep = is_robot_related(blob, "zh") or is_robot_related(blob, "en")
            else:
                keep = True
            if not keep:
                if src["board"] == "bigtech":
                    stats["bt_dropped"] += 1
                continue

            url_key = re.sub(r"[#?].*$", "", link).rstrip("/")
            tkey = norm_title(title)[:28]
            if url_key in seen_urls or (tkey and tkey in seen_titles):
                continue

            # 「大厂重点」口径：必须与机器人强相关，且是大厂/头部机器人公司的内容
            lang = src["lang"]
            bt_title = bigtech_hit(title)
            bt_all = bt_title or bigtech_hit(blob)
            # 标题直接讲机器人，或摘要出现强相关词（本体/核心零部件/具身智能等）
            robot_ok = (is_robot_related(title, "zh") or is_robot_related(title, "en")
                        or is_robot_strong(title, "zh") or is_robot_strong(title, "en")
                        or is_robot_strong(blob, "zh") or is_robot_strong(blob, "en"))

            board = src["board"]
            if board == "bigtech":
                own_company = SOURCE_COMPANY.get(src["id"])
                if robot_ok and (bt_all or own_company):
                    board = "bigtech"
                else:
                    # 与机器人无关/只是顺带提及 → 不进「大厂重点」
                    board = "overseas" if lang == "en" else "domestic"
                    bt_all = bt_title
                    stats["bt_demoted"] += 1
            elif robot_ok and bt_title:
                # 标题点名机器人大厂 + 机器人相关 → 归入大厂重点
                board = "bigtech"

            summary = shorten(ent["summary_raw"], 240 if lang == "zh" else 460)

            seen_urls.add(url_key)
            if tkey:
                seen_titles.add(tkey)
            records.append({
                "board": board,
                "title": title,
                "title_original": "",
                "summary": summary,
                "summary_original": "",
                "source": media if (is_gnews or is_bing) and media else src["name"],
                "source_id": src["id"],
                "via": ("Bing" if is_bing and media else
                        (src["name"] if is_gnews and media else "")),
                "url": link,
                "published": dt.isoformat(),
                "lang": lang,
                "companies": (bt_title or bt_all)[:2],
                "is_placeholder": False,
            })
            status["count"] += 1

        report.append(status)
        if verbose:
            flag = "OK  " if status["status"] == "ok" else "FAIL"
            print(f"  [{flag}] {src['name']:<26} raw={status['raw']:<4} kept={status['count']}")

    return records, report, now, stats


def assign_companies(rec):
    if rec["companies"]:
        return rec["companies"]
    blob = rec["title"] + " " + rec["summary"]
    return bigtech_hit(blob)[:2]


def extract_meta_desc(page_html):
    """从文章页里取 meta description / og:description，作为缺失摘要的补充。"""
    pats = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{20,})["\']',
        r'<meta[^>]+content=["\']([^"\']{20,})["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{20,})["\']',
        r'<meta[^>]+content=["\']([^"\']{20,})["\'][^>]+property=["\']og:description["\']',
        r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\']([^"\']{20,})["\']',
    ]
    for p in pats:
        m = re.search(p, page_html, re.I)
        if m:
            txt = strip_html(html.unescape(m.group(1)))
            if len(txt) >= 20:
                return txt
    return ""


def enrich_summaries(records, verbose=True, limit=40):
    """对「有直链但无摘要」的条目，抓原文页面的 meta 描述补摘要（抓不到就留空）。"""
    cand = [r for r in records if not r["summary"] and r["url"].startswith("http")
            and "news.google.com" not in r["url"]]
    filled = 0
    for rec in cand[:limit]:
        try:
            page = fetch(rec["url"], timeout=12, retries=0)
        except Exception:  # noqa: BLE001
            continue
        desc = extract_meta_desc(page)
        if desc:
            rec["summary"] = shorten(desc, 240 if rec["lang"] == "zh" else 460)
            filled += 1
        time.sleep(0.2)
    if verbose:
        print(f"      补摘要：{filled}/{min(len(cand), limit)} 条", flush=True)
    return filled


def translate_records(records, verbose=True):
    cache = load_cache()
    total = [r for r in records if r["lang"] == "en"]
    done, fails = 0, 0
    for rec in total:
        zh_title = translate(rec["title"], cache)
        zh_sum = translate(rec["summary"], cache) if rec["summary"] else ""
        if zh_title:
            rec["title_original"] = rec["title"]
            rec["title"] = zh_title
        if zh_sum:
            rec["summary_original"] = rec["summary"]
            rec["summary"] = shorten(zh_sum, 240)
        if not zh_title:
            fails += 1
        else:
            fails = 0
        if fails >= 5:
            print("      翻译接口连续失败，停止翻译；其余条目保留英文原文。", flush=True)
            break
        done += 1
        if verbose and done % 10 == 0:
            print(f"  ... translated {done}/{len(total)}", flush=True)
        time.sleep(0.05)
    save_cache(cache)
    return len([r for r in records if r["title_original"]])


PLACEHOLDER_JOKES = [
    {"id": "demo-1", "board": "jokes", "title": "示例占位：这里以后放用户提供的机器人段子",
     "summary": "本条为结构占位，不是真实流传的段子。请在 jokes 数据中用真实内容替换。",
     "source": "手动录入", "url": "", "published": "", "lang": "zh",
     "companies": [], "is_placeholder": True},
    {"id": "demo-2", "board": "jokes", "title": "示例占位：段子二（待替换）",
     "summary": "示例占位。字段说明见 README：title / summary / source / published 可填。",
     "source": "手动录入", "url": "", "published": "", "lang": "zh",
     "companies": [], "is_placeholder": True},
    {"id": "demo-3", "board": "jokes", "title": "示例占位：段子三（待替换）",
     "summary": "示例占位。段子可按时间倒序展示，也支持搜索。",
     "source": "手动录入", "url": "", "published": "", "lang": "zh",
     "companies": [], "is_placeholder": True},
]


def load_config():
    """读取 config.json；缺失或损坏时回落到默认值，不让进程因此退出。"""
    cfg = {"interval_minutes": 60, "retries": 1, "max": 60,
           "per_source": 16, "translate": True,
           "llm": {"enabled": False, "provider": "openai-compatible", "model": "",
                   "base_url": "https://api.openai.com/v1", "api_key": "",
                   "api_key_env": "ROBOT_LLM_API_KEY", "timeout": 45}}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if k in cfg})
            if isinstance(data.get("llm"), dict):
                cfg["llm"].update({k: v for k, v in data["llm"].items()
                                   if k in cfg["llm"]})
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] config.json 读取失败，使用默认配置：{e}", flush=True)
    return cfg


def log_line(msg):
    """同时打印到控制台并追加到 daemon.log，便于回看每轮结果。"""
    line = f"[{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def run_round(args, cfg):
    """跑一轮抓取 + 翻译 + 写数据文件，返回 data payload。"""
    max_items = args.max or int(cfg.get("max", 60))
    per_source = args.per_source or int(cfg.get("per_source", 16))
    do_translate = (not args.no_translate) and bool(cfg.get("translate", True))

    print(f"[1/4] 抓取 {len(SOURCES)} 个公开来源 ...")
    records, report, now, stats = collect()
    print(f"      原始命中 {len(records)} 条；大厂通道进入判定 {stats['bt_raw']} 条，"
          f"判定非机器人剔除 {stats['bt_dropped']} 条，"
          f"机器人相关但不属大厂分流 {stats['bt_demoted']} 条")

    for rec in records:
        rec["companies"] = assign_companies(rec)
        rec["id"] = hashlib.md5((rec["url"] + rec["title"]).encode("utf-8")).hexdigest()[:12]

    records.sort(key=lambda r: r["published"], reverse=True)
    boards = {}
    for key in ["domestic", "overseas", "bigtech"]:
        picked, used, src_counter = [], set(), {}
        for rec in records:
            if rec["board"] != key or rec["id"] in used:
                continue
            sid = rec["source_id"]
            # Google 检索源多为「标题+跳转链接」，无摘要，限额小一些；自有 RSS 可多留
            cap = 6 if sid.startswith("gnews") else (16 if sid.startswith("bing") else 20)
            cap = min(cap, per_source)
            if src_counter.get(sid, 0) >= cap:
                continue
            picked.append(rec)
            used.add(rec["id"])
            src_counter[sid] = src_counter.get(sid, 0) + 1
            if len(picked) >= max_items:
                break
        boards[key] = picked

    for rec in records:
        rec.pop("source_id", None)

    selected = [r for k in ["domestic", "overseas", "bigtech"] for r in boards[k]]
    if do_translate:
        print(f"[2/4] 补全缺失摘要 + 翻译英文条目（{len([r for r in selected if r['lang'] == 'en'])} 条英文待翻译）...")
        enrich_summaries(selected)
        translated = translate_records(selected)
        print(f"      已翻译 {translated} 条")
    else:
        print("[2/4] 跳过翻译（--no-translate）")

    # 翻译后再去一次重：同一站点同标题（如 Unite.AI 中英文版同稿）只保留一条
    for key in ["domestic", "overseas", "bigtech"]:
        kept, seen_key = [], set()
        for rec in boards[key]:
            dup_key = (norm_title(rec["title"])[:40], site_of(rec["url"]))
            if dup_key[0] and dup_key in seen_key:
                continue
            seen_key.add(dup_key)
            kept.append(rec)
        boards[key] = kept
    selected = [r for k in ["domestic", "overseas", "bigtech"] for r in boards[k]]

    attach_verification(selected)
    cross = len([r for r in selected if r["verify"]["level"] == "cross"])
    print(f"      信息核对：已交叉印证 {cross} 条，单一来源 {len(selected) - cross} 条")

    summary = build_summary(selected, now, cfg,
                            counts={k: len(boards[k])
                                    for k in ["domestic", "overseas", "bigtech"]})
    mode_txt = "大模型分析 + 规则要点" if summary["mode"] == "llm" else "规则式要点"
    print(f"      速览：{len(summary['points'])} 条要点（{mode_txt}）")

    # 保留已有段子（脚本绝不改写段子内容）
    existing_jokes = []
    if os.path.exists(DATA_JSON):
        try:
            with open(DATA_JSON, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_jokes = old.get("boards", {}).get("jokes", [])
            if isinstance(old_jokes, list) and old_jokes:
                existing_jokes = old_jokes
        except Exception:  # noqa: BLE001
            existing_jokes = []
    boards["jokes"] = existing_jokes or PLACEHOLDER_JOKES

    def board_meta(key):
        alive = [r for r in report if r["board"] == key]
        return {
            "ok_sources": len([r for r in alive if r["status"] == "ok"]),
            "failed_sources": len([r for r in alive if r["status"] == "fail"]),
        }

    payload = {
        "generated_at": now.isoformat(),
        "generated_label": now.strftime("%Y-%m-%d %H:%M"),
        "boards": {
            "domestic": {"key": "domestic", "name": "国内新闻",
                         "desc": "中国人形机器人、产业链与国产机器人公司的公开新闻",
                         "items": boards["domestic"], **board_meta("domestic")},
            "overseas": {"key": "overseas", "name": "海外新闻",
                         "desc": "海外机器人公司、产业动态与人形机器人进展",
                         "items": boards["overseas"], **board_meta("overseas")},
            "bigtech": {"key": "bigtech", "name": "大厂重点",
                        "desc": "机器人大厂动态：OpenAI、特斯拉、英伟达、DeepMind、Meta，以及宇树、优必选、智元、Figure、Boston Dynamics 等",
                        "items": boards["bigtech"], **board_meta("bigtech")},
            "jokes": {"key": "jokes", "name": "近期段子",
                      "desc": "机器人相关段子与梗，由用户手动提供（当前为示例占位）",
                      "items": boards["jokes"], "ok_sources": 0, "failed_sources": 0},
        },
        "source_report": report,
        "summary": summary,
        "meta": {
            "total_items": sum(len(boards[k]) for k in ["domestic", "overseas", "bigtech"]),
            "translated": 0 if not do_translate else len([r for r in selected if r["title_original"]]),
            "bigtech_filter": stats,
            "verify": {"cross": cross, "single": len(selected) - cross},
            "note": "数据来自公开 RSS 与新闻检索；每条含来源与原文链接。抓取失败的源见 source_report。",
        },
    }

    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    # 供 file:// 双击打开时使用（fetch 在本地文件协议下会被浏览器拦截）
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.ROBOT_NEWS_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")

    print("[3/4] 写入 data.json / data.js 完成")
    print("[4/4] 汇总：")
    for k in ["domestic", "overseas", "bigtech", "jokes"]:
        n = len(payload["boards"][k]["items"])
        print(f"      {payload['boards'][k]['name']}: {n} 条")
    failed = [r for r in report if r["status"] == "fail"]
    if failed:
        print(f"      失败来源 {len(failed)} 个：" +
              ", ".join(f"{r['name']}({r['id']})" for r in failed))
    else:
        print("      所有来源抓取成功")
    return payload


def daemon_loop(args, cfg):
    """常驻定时抓取：按 interval_minutes 循环，单轮失败重试，失败不退出。"""
    log_line(f"定时抓取启动：间隔 {max(1, int(cfg.get('interval_minutes', 60)))} 分钟，"
             f"单轮最多重试 {max(0, int(cfg.get('retries', 1)))} 次，日志写入 {LOG_FILE}")
    while True:
        interval = max(1, int(cfg.get("interval_minutes", 60)))
        retries = max(0, int(cfg.get("retries", 1)))
        started = time.time()
        payload = None
        for attempt in range(retries + 1):
            try:
                payload = run_round(args, cfg)
                break
            except Exception as e:  # noqa: BLE001
                log_line(f"本轮抓取失败（第 {attempt + 1} 次尝试）：{type(e).__name__}: {e}")
                if attempt < retries:
                    time.sleep(min(30, 5 * (attempt + 1)))
        if payload is None:
            log_line("本轮放弃，保留上一份 data.json；等待下一轮")
        else:
            b = payload["boards"]
            counts = "、".join(
                f"{b[k]['name']} {len(b[k]['items'])} 条" for k in ["domestic", "overseas", "bigtech"])
            failed = [r for r in payload["source_report"] if r["status"] != "ok"]
            fail_txt = ("、".join(f"{r['name']}({r['id']})" for r in failed) if failed else "无")
            log_line(f"本轮完成：{counts}；失败通道 {fail_txt}；耗时 {time.time() - started:.0f}s")
        wait_sec = max(3, interval * 60 - (time.time() - started))
        log_line(f"下一轮在 {wait_sec / 60:.1f} 分钟后")
        time.sleep(wait_sec)
        # 每轮重新读配置，改 config.json 里的间隔后无需重启进程
        new_cfg = load_config()
        if cfg.get("_interval_fixed"):
            new_cfg["interval_minutes"] = cfg["interval_minutes"]
            new_cfg["_interval_fixed"] = True
        cfg = new_cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="每个板块最多保留条数（默认读 config.json）")
    ap.add_argument("--per-source", type=int, default=None,
                    help="同一抓取源在每个板块的条数上限（默认读 config.json）")
    ap.add_argument("--no-translate", action="store_true", help="不翻译英文条目")
    ap.add_argument("--sync-only", action="store_true",
                    help="不联网抓取，仅把现有 data.json 同步生成 data.js（手动改完段子后用）")
    ap.add_argument("--daemon", action="store_true", help="常驻定时抓取模式")
    ap.add_argument("--interval", type=int, default=None, help="定时抓取间隔（分钟，覆盖 config.json）")
    args = ap.parse_args()

    if args.sync_only:
        with open(DATA_JSON, "r", encoding="utf-8") as f:
            payload = json.load(f)
        with open(DATA_JS, "w", encoding="utf-8") as f:
            f.write("window.ROBOT_NEWS_DATA = ")
            json.dump(payload, f, ensure_ascii=False)
            f.write(";\n")
        n = len(payload.get("boards", {}).get("jokes", {}).get("items", []))
        print(f"已从 data.json 生成 data.js（段子 {n} 条）。刷新页面即可看到改动。")
        return

    cfg = load_config()
    if args.interval:
        cfg["interval_minutes"] = args.interval
        cfg["_interval_fixed"] = True
    if args.daemon:
        daemon_loop(args, cfg)
    else:
        run_round(args, cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
        sys.exit(130)
