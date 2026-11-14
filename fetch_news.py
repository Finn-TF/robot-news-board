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
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
LOG_FILE = os.path.join(BASE_DIR, "daemon.log")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
CST = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 关键词 ----
# 机器人相关词（标题级判定：命中即可视为在讲机器人）
ROBOT_ZH = ["机器人", "人形", "具身智能", "具身", "机械臂", "灵巧手", "四足", "机器狗",
            "外骨骼", "协作机器人", "工业机器人", "服务机器人", "减速器", "伺服",
            "丝杠", "电子皮肤", "遥操作", "VLA", "宇树", "优必选", "智元",
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

    # ---- 国内财经 / 科技媒体（新增） ----
    {"id": "36kr", "name": "36氪", "board": "domestic", "lang": "zh",
     "url": "https://36kr.com/feed", "filter": "zh_title"},
    {"id": "huxiu", "name": "虎嗅", "board": "domestic", "lang": "zh",
     "url": "https://www.huxiu.com/rss/0.xml", "filter": "zh_title"},
    {"id": "jiqizhixin", "name": "机器之心", "board": "domestic", "lang": "zh",
     "url": "https://www.jiqizhixin.com/rss", "filter": "zh_title"},
    {"id": "zhidx", "name": "智东西", "board": "domestic", "lang": "zh",
     "url": "https://zhidx.com/feed", "filter": "zh_title"},
    {"id": "ifanr", "name": "爱范儿", "board": "domestic", "lang": "zh",
     "url": "https://www.ifanr.com/feed", "filter": "zh_title"},
    {"id": "jiemian", "name": "界面新闻", "board": "domestic", "lang": "zh",
     "url": "https://www.jiemian.com/rss", "filter": "zh_title"},
    {"id": "sina-tech", "name": "新浪科技", "board": "domestic", "lang": "zh",
     "url": "http://rss.sina.com.cn/tech/rollnews.xml", "filter": "zh_title"},
    {"id": "robotchina", "name": "中国机器人网", "board": "domestic", "lang": "zh",
     "url": "https://www.robot-china.com/rss.xml", "filter": "zh_title"},
    {"id": "ofweek-robot", "name": "OFweek 机器人", "board": "domestic", "lang": "zh",
     "url": "https://robot.ofweek.com/rss.xml", "filter": "zh_title"},
    {"id": "bing-cn-8", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("机器人 政策"), "filter": "zh"},
    {"id": "bing-cn-9", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("人形机器人 量产"), "filter": "zh"},
    {"id": "bing-cn-10", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("机器人 订单 交付"), "filter": "zh"},
    {"id": "bing-cn-11", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("机器人 行业 研报"), "filter": "zh"},
    {"id": "bing-cn-12", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("机器人 上市公司"), "filter": "zh"},
    {"id": "bing-cn-13", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("中国机器人产业联盟"), "filter": "zh"},
    {"id": "bing-cn-14", "name": "Bing 新闻检索", "board": "domestic", "lang": "zh",
     "url": bing("具身智能 融资"), "filter": "zh"},

    # ---- 海外机器人垂直媒体（新增） ----
    {"id": "therobotreport", "name": "The Robot Report", "board": "overseas", "lang": "en",
     "url": "https://www.therobotreport.com/feed/", "filter": None},
    {"id": "robotics247", "name": "Robotics 24/7", "board": "overseas", "lang": "en",
     "url": "https://www.robotics247.com/rss", "filter": None},
    {"id": "robohub", "name": "Robohub", "board": "overseas", "lang": "en",
     "url": "https://robohub.org/feed/", "filter": None},
    {"id": "venturebeat", "name": "VentureBeat AI", "board": "overseas", "lang": "en",
     "url": "https://venturebeat.com/category/ai/feed/", "filter": "en_title"},
    {"id": "engadget", "name": "Engadget", "board": "overseas", "lang": "en",
     "url": "https://www.engadget.com/rss.xml", "filter": "en_title"},
    {"id": "wired-ai", "name": "WIRED AI", "board": "overseas", "lang": "en",
     "url": "https://www.wired.com/feed/tag/ai/latest/rss", "filter": "en_title"},
    {"id": "arstechnica", "name": "Ars Technica", "board": "overseas", "lang": "en",
     "url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "filter": "en_title"},
    {"id": "bing-en-8", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("humanoid robot funding deployment"), "filter": "en"},
    {"id": "bing-en-9", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("dexterous robot hand manipulation"), "filter": "en"},
    {"id": "bing-en-10", "name": "Bing News", "board": "overseas", "lang": "en",
     "url": bing("warehouse robotics automation orders"), "filter": "en"},

    # ---- 大厂官方博客 / 新闻室（新增） ----
    {"id": "bostondynamics", "name": "Boston Dynamics Blog", "board": "bigtech", "lang": "en",
     "url": "https://bostondynamics.com/blog/feed/", "filter": "robot_only"},
    {"id": "metaai", "name": "Meta AI Blog", "board": "bigtech", "lang": "en",
     "url": "https://ai.meta.com/blog/rss/", "filter": "robot_only"},
    {"id": "googleai", "name": "Google AI Blog", "board": "bigtech", "lang": "en",
     "url": "https://blog.google/technology/ai/rss/", "filter": "robot_only"},
    {"id": "msr", "name": "Microsoft Research Blog", "board": "bigtech", "lang": "en",
     "url": "https://www.microsoft.com/en-us/research/feed/", "filter": "robot_only"},
    {"id": "amazonscience", "name": "Amazon Science", "board": "bigtech", "lang": "en",
     "url": "https://www.amazon.science/index.rss", "filter": "robot_only"},
    {"id": "appleml", "name": "Apple Machine Learning", "board": "bigtech", "lang": "en",
     "url": "https://machinelearning.apple.com/rss.xml", "filter": "robot_only"},

    # ---- 大厂/头部公司检索补充（新增） ----
    {"id": "bing-bt-17", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("宇树科技 上市"), "filter": "robot_only"},
    {"id": "bing-bt-18", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("智元机器人 量产"), "filter": "robot_only"},
    {"id": "bing-bt-19", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("特斯拉 Optimus 量产"), "filter": "robot_only"},
    {"id": "bing-bt-20", "name": "Bing News", "board": "bigtech", "lang": "en",
     "url": bing("Figure 03 humanoid"), "filter": "robot_only"},
    {"id": "bing-bt-21", "name": "Bing News", "board": "bigtech", "lang": "zh",
     "url": bing("1X NEO 家庭 机器人"), "filter": "robot_only"},
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
            name = clean_source_name(name)
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
    "org": ["撤销", "解散", "裁员", "离职", "重组", "团队调整", "layoff", "restructur"],
}

SUMMARY_MAX_POINTS = 6
SUMMARY_LINK_LIMIT = 4
CANDIDATE_MAX = 16
TOP_MIN, TOP_MAX = 3, 5

# 日报「影响判断」的规则口径：按事件类型给出通用判断，不含任何未在原文出现的事实
IMPACT_BY_TOPIC = {
    "massprod": "量产/交付信号直接影响产业链订单预期，重点跟踪产能爬坡与实际出货节奏。",
    "funding": "融资进展反映一级市场对赛道的定价，关注资金投向与后续量产兑现。",
    "ipo": "资本化进程推进，短期影响板块关注度，后续跟踪募资用途与业绩披露。",
    "launch": "新品或新模型发布带来产品力对比窗口，关注性能参数与商业化落地进度。",
    "earnings": "经营数据影响盈利预期，留意收入结构、毛利率与订单能见度。",
    "policy": "政策口径变化可能影响行业准入、补贴与采购节奏，需持续跟踪细则。",
    "org": "组织与团队调整反映大厂在具身智能路线上的取舍，关注后续资源投入变化。",
}
IMPACT_FALLBACK = "产业动态更新，暂无明确信号，建议结合原文核对细节。"

SIGNAL_ORDER = [("massprod", "量产/交付"), ("funding", "融资"), ("ipo", "上市/资本化"),
                ("policy", "政策"), ("earnings", "财报"), ("launch", "新品"),
                ("org", "组织调整")]
SIGNAL_LABEL = dict(SIGNAL_ORDER)

# 媒体标题里常见的栏目前缀（如「观察 | …」「重磅 | …」），日报正文里去掉
COLUMN_PREFIX = re.compile(r"^[\u4e00-\u9fff]{2,6}\s*[|｜]\s*")


def clean_headline(title):
    return COLUMN_PREFIX.sub("", (title or "").strip()).strip()


def clean_source_name(name):
    """把「澎湃新闻 on MSN」这类聚合站后缀去掉，保留原始媒体名。"""
    name = (name or "").strip()
    return re.sub(r"\s+on\s+MSN$", "", name, flags=re.I).strip()


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
        name = rec.get("source") or host
        if name in ("Bing 新闻检索", "Bing News"):
            name = host          # 与信息核对一致：检索通道没解析出媒体名时用原站域名
        name = clean_source_name(name)
        links.append({"title": rec.get("title") or "", "url": rec["url"],
                      "source": name, "published": rec.get("published") or "",
                      "host": host})
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


def build_candidates(selected, now, limit=CANDIDATE_MAX):
    """规则先筛候选池：事件聚类 → 打分排序 → 去重，供重点新闻与 DeepSeek 使用。"""
    scored = []
    for g in event_clusters(selected):
        items = g["items"]
        if not items:
            continue
        score, _latest, level, count = score_cluster(items, now)
        scored.append((score, items, pick_rep(items), level, count))
    scored.sort(key=lambda x: (x[0], x[2].get("published") or ""), reverse=True)

    candidates, seen_reps, seen_co_topics = [], [], {}
    for score, items, rep, level, count in scored:
        if len(candidates) >= limit:
            break
        text = (rep.get("title") or "").strip()
        if not text:
            continue
        if any(title_sim(text, t) >= 0.28 for t in seen_reps):
            continue
        cos = sorted({c for r in items for c in (r.get("companies") or [])})
        tag_text = " ".join((r.get("title") or "") + " " + (r.get("summary") or "")
                            for r in items)
        tags = topic_tags(tag_text)
        if tags and cos:
            if any(tags & seen_co_topics.get(c, set()) for c in cos):
                continue
            for c in cos:
                seen_co_topics[c] = seen_co_topics.get(c, set()) | tags

        candidates.append({
            "id": len(candidates) + 1,
            "items": items,
            "rep": rep,
            "level": level,
            "count": count,
            "score": round(score, 2),
            "tags": [t for t, _ in SIGNAL_ORDER if t in tags],
            "links": build_links(items),
        })
        seen_reps.append(text)
    return candidates


def candidate_topic(c):
    """先看代表条目标题命中的事件类型，再看整组聚类，避免「上市」被误判为量产。"""
    title_low = (c["rep"].get("title") or "").lower()
    for tag, _label in SIGNAL_ORDER:
        if any(w.lower() in title_low for w in TOPIC_GROUPS[tag]):
            return tag
    return c["tags"][0] if c.get("tags") else ""


def impact_for(c):
    return IMPACT_BY_TOPIC.get(candidate_topic(c), IMPACT_FALLBACK)


def candidate_detail(c, limit=160):
    """候选条目的中文摘要（没有中文摘要就不展示，避免英文原文混入日报正文）。"""
    detail = (c["rep"].get("summary") or "").strip()
    if not detail or not CJK_CHAR.search(detail):
        return ""
    return shorten(detail, limit)


def rule_top_news(candidates, limit=TOP_MAX):
    out = []
    for c in candidates[:limit]:
        rep = c["rep"]
        out.append({
            "event": clean_headline(rep.get("title") or ""),
            "impact": impact_for(c),
            "impact_mode": "rule",
            "level": c["level"],
            "count": c["count"],
            "companies": rep.get("companies") or [],
            "board": rep.get("board") or "",
            "published": rep.get("published") or "",
            "detail": candidate_detail(c),
            "links": c["links"],
        })
    return out


def rule_signals(candidates, top_ids, limit=5):
    """规则口径的前瞻信号：量产/交付、融资、上市、政策、财报、新品。"""
    out, seen_text = [], set()
    for c in candidates:
        topic = candidate_topic(c)
        if not topic:
            continue
        # 优先用未进重点新闻的条目，避免整份日报只围绕同一件事
        if c["id"] in top_ids:
            continue
        text = clean_headline(c["rep"].get("title") or "")
        key = norm_title(text)[:24]
        if key and key in seen_text:
            continue
        links = c["links"]
        rep = c["rep"]
        out.append({
            "tag": SIGNAL_LABEL.get(topic, "产业动态"),
            "text": text,
            "level": c["level"],
            "count": c["count"],
            "published": rep.get("published") or "",
            "source": (links[0]["source"] if links else (rep.get("source") or "")),
            "url": (links[0]["url"] if links else (rep.get("url") or "")),
        })
        if key:
            seen_text.add(key)
        if len(out) >= limit:
            break
    return out


def rule_mainline(candidates, selected, cross_n, counts):
    if not candidates:
        return "本轮未抓到可用条目，暂无明确信号；请查看页脚抓取状态或稍后重试。"
    first = candidates[0]
    raw_title = clean_headline(first["rep"].get("title") or "")
    # 主线里只取标题的主干（冒号/逗号前），避免出现截断的省略号
    title = re.split(r"[：:，,（(]", raw_title)[0].strip()
    title = shorten(title, 34)
    verify_txt = (f"{first['count']} 家独立站点交叉印证" if first["level"] == "cross"
                  else "单一来源、待进一步核实")
    hard_n = len([c for c in candidates if candidate_topic(c)])
    return (f"今日机器人板块焦点为「{title}」（{verify_txt}）；"
            f"本轮共收录 {len(selected)} 条公开信息（国内 {counts.get('domestic', 0)} / "
            f"海外 {counts.get('overseas', 0)} / 大厂 {counts.get('bigtech', 0)}），"
            f"{cross_n} 条获多家印证，另有 {hard_n} 条量产、融资、政策类硬信息值得跟踪。")


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


def redact(text, key):
    """错误信息里若意外带上 key，一律打码后再记录。"""
    return (text or "").replace(key, "***") if key else (text or "")


DEEPSEEK_SYSTEM_PROMPT = """你是国联民生证券研究所汽车行业研究团队的机器人产业日报编辑。
必须严格遵守以下规则：
1. 只能使用用户给出的候选新闻（编号 [#1]…[#N]）中的事实；禁止引入候选之外的公司、产品、订单、金额、数字、时间或来源。
2. 不得编造数据、链接、引述，不得给出目标价、投资评级或收益承诺。
3. 只输出一个 JSON 对象，不要 markdown 代码块，不要输出 JSON 之外的任何文字。
4. JSON 结构必须严格为：
{"mainline":"一句话概括今日机器人板块核心变化（40-90 字）",
 "top_news":[{"candidate_id":1,"event":"一句话说清发生了什么（不超过 60 字）","impact":"1-2 句影响判断，说明对产业链、公司或市场情绪的含义（不超过 90 字）"}],
 "signals":[{"candidate_id":2,"text":"一句前瞻或跟踪信号（不超过 50 字）"}]}
5. top_news 输出 3-5 条，按重要性从高到低排序；candidate_id 必须来自候选列表，且不能重复。
6. signals 输出 0-4 条，聚焦量产节点、订单、融资、政策等前瞻信息；候选新闻里没有可用信号时输出空数组 []。
7. 若某条新闻无法判断影响，impact 写「暂无明确信号」。
8. mainline 不得罗列条目标题，要概括当日主线。"""


def build_candidate_prompt(candidates, date_label):
    """固定模板：把候选池按编号交给模型，模型只能引用这些编号。"""
    lines = [f"日期：{date_label}", f"候选新闻共 {len(candidates)} 条（只能引用这些编号）："]
    for c in candidates:
        rep = c["rep"]
        verify = (f"多家印证（{c['count']} 家独立站点）"
                  if c["level"] == "cross" else "单一来源")
        cos = "、".join(rep.get("companies") or []) or "未标注"
        detail = candidate_detail(c, 220) or "无摘要"
        pub = (rep.get("published") or "")[:16].replace("T", " ")
        lines.append(f"[#{c['id']}] 标题：{clean_headline(rep.get('title') or '')}")
        lines.append(f"     来源：{rep.get('source') or ''}；时间：{pub}；"
                     f"涉及公司：{cos}；印证：{verify}")
        lines.append(f"     摘要：{detail}")
    lines.append("请按固定 JSON 结构输出。")
    return "\n".join(lines)


def validate_llm_report(obj, candidates, verbose=True):
    """严格校验模型输出：编号必须来自候选池，越界/重复/过短一律丢弃。"""
    by_id = {c["id"]: c for c in candidates}
    if not isinstance(obj, dict):
        raise RuntimeError("模型输出不是 JSON 对象")
    mainline = str(obj.get("mainline") or "").strip()
    if not (8 <= len(mainline) <= 240):
        raise RuntimeError("mainline 缺失或长度不合规")

    tops, seen = [], set()
    for item in obj.get("top_news") or []:
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("candidate_id"))
        except (TypeError, ValueError):
            continue
        if cid in seen or cid not in by_id:
            continue
        event = str(item.get("event") or "").strip()
        impact = str(item.get("impact") or "").strip()
        if not (4 <= len(event) <= 110) or not (4 <= len(impact) <= 170):
            continue
        c = by_id[cid]
        rep = c["rep"]
        tops.append({
            "event": event,
            "impact": impact,
            "impact_mode": "deepseek",
            "level": c["level"],
            "count": c["count"],
            "companies": rep.get("companies") or [],
            "board": rep.get("board") or "",
            "published": rep.get("published") or "",
            "detail": candidate_detail(c),
            "links": c["links"],          # 链接一律取真实条目，不用模型输出
        })
        seen.add(cid)
        if len(tops) >= TOP_MAX:
            break
    if len(tops) < TOP_MIN:
        raise RuntimeError(f"有效重点新闻仅 {len(tops)} 条，低于 {TOP_MIN} 条")

    signals, seen_sig = [], set()
    for item in obj.get("signals") or []:
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("candidate_id"))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if cid in seen_sig or cid not in by_id or not (4 <= len(text) <= 70):
            continue
        c = by_id[cid]
        rep = c["rep"]
        links = c["links"]
        topic = candidate_topic(c)
        signals.append({
            "tag": SIGNAL_LABEL.get(topic, "产业动态"),
            "text": text,
            "level": c["level"],
            "count": c["count"],
            "published": rep.get("published") or "",
            "source": (links[0]["source"] if links else (rep.get("source") or "")),
            "url": (links[0]["url"] if links else (rep.get("url") or "")),
        })
        seen_sig.add(cid)
        if len(signals) >= 4:
            break
    return {"mainline": mainline, "top_news": tops, "signals": signals}


