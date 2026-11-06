# 机器人行业资讯看板

一个纯静态的机器人资讯看板：国内新闻 / 海外新闻 / 大厂重点 / 近期段子 四个板块，按发布时间倒序，支持关键词搜索与时间、公司筛选。

## 快速开始

**直接双击 `index.html`** 即可在浏览器打开（页面会自动读取同目录的 `data.js` 内嵌数据）。

也可以用本地服务打开（能读到最新的 `data.json`）：

```
python -m http.server 8765
# 浏览器访问 http://127.0.0.1:8765/index.html
```

## 部署到 GitHub Pages（在线访问）

当前状态：**已上线**，访问地址 <https://finn-tf.github.io/robot-news-board/>（仓库 <https://github.com/Finn-TF/robot-news-board>，Pages 构建方式为 GitHub Actions workflow，`deploy.yml` 已验证构建成功）。

下面保留首次建仓/推送的命令，供换机器或换仓库时参考。

```powershell
# 0) 先设置提交身份（只需一次）
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"

# 1) 首次提交
git commit -m "init: 机器人行业资讯看板"

# 2) 建公开仓库并推送（推荐，需要 gh）
winget install --id GitHub.cli      # 若没装 gh
gh auth login                       # 浏览器里授权
gh repo create robot-news-board --public --source=. --remote=origin --push

# 3) 把 Pages 切到 GitHub Actions 发布
gh api --method POST repos/{owner}/{repo}/pages -f build_type=workflow
```

不想装 `gh` 的话，第 2 步改成网页建仓后手动关联：

```powershell
# 在 github.com 新建一个 public 仓库（建议名 robot-news-board），然后：
git remote add origin https://github.com/<你的用户名>/robot-news-board.git
git push -u origin main
# 再到仓库 Settings → Pages → Source 选 "GitHub Actions"
```

部署完成后访问地址是：

```
https://<你的用户名>.github.io/robot-news-board/
```

注意：Pages **首次发布要等构建**（通常 1–2 分钟，慢时几分钟），在仓库的 **Actions** 标签页看到 `Deploy Pages` 变绿、并出现 `github-pages` 环境的 URL 才算真的上线；在此之前不要当作已发布。

**以后怎么更新**

- 本地改了数据或段子：`powershell -ExecutionPolicy Bypass -File .\update.ps1`（一条命令完成 commit + pull --rebase + push，提交信息默认带时间，可用 `-Message "..."` 自定义，脚本内部用 UTF-8 提交避免中文乱码）；
- 只改网页：同样跑 `update.ps1`，push 后 `deploy.yml` 自动重新发布；
- 想手动在网页上发布：Actions → Deploy Pages → Run workflow。

**换了仓库要改哪里**

- 只换地址：`git remote set-url origin <新仓库地址>`，workflow 不用改；
- 换默认分支：`deploy.yml` 与 `fetch.yml` 里的 `branches: [main]`、`ref: main` 以及 `update.ps1` 里的 `origin main` 一起改；
- 仓库名/用户名变了：只需更新本 README 里的访问地址，代码里没有硬编码仓库地址。

**云端定时抓取（`fetch.yml`）**

- 每小时第 5 分钟跑一次（GitHub cron 是 UTC，整点偏移不影响「每小时」），抓取后自动提交 `data.json` / `data.js` 并重新发布，本地电脑不用开机；
- 免费额度足够这个体量（每小时 1 次、单次约 1–2 分钟）；
- **这条路能否稳定跑通取决于 GitHub 美国机房的网络**：国内站点和 Bing 检索可能出现超时或被反爬，脚本会照常写出「失败通道」到 `data.json.source_report` 和 Actions 日志。若云端大面积失败，继续用本地 `python fetch_news.py` + `update.ps1` 兜底；
- 定时任务只会在仓库默认分支上按 cron 触发。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `index.html` | 页面入口，无外部依赖，双击可用 |
| `fetch_news.py` | 抓取脚本（仅用 Python 标准库，需 Python 3.8+） |
| `data.json` | 数据源文件：4 个板块的条目 + 抓取状态报告 |
| `data.js` | `data.json` 的内嵌副本，供 `file://` 双击打开时使用（浏览器会拦截本地 fetch） |
| `translate_cache.json` | 英文标题/摘要的翻译缓存，加速重复运行，可删除 |
| `config.json` | 定时抓取配置：间隔分钟数、重试次数、每板块条数、是否翻译 |
| `daemon.log` | 定时模式日志：每轮时间、各板块条数、失败通道（运行后自动生成） |
| `update.ps1` | 一键 commit + push（Windows，中文提交信息不乱码） |
| `.github/workflows/deploy.yml` | push 到 main 后发布到 GitHub Pages |
| `.github/workflows/fetch.yml` | 云端每小时抓取 → 提交数据 → 重新发布 |
| `config.json` / `.gitignore` / `.gitattributes` | 定时配置、忽略规则、换行符规则 |

