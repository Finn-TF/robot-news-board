"""临时探测脚本：在 GitHub Actions（境外）环境实测候选源可访问性与产出。
不写入任何数据文件，仅打印结果供人工判断。用完即删。
"""
import gzip
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ROBOT_ZH = ["机器人", "具身智能", "人形", "机械臂", "灵巧手", "减速器", "伺服", "外骨骼"]
ROBOT_EN = ["robot", "humanoid", "embodied", "robotics", "actuator", "quadruped"]


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, application/json, text/html, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        ctype = resp.headers.get("Content-Type") or ""
        return resp.status, ctype, raw


def count_robot(text):
    low = text.lower()
    n = sum(1 for w in ROBOT_ZH if w in text) + sum(1 for w in ROBOT_EN if w in low)
    return n


def probe_url(item):
    sid, url = item
    try:
        status, ctype, raw = get(url)
    except Exception as e:  # noqa: BLE001
        return f"[URL-FAIL] {sid:<22} {type(e).__name__}: {str(e)[:90]}"
    text = raw.decode("utf-8", "ignore")
    out = f"[URL-OK]   {sid:<22} {status} {len(raw)//1024}KB {ctype[:40]}"
    # RSS/Atom
    try:
        root = ET.fromstring(text.strip().lstrip("\ufeff"))
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        if items:
            titles = []
            for node in items[:80]:
                t = node.find("title")
                if t is None:
                    t = node.find("{http://www.w3.org/2005/Atom}title")
                titles.append("".join(t.itertext()) if t is not None else "")
            hits = [x for x in titles if count_robot(x) > 0]
            out += f" | feed items={len(items)} robot_titles={len(hits)}"
            if hits:
                out += " | sample: " + " || ".join(h[:40] for h in hits[:3])
            return out
    except Exception:  # noqa: BLE001
        pass
    # JSON
    try:
        data = json.loads(text)
        keys = list(data.keys())[:6] if isinstance(data, dict) else f"list[{len(data)}]"
        out += f" | json keys={keys}"
        blob = json.dumps(data, ensure_ascii=False)[:200000]
        out += f" robot_hits={count_robot(blob)}"
        return out
    except Exception:  # noqa: BLE001
        pass
    # HTML
    anchors = re.findall(r"<a[^>]+>(.*?)</a>", text, flags=re.S)[:300]
    hits = [re.sub(r"<[^>]+>", "", a).strip() for a in anchors]
    hits = [h for h in hits if h and count_robot(h) > 0]
    out += f" | html anchors={len(anchors)} robot_anchors={len(hits)}"
    if hits:
        out += " | sample: " + " || ".join(h[:40] for h in hits[:3])
    return out


def probe_bing(item):
    sid, domain, query = item
    q = urllib.parse.quote(f"site:{domain} {query}", safe="")
    url = f"https://www.bing.com/news/search?q={q}&format=rss"
    try:
        status, ctype, raw = get(url)
    except Exception as e:  # noqa: BLE001
        return f"[BING-FAIL] {sid:<18} {type(e).__name__}: {str(e)[:80]}"
    text = raw.decode("utf-8", "ignore")
    try:
        root = ET.fromstring(text.strip().lstrip("\ufeff"))
        items = root.findall(".//item")
    except Exception as e:  # noqa: BLE001
        return f"[BING-ERR]  {sid:<18} {type(e).__name__}: {str(e)[:80]} len={len(raw)}"
    titles, medias = [], []
    for node in items:
        t = node.find("title")
        titles.append("".join(t.itertext()).strip() if t is not None else "")
        m = node.find("{*}Source")
        medias.append(("".join(m.itertext()).strip() if m is not None else ""))
    hits = [t for t in titles if count_robot(t) > 0]
    uniq_media = sorted({m for m in medias if m})
    return (f"[BING-OK]   {sid:<18} items={len(items)} robot={len(hits)} "
            f"media={uniq_media[:6]}" + (f" | sample: {hits[0][:50]}" if hits else ""))