def deepseek_report(cfg, candidates, date_label):
    """按固定提示词模板调用 DeepSeek（OpenAI 兼容），返回校验后的日报内容。"""
    llm = cfg.get("llm") or {}
    model = (llm.get("model") or "").strip()
    if not model:
        raise RuntimeError("config.llm.model 未配置")
    env_name = (llm.get("api_key_env") or "DEEPSEEK_API_KEY").strip()
    key = (os.environ.get(env_name) or "").strip()
    if not key:
        raise RuntimeError(f"环境变量 {env_name} 为空，未配置 key")
    base = (llm.get("base_url") or "https://api.deepseek.com").rstrip("/")
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": DEEPSEEK_SYSTEM_PROMPT},
            {"role": "user", "content": build_candidate_prompt(candidates, date_label)},
        ],
        "temperature": float(llm.get("temperature", 0.3)),
        "max_tokens": int(llm.get("max_tokens", 1600)),
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    try:
        data = http_post_json(url, payload, {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        }, timeout=int(llm.get("timeout") or 60))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(redact(f"{type(e).__name__}: {str(e)[:200]}", key))

    try:
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception:  # noqa: BLE001
        content = ""
    content = content.strip()
    if not content:
        raise RuntimeError("模型返回内容为空")
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    try:
        obj = json.loads(content)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise RuntimeError("模型输出不是合法 JSON")
        obj = json.loads(m.group(0))
    return validate_llm_report(obj, candidates)


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def format_date_label(now):
    return f"{now.year}年{now.month}月{now.day}日 {WEEKDAYS[now.weekday()]}"