## 更新数据

```
python fetch_news.py                 # 抓取 + 补摘要 + 翻译英文为中文，写入 data.json / data.js
python fetch_news.py --no-translate  # 不翻译，英文保留原文
python fetch_news.py --max 80        # 每板块最多条数（默认 60）
python fetch_news.py --sync-only     # 不联网，仅用当前 data.json 重新生成 data.js
python fetch_news.py --daemon        # 常驻定时抓取（间隔读 config.json）
python fetch_news.py --daemon --interval 30   # 临时把间隔改成 30 分钟
```

## 定时自动更新

两种方式，任选其一。

**方式一：内置常驻定时（`--daemon`）**

```
python fetch_news.py --daemon
```

- 按 `config.json` 的 `interval_minutes` 循环抓取，每轮结束自动写 `data.json` / `data.js`，刷新页面即可看到新数据；
- 每轮失败会用 `retries` 指定的次数重试（间隔递增），重试用尽只记录日志并保留上一份数据，**进程不退出**；
- 每轮开始前重新读取 `config.json`，改间隔不用重启进程；
- 日志同时打印到终端并追加到 `daemon.log`，格式为：`[时间] 本轮完成：国内新闻 x 条、海外新闻 y 条、大厂重点 z 条；失败通道 …；耗时 …s`。

`config.json` 字段：

```json
{
  "interval_minutes": 60,
  "retries": 2,
  "max": 60,
  "per_source": 16,
  "translate": true
}
```

`interval_minutes` 抓取间隔（分钟）；`retries` 单轮失败重试次数；`max` 每板块最多条数；`per_source` 单通道每板块条数上限；`translate` 是否把英文标题/摘要翻成中文。

**方式二：Windows 计划任务**

```
schtasks /create /tn "RobotNewsFetch" /tr "python C:\Users\27335\Documents\Trae-CN\temp_20261106_01\fetch_news.py" /sc hour /mo 1 /st 09:00 /f
```

- `/sc hour /mo 1` 表示每小时一次；改成 `/sc minute /mo 30` 即每 30 分钟；`/st` 是首次启动时间；
- 删除：`schtasks /delete /tn "RobotNewsFetch" /f`；
- 立即跑一次验证：`schtasks /run /tn "RobotNewsFetch"`。
- 注意：计划任务用的是系统默认 Python 路径，必要时把 `/tr` 里的 `python` 换成完整路径（如 `C:\Python313\python.exe`）。

需要能访问外网（Bing 新闻、各站 RSS、translate.googleapis.com）。运行时每个抓取通道的结果会打印在终端；失败的通道会写入 `data.json` 的 `source_report`，页面页脚「抓取状态与来源清单」里可以看到并标红。

**每次运行都会原样保留你手动写入的段子，不会覆盖。**

## 添加段子

编辑 `data.json` 里 `boards.jokes.items`（`items` 为数组），每条字段：

```json
{
  "title": "段子标题或一句话",
  "summary": "段子正文",
  "source": "来源（如：群聊/微博/知乎，可留空）",
  "published": "2026-11-06T15:00:00+08:00",
  "url": "可选，原始链接，没有就留空字符串",
  "is_placeholder": false
}
```

改完后：

- 用 `python -m http.server` 打开 → 刷新即可；
- 双击打开 → 需要先跑一次 `python fetch_news.py --sync-only`，把 `data.json` 同步到 `data.js`，再刷新。