URLS = [
    # 方案一：国内财经/科技（大陆，境外可达性待测）
    ("cls-home", "https://www.cls.cn/"),
    ("cls-telegraph", "https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=7.7.5"),
    ("stcn-home", "https://www.stcn.com/"),
    ("stcn-rss", "http://www.stcn.com/rss/news.xml"),
    ("yicai-home", "https://www.yicai.com/"),
    ("yicai-rss", "https://www.yicai.com/rss/"),
    ("nbd-home", "https://www.nbd.com.cn/"),
    ("nbd-rss", "https://www.nbd.com.cn/rss/"),
    ("eastmoney-rss", "http://rss.eastmoney.com/rss.aspx?type=1"),
    ("sina-fin-roll", "http://rss.sina.com.cn/roll/finance/hot_roll.xml"),
    ("wscn-live", "https://api-one-wscn.awtmt.com/apiv1/content/lives?channel=global-channel&client=pc&limit=50"),
    ("zhitong-home", "https://www.zhitongcaijing.com/"),
    ("gelonghui-home", "https://www.gelonghui.com/"),
    ("jiemian-rss", "https://a.jiemian.com/index.php?m=article&a=rss"),
    ("huxiu-rss", "https://www.huxiu.com/rss/0.xml"),
    ("pingwest-rss", "https://www.pingwest.com/feed"),
    ("163-tech-rss", "https://www.163.com/special/00011K6L/rss_newstech.xml"),
    ("cnstock-home", "https://www.cnstock.com/"),
    # 方案一：港台/海外中文转载聚合
    ("technews", "https://technews.tw/feed/"),
    ("cnyes-rss", "https://news.cnyes.com/rss/cat/headline"),
    ("cnyes-api", "https://news.cnyes.com/api/v3/news/category/tw_stock?limit=30"),
    ("bnext", "https://www.bnext.com.tw/rss"),
    ("ithome-tw", "https://www.ithome.com.tw/rss"),
    ("cna-tech", "https://feeds.feedburner.com/rsscna/technology"),
    ("cnbeta", "https://www.cnbeta.com.tw/backend.php"),
    ("inside-tw", "https://www.inside.com.tw/feed/rss"),
    ("techorange", "https://buzzorange.com/techorange/feed/"),
    ("udn-fin", "https://udn.com/rssfeed/news/2/6649?ch=news"),
    ("moneydj", "https://www.moneydj.com/kmdj/news/rss/rsslist.aspx"),
    ("hket", "https://www.hket.com/rss/"),
    ("aastocks", "http://www.aastocks.com/tc/resources/datafeed/rss.ashx?type=1"),
    ("zaobao-cn", "https://www.zaobao.com.sg/rss/realtime/china"),
    ("kr-asia", "https://kr-asia.com/feed"),
    ("technode", "https://technode.com/feed/"),
    ("investing-cn", "https://cn.investing.com/rss/news_25.rss"),
    ("rsshub-cls", "https://rsshub.app/cls/telegraph"),
]

BING = [
    ("jx", "jiqizhixin.com", "机器人"),
    ("36kr", "36kr.com", "机器人"),
    ("huxiu", "huxiu.com", "机器人"),
    ("qbitai", "qbitai.com", "机器人"),
    ("jiemian", "jiemian.com", "机器人"),
    ("ithome", "ithome.com", "机器人"),
    ("tmtpost", "tmtpost.com", "机器人"),
    ("zhidx", "zhidx.com", "机器人"),
    ("ofweek", "ofweek.com", "机器人"),
    ("robot-china", "robot-china.com", "机器人"),
    ("leiphone", "leiphone.com", "机器人"),
    ("geekpark", "geekpark.net", "机器人"),
    ("cls", "cls.cn", "机器人"),
    ("stcn", "stcn.com", "机器人"),
    ("yicai", "yicai.com", "机器人"),
    ("nbd", "nbd.com.cn", "机器人"),
    ("eeo", "eeo.com.cn", "机器人"),
    ("technews", "technews.tw", "机器人"),
    ("cnyes", "cnyes.com", "机器人"),
    ("cnbeta", "cnbeta.com.tw", "机器人"),
    ("gongkong", "gongkong.com", "机器人"),
    ("sina-tech", "tech.sina.com.cn", "机器人"),
]

if __name__ == "__main__":
    print("=" * 30, "URL 探测", "=" * 30, flush=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for line in ex.map(probe_url, URLS):
            print(line, flush=True)
    print("=" * 30, "Bing site: 定向检索", "=" * 30, flush=True)
    with ThreadPoolExecutor(max_workers=4) as ex:
        for line in ex.map(probe_bing, BING):
            print(line, flush=True)