def build_report(selected, now, cfg, counts=None, issue_no=1, verbose=True):
    """生成日报：规则式打底；DeepSeek 开启且有 key 时按固定模板改写，
    任何解析/超时/限流/鉴权失败都回落到规则式，页面始终有内容。"""
    counts = counts or {}
    llm = cfg.get("llm") or {}
    candidates = build_candidates(selected, now)
    cross_n = len([r for r in selected if (r.get("verify") or {}).get("level") == "cross"])

    top_news = rule_top_news(candidates)
    signals = rule_signals(candidates, {c["id"] for c in candidates[:TOP_MAX]})
    report = {
        "issue_no": issue_no,
        "date": now.strftime("%Y-%m-%d"),
        "date_label": format_date_label(now),
        "team": "国联民生研究所汽车组",
        "mode": "rule",
        "generated_at": now.isoformat(),
        "generated_label": now.strftime("%Y-%m-%d %H:%M"),
        "mainline": rule_mainline(candidates, selected, cross_n, counts),
        "top_news": top_news,
        "signals": signals,
        "stats": {
            "items": len(selected),
            "cross": cross_n,
            "single": len(selected) - cross_n,
            "clusters": len(event_clusters(selected)),
            "candidates": len(candidates),
        },
        "llm": {
            "enabled": bool(llm.get("enabled")),
            "used": False,
            "provider": llm.get("provider") or "deepseek",
            "model": llm.get("model") or "",
            "error": "",
        },
    }

    if not llm.get("enabled"):
        return report
    if not candidates:
        report["llm"]["error"] = "本轮无候选条目"
        if verbose:
            print("      日报：无候选条目，使用规则式", flush=True)
        return report
    try:
        out = deepseek_report(cfg, candidates, report["date_label"])
        report["mainline"] = out["mainline"]
        report["top_news"] = out["top_news"]
        report["signals"] = out["signals"]
        report["mode"] = "deepseek"
        report["llm"]["used"] = True
        if verbose:
            print(f"      日报：DeepSeek（{report['llm']['model']}）生成成功", flush=True)
    except Exception as e:  # noqa: BLE001
        report["llm"]["error"] = redact(f"{type(e).__name__}: {str(e)[:200]}",
                                        os.environ.get(
                                            (llm.get("api_key_env") or "DEEPSEEK_API_KEY")
                                            .strip(), ""))
        if verbose:
            print(f"      日报：DeepSeek 失败，降级为规则式（{report['llm']['error']}）",
                  flush=True)
    return report


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
def fetch_sources(sources, workers=10):
    """并发拉取所有通道；单个通道失败只记录原因，不影响其他通道。"""
    results = {}

    def one(src):
        try:
            return src["id"], fetch(src["url"], timeout=18), ""
        except Exception as e:  # noqa: BLE001
            return src["id"], None, f"{type(e).__name__}: {str(e)[:160]}"

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, s) for s in sources]
        for fut in as_completed(futures):
            sid, xml_text, err = fut.result()
            results[sid] = (xml_text, err)
    return results


