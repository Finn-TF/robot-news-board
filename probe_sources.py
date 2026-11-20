"""临时探测脚本（第二轮）：重点验证国内财经站的公开 JSON/HTML 列表与 Bing 定向检索变体。
用完即删。
"""
import gzip
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ROBOT_ZH = ["机器人", "具身智能", "人形", "机械臂", "灵巧手", "减速器", "伺服", "外骨骼"]


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, application/json, text/html, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
            raw = gzip.decompress(raw)
        return resp.status, resp.headers.get("Content-Type") or "", raw


def count_robot(text):
    low = text.lower()
    return sum(1 for w in ROBOT_ZH if w in text) + sum(1 for w in
                                                     ["robot", "humanoid", "embodied"] if w in low)


def probe(item):
    sid, url = item
    try:
        status, ctype, raw = get(url)
    except Exception as e:  # noqa: BLE001
        return f"[FAIL] {sid:<16} {type(e).__name__}: {str(e)[:80]}"
    text = raw.decode("utf-8", "ignore")
    out = f"[OK]   {sid:<16} {status} {len(raw)//1024}KB {ctype[:32]} robot={count_robot(text)}"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            out += f" topkeys={list(data.keys())[:6]}"
            inner = data.get("data")
            if isinstance(inner, dict):
                out += f" data.keys={list(inner.keys())[:8]}"
                for k, v in inner.items():
                    if isinstance(v, list) and v:
                        out += f" | {k}[0].keys={list(v[0].keys())[:10] if isinstance(v[0], dict) else type(v[0]).__name__}"
                        break
        return out
    except Exception:  # noqa: BLE001
        pass
    # 抽取标题样例
    titles = re.findall(r">(机器人[^<>]{4,40}|[^<>]{0,20}机器人[^<>]{4,40})<", text)
    uniq = list(dict.fromkeys(t.strip() for t in titles))[:4]
    if uniq:
        out += " | sample: " + " || ".join(uniq)
    return out


def probe_bing(item):
    sid, domain, query = item
    base = f"site:{domain}" + (f" {query}" if query else "")
    q = urllib.parse.quote(base, safe="")
    url = f"https://www.bing.com/news/search?q={q}&format=rss"
    try:
        status, _, raw = get(url)
        root = ET.fromstring(raw.decode("utf-8", "ignore").strip().lstrip("\ufeff"))
        items = root.findall(".//item")
    except Exception as e:  # noqa: BLE001
        return f"[BING-FAIL] {sid:<12} {type(e).__name__}: {str(e)[:70]}"
    titles, medias = [], []
    for node in items:
        t = node.find("title")
        titles.append("".join(t.itertext()).strip() if t is not None else "")
        m = node.find("{*}Source")
        medias.append("".join(m.itertext()).strip() if m is not None else "")
    hits = [t for t in titles if count_robot(t) > 0]
    return (f"[BING] {sid:<12} q={base[:34]:<34} items={len(items)} robot={len(hits)} "
            f"media={sorted({m for m in medias if m})[:4]}"
            + (f" | {hits[0][:40]}" if hits else ""))


URLS = [
    ("cls-telegraph2", "https://www.cls.cn/nodeapi/telegraphList?app=CailianpressWeb&os=web&sv=7.7.5"),
    ("cls-depth", "https://www.cls.cn/nodeapi/depthList?app=CailianpressWeb&os=web&sv=7.7.5"),
    ("cls-search-html", "https://www.cls.cn/searchPage?keyword=%E6%9C%BA%E5%99%A8%E4%BA%BA&type=telegram"),
    ("cls-search-api", "https://www.cls.cn/api/sw?app=CailianpressWeb&keyword=%E6%9C%BA%E5%99%A8%E4%BA%BA&os=web&sv=7.7.5"),
    ("yicai-list", "https://www.yicai.com/api/ajax/getlistbycid?cid=32&page=1"),
    ("yicai-search", "https://www.yicai.com/search?keys=%E6%9C%BA%E5%99%A8%E4%BA%BA"),
    ("nbd-column", "https://www.nbd.com.cn/columns/3/news"),
    ("nbd-search", "https://www.nbd.com.cn/search?keyword=%E6%9C%BA%E5%99%A8%E4%BA%BA"),
    ("em-news", "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=345&order=1&page_index=1&page_size=20"),
    ("10jqka", "http://news.10jqka.com.cn/today_list/"),
    ("zhitong-api", "https://www.zhitongcaijing.com/api/index/hot-article"),
    ("wscn-live2", "https://api-one-wscn.awtmt.com/apiv1/content/lives?channel=global-channel&client=pc&limit=100"),
    ("cnyes-list", "https://news.cnyes.com/api/v3/news/category/tw_stock?limit=30"),
    ("sina-roll-xml", "http://rss.sina.com.cn/roll/finance/hot_roll.xml"),
]

BING = [
    ("jx2", "jiqizhixin.com", "具身智能"),
    ("jx3", "jiqizhixin.com", ""),
    ("jx4", "jiqizhixin.com", "大模型"),
    ("rc2", "robot-china.com", "人形机器人"),
    ("ifanr", "ifanr.com", "机器人"),
    ("qbitai2", "qbitai.com", "具身智能"),
    ("sina-fin", "finance.sina.com.cn", "机器人"),
    ("eastmoney", "eastmoney.com", "机器人"),
    ("netease", "163.com", "机器人"),
    ("qq", "qq.com", "机器人"),
    ("sohu", "sohu.com", "机器人"),
    ("technews2", "technews.tw", "人形機器人"),
]

if __name__ == "__main__":
    print("=" * 26, "API/HTML 探测", "=" * 26, flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for line in ex.map(probe, URLS):
            print(line, flush=True)
    print("=" * 26, "Bing 定向检索变体", "=" * 26, flush=True)
    with ThreadPoolExecutor(max_workers=4) as ex:
        for line in ex.map(probe_bing, BING):
            print(line, flush=True)