当前段子板块的 3 条是**示例占位**（`is_placeholder: true`），会显示「示例占位」徽章；替换成真实内容后请把该字段设为 `false`，或直接删除占位条目。

## 抓取来源

- 国内：量子位、雷峰网、IT之家、钛媒体 RSS；Bing 中文新闻检索（人形机器人 / 机器人产业链 / 具身智能 / 优必选 / 机器人融资 / 机器人量产 / 外骨骼）
- 海外：IEEE Spectrum Robotics、TechCrunch Robotics、The Verge（机器人关键词过滤）；Bing 英文检索（humanoid robot / Figure AI / Boston Dynamics / robotics funding / Atlas 等）
- 大厂（机器人大厂）：OpenAI、NVIDIA、Google DeepMind 官方博客 RSS；Bing 检索覆盖 OpenAI / 英伟达 / 特斯拉 Optimus / DeepMind / Meta，以及头部机器人公司宇树 Unitree、优必选 UBTECH、智元 AgiBot、Figure、Boston Dynamics、Agility Robotics、1X、Physical Intelligence（共 41 个通道）。

条目入库规则：60 天内、关键词命中、标题去重、单来源限额。来源名称优先取原始媒体（Bing RSS 的 `<News:Source>`），页面上标「经 Bing」表示经检索通道获得。

**「大厂重点」口径（机器人大厂，不是泛科技大厂）**：条目必须与机器人强相关，才允许进该板块 ——

- 标题命中机器人词（人形机器人 / 具身智能 / 机械臂 / 灵巧手 / 四足 / 减速器 / 伺服 / VLA 等），或摘要出现强相关词（机器人本体、核心零部件、机器人量产/供应链、具身智能等）；
- 且标题或摘要点名「机器人大厂」公司；官方博客通道按来源公司归属；
- 只是顺带提一句机器人、或与机器人无关的科技新闻，会被分流到国内/海外板块或直接丢弃，不进「大厂重点」。
- 英文关键词按词边界匹配，并排除 `robots.txt`、RPA（robotic process automation）等误命中。

## 信息核对

每条资讯底部列出该事件的来源清单：同一事件在 **≥2 家独立站点（按域名判定）** 出现时标绿色「✓ 已核」；只有 1 家时标「单一来源」，表示未获其他媒体交叉印证。

- 来源名可点击，直接打开对应原文；鼠标悬停显示该来源的发布时间；
- 事件归并用标题指纹（中文 2 字窗口 + 英文词）计算相似度，阈值 0.30，只做聚类辅助，不改变原文标题与链接；
- 核对结果只来自本轮真实抓到的条目，抓不到第二家就如实标单一来源，不会编造「已核」。

`data.json` 中每条记录带 `verify` 字段（`level: cross/single`、`count`、`sources[]`），可按需二次开发。

## 已知限制

1. **微信公众号未直接抓取**：搜狗微信搜索与 `mp.weixin.qq.com` 链接存在反爬与跳转校验，脚本无法稳定拿到可核查的原文链接，因此没有纳入；公众号内容通过上述媒体 RSS 与检索通道间接覆盖。
2. **Google 新闻检索已移除**：其 RSS 链接是 `news.google.com` 跳转地址（需访问 Google 才能打开），不如 Bing 的原文直链可靠。
3. **The Robot Report 等站点 403**：部分站点对脚本请求做反爬，抓不到就不入库，也不会造假。
4. **翻译**：英文条目的中文标题/摘要调用 Google 翻译公开接口，机翻可能有偏差，页面保留英文原标题便于核对；接口不可用时该条保留英文原文。
5. **摘要**：来自 RSS 描述或文章页 `meta description` 摘录，缺失时页面显示「该来源未提供摘要」。
6. **信息核对的边界**：只做「多家独立站点是否报道同一事件」的交叉印证，不等于事实核查；同一稿件的转载、通稿会被当作多来源，标题聚类也存在漏配可能，判断真伪仍需点开原文。

## 托管

当前环境没有可用的在线托管账号/凭证（BytePlus Edge Pages 需要账号 + AK/SK + 开通 CDN），因此**未做在线托管**，交付为本地可双击打开的完整页面。如需分享，可把整个目录上传到任意静态托管（GitHub Pages / Vercel / 对象存储静态网站），`index.html` 无需构建。