def collect(verbose=True):
    now = datetime.now(CST)
    horizon = now - timedelta(days=60)
    records, report, seen_urls, seen_titles = [], [], set(), set()
    # 大厂通道口径统计：进入判定的条数 / 判定为非机器人被剔除 / 机器人相关但不属大厂被分流
    stats = {"bt_raw": 0, "bt_dropped": 0, "bt_demoted": 0}

    if verbose:
        print(f"      并发拉取 {len(SOURCES)} 个通道 ...", flush=True)
    fetched = fetch_sources(SOURCES)

    for src in SOURCES:
        status = {"id": src["id"], "name": src["name"], "board": src["board"],
                  "url": src["url"], "status": "ok", "count": 0, "raw": 0, "error": ""}
        xml_text, err = fetched.get(src["id"], (None, "未抓取"))
        if xml_text is None or err:
            status["status"] = "fail"
            status["error"] = err or "空响应"
            report.append(status)
            if verbose:
                print(f"  [FAIL] {src['name']:<26} {status['error']}")
            continue
        try:
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
                "source": media if (is_gnews or is_bing) and media else (
                    site_of(link) if (is_gnews or is_bing) else src["name"]),
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
           "llm": {"enabled": False, "provider": "deepseek",
                   "model": "deepseek-chat",
                   "base_url": "https://api.deepseek.com",
                   "api_key_env": "DEEPSEEK_API_KEY",
                   "max_tokens": 1600, "temperature": 0.3, "timeout": 60}}
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


def load_today_report(date):
    """读取当日已归档的日报（用于常规轮次沿用 9:00 的 DeepSeek 版）。"""
    path = os.path.join(ARCHIVE_DIR, f"{date}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rep = data.get("report") or {}
        return rep if rep.get("date") == date else None
    except Exception:  # noqa: BLE001
        return None


def load_archive_index():
    """读取归档索引（按日期升序）。不存在时返回空列表。"""
    path = os.path.join(ARCHIVE_DIR, "index.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def save_archive(payload, force=True):
    """把当日完整日报快照写入 archive/<日期>.json，并更新索引。

    - force=True（每日 9:00 日报任务）：始终覆盖当日快照，写入 DeepSeek 或降级后的规则版；
    - force=False（每 30 分钟常规抓取）：只在当日尚无 DeepSeek 版时更新规则版，
      避免把当天已生成的 DeepSeek 日报覆盖掉。历史其他日期永不改写。
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    rep = payload.get("report") or {}
    date = rep.get("date") or payload.get("generated_at", "")[:10]
    if not date:
        return []
    index = load_archive_index()
    existing = [e for e in index if e.get("date") == date]
    if existing and not force and existing[0].get("mode") == "deepseek":
        return index

    path = os.path.join(ARCHIVE_DIR, f"{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    entry = {
        "date": date,
        "issue_no": rep.get("issue_no"),
        "date_label": rep.get("date_label") or date,
        "mainline": rep.get("mainline") or "",
        "mode": rep.get("mode") or "rule",
        "generated_label": payload.get("generated_label") or "",
        "counts": {
            "domestic": len(payload.get("boards", {}).get("domestic", {}).get("items", [])),
            "overseas": len(payload.get("boards", {}).get("overseas", {}).get("items", [])),
            "bigtech": len(payload.get("boards", {}).get("bigtech", {}).get("items", [])),
        },
    }
    index = [e for e in index if e.get("date") != date]
    index.append(entry)
    index.sort(key=lambda e: e.get("date") or "")
    with open(os.path.join(ARCHIVE_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return index


def write_data_files(payload, archive_index=None):
    """写 data.json / data.js；data.js 同时内嵌归档索引，供 file:// 双击时使用。"""
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    if archive_index is None:
        archive_index = load_archive_index()
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.ROBOT_NEWS_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\nwindow.ROBOT_NEWS_ARCHIVE = ")
        json.dump(archive_index, f, ensure_ascii=False)
        f.write(";\n")


def log_line(msg):
    """同时打印到控制台并追加到 daemon.log，便于回看每轮结果。"""
    line = f"[{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def run_round(args, cfg, daily=False):
    """跑一轮抓取 + 翻译 + 写数据文件，返回 data payload。

    daily=True：每日 9:00 的正式日报任务，允许调用 DeepSeek 并强制归档当日一期；
    daily=False：每 30 分钟的常规抓取，只更新列表与规则式日报，不调用大模型。
    """
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

    # 期号：同一日期重跑沿用原期号，新日期顺延
    archive_index = load_archive_index()
    today = now.strftime("%Y-%m-%d")
    same_day = [e for e in archive_index if e.get("date") == today]
    issue_no = (same_day[0].get("issue_no") or len(archive_index) + 1) if same_day \
        else len(archive_index) + 1

    # 常规抓取不调用大模型：复制一份配置并关闭 llm，节约调用成本
    report_cfg = cfg
    if not daily:
        report_cfg = dict(cfg)
        report_cfg["llm"] = dict(cfg.get("llm") or {}, enabled=False)

    daily_report = build_report(selected, now, report_cfg,
                                counts={k: len(boards[k])
                                        for k in ["domestic", "overseas", "bigtech"]},
                                issue_no=issue_no)
    # 常规轮次不覆盖当日 9:00 的 DeepSeek 日报：列表更新，日报正文沿用当天正式一期
    if not daily:
        prev = load_today_report(today)
        if prev and prev.get("mode") == "deepseek":
            daily_report = prev
            print("      日报：沿用今日 9:00 的 DeepSeek 版（常规轮次只更新列表）")
    mode_txt = "DeepSeek 生成" if daily_report["mode"] == "deepseek" else "规则式（常规轮次或无 key 降级）"
    print(f"      日报：第 {issue_no} 期，重点 {len(daily_report['top_news'])} 条、"
          f"信号 {len(daily_report['signals'])} 条（{mode_txt}）")

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
        "report": daily_report,
        "meta": {
            "total_items": sum(len(boards[k]) for k in ["domestic", "overseas", "bigtech"]),
            "translated": 0 if not do_translate else len([r for r in selected if r["title_original"]]),
            "bigtech_filter": stats,
            "verify": {"cross": cross, "single": len(selected) - cross},
            "note": "数据来自公开 RSS 与新闻检索；每条含来源与原文链接。抓取失败的源见 source_report。",
        },
    }

    archive_index = save_archive(payload, force=daily)
    write_data_files(payload, archive_index)

    print("[3/4] 写入 data.json / data.js / archive 完成")
    print("[4/4] 汇总：")
    for k in ["domestic", "overseas", "bigtech", "jokes"]:
        n = len(payload["boards"][k]["items"])
        print(f"      {payload['boards'][k]['name']}: {n} 条")
    print(f"      归档：共 {len(archive_index)} 期，当日快照 archive/{daily_report['date']}.json")
    failed = [r for r in report if r["status"] == "fail"]
    print(f"      抓取通道：{len(report)} 个，成功 {len(report) - len(failed)} 个，失败 {len(failed)} 个")
    if failed:
        print("      失败来源：" + ", ".join(f"{r['name']}({r['id']})" for r in failed))
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
                payload = run_round(args, cfg, daily=getattr(args, "daily", False))
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
    ap.add_argument("--daily", action="store_true",
                    help="每日正式日报：允许调用 DeepSeek 并强制归档当日一期（9:00 任务用）")
    ap.add_argument("--interval", type=int, default=None, help="定时抓取间隔（分钟，覆盖 config.json）")
    args = ap.parse_args()

    if args.sync_only:
        with open(DATA_JSON, "r", encoding="utf-8") as f:
            payload = json.load(f)
        write_data_files(payload)
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
        run_round(args, cfg, daily=args.daily)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
        sys.exit(130)
