# 机器人产业日报 · 国联民生研究所汽车组

面向客户的机器人产业日报产品（静态页面）：首屏是当日成品日报（报头 / 今日主线 / 重点新闻 TOP 3–5 / 值得跟踪的信号），下方保留国内新闻、海外新闻、机器人大厂、近期段子四个分类详表，支持搜索、时间与公司筛选、多方来源核对。

- 线上地址：<https://finn-tf.github.io/robot-news-board/>（仓库 <https://github.com/Finn-TF/robot-news-board>）
- 技术栈：纯静态 HTML + Python 标准库脚本，无需构建、无外部依赖。

## 日报是怎么生成的

每次抓取都会做同一套规则筛选，作为「候选池」：

1. **事件聚类**：用标题指纹（中文 2 字窗口 + 英文词，Jaccard ≥ 0.30）把同一事件的多家报道聚成一组；
2. **打分**：多家独立站点交叉印证 > 标题含量产/交付/订单/融资/上市/政策/财报等硬信息 > 发布时间最新 > 机器人本体与核心零部件词 > 头部机器人公司；
3. **去重**：同一标题反复出现、或同一家公司同一类事件（如「上市」）只保留一条；
4. **固定口径**：候选池最多 16 条，日报的重点新闻、跟踪信号**只能**从候选池里产生，候选池以外的内容不进入日报。

在此基础上：

- **规则模式（默认，无需 key）**：按打分排序取 3–5 条重点新闻；「影响判断」按事件类型套用固定口径（量产/融资/上市/政策/新品/组织调整），识别不了时明确写「暂无明确信号」；跟踪信号从量产、融资、上市、政策、财报、新品类条目中提取。
- **DeepSeek 模式（配置 key 后启用）**：把候选池按编号交给模型，使用固定提示词与固定 JSON 结构输出「今日主线 + 重点新闻（事件 + 影响判断）+ 跟踪信号」；脚本对输出做严格校验（编号必须来自候选池、不得重复、长度合规），不合格的条目直接丢弃，有效重点新闻不足 3 条则整体降级为规则模式。**链接一律取真实条目，不使用模型给出的链接。**
- 模型只能看到脚本给的候选条目，提示词明确禁止引入候选之外的公司、数字、订单、金额与来源，禁止目标价与投资评级。

日报写入 `data.json` 的 `report` 字段，页面直接渲染。

## 时间安排

| 任务 | 频率 | 北京时间 | GitHub cron（UTC） | 是否会调用 DeepSeek |
| --- | --- | --- | --- | --- |
| 新闻抓取（`fetch.yml`） | 每 30 分钟 | 每小时 00 / 30 分 | `*/30 * * * *` | 否，只更新列表与规则式日报 |
| 正式日报（`daily.yml`） | 每天一次 | 09:00 | `0 1 * * *` | 是（有 key 时），失败自动降级 |

- GitHub Actions 的 cron **只认 UTC**；北京时间 09:00 = UTC 01:00，所以写 `0 1 * * *`。
- 9:00 的正式日报会重新抓取一轮，用当天累积的最新候选池生成当日一期并归档，然后触发部署更新线上首页。
- 30 分钟的常规抓取**不会**调用 DeepSeek，避免无谓消耗；它只刷新板块列表，并在当天还没有正式日报时用规则式打底（已有 DeepSeek 日报则保持不变）。
- 若 9:00 那次 DeepSeek 调用失败（超时、限流、key 无效、JSON 解析失败等），自动用规则式生成当日日报并照常归档，不会出现「当天没有日报」；失败原因写入 `report.llm.error` 与 Actions 日志（不含 key）。
- 常规抓取也会写入当日快照：如果当天已经生成过 DeepSeek 版，常规轮次**不会覆盖**它——首页在当天剩余时间继续展示 9:00 的正式日报，下方列表随抓取实时更新；如果 9:00 任务因故没跑，当天至少仍有一份规则式日报可看。

## 归档机制

- 每期日报完整快照写到 `archive/YYYY-MM-DD.json`，索引写到 `archive/index.json`；
- 同一日期重跑只覆盖当日快照（当天日报滚动更新），**其他日期永不改写**，重新抓取不会覆盖历史日报；
- 期号按归档天数递增，同一天沿用同一期号；
- 页面右上角「往期」下拉可切换日期，默认最新一期；用 `?date=YYYY-MM-DD` 可直接分享某一期；
- 归档目录会随 Pages 一起发布（`deploy.yml` / `daily.yml` / `fetch.yml` 都会复制 `archive/`）；
- 本地双击打开（`file://`）时，浏览器无法读取 `archive/` 目录，只能看最新一期；要回看往期请用本地服务或线上地址。

## 接入 DeepSeek（用户自行配置 key）

### 第一步：先在本地确认 key 可用（推荐）

key 还没配到仓库前，可以先在本机自检，不写任何文件、不打印完整 key：

```powershell
# PowerShell（只对当前终端生效，关闭窗口即失效）
$env:DEEPSEEK_API_KEY = "sk-..."   # 换成你自己的 key，不要写进 config.json
python check_llm.py
```

- 成功会打印：`[成功] DeepSeek 连通，模型有正常返回。`，并显示模型回显与 token 用量；
- 失败会直接翻译原因：401 表示 key 无效/复制不完整，402 表示余额不足，404 表示接口或模型名不对，429 表示限流，超时表示网络/代理问题；
- 退出码：0 成功、1 未设置 key、2 调用失败；脚本不会把 key 写入任何文件或日志。

key 在 DeepSeek 开放平台 <https://platform.deepseek.com> 的 **API keys** 页面创建，**只在创建时显示一次**，关掉页面就看不到了，请当场复制。

### 第二步：把 key 加到 GitHub 仓库（图文步骤）

1. 打开仓库 <https://github.com/Finn-TF/robot-news-board>；
2. 点右上角 **Settings**（仓库设置，不是个人账号设置）；
3. 左侧栏找到 **Secrets and variables** → 点下面的 **Actions**；
4. 页面分两块：**Environment secrets** 和 **Repository secrets**。**只用下面那块 Repository secrets**，Environment secrets 不用管；
5. 点右下角绿色按钮 **New repository secret**；
6. **Name** 填：`DEEPSEEK_API_KEY`（全大写、下划线，前后不要有空格）；
7. **Secret** 粘贴你的 key（粘贴后输入框不会显示明文，这是正常的）；
8. 点 **Add secret**；
9. 回到列表，看到 `DEEPSEEK_API_KEY` 一行、右侧显示更新时间，就说明保存成功。

注意事项：

- 保存后**再也看不到 key 原文**，这是 GitHub 的正常设计；忘了只能删除重建；
- 如果页面显示 “This repository has no secrets.”，说明还没保存成功，重新走第 5–8 步；
- 名称必须完全一致 `DEEPSEEK_API_KEY`，写成 `DEEPSEEK-KEY`、`DEEPSEEK_API_KEY `（带空格）都不会生效；
- 不要用 Environment secrets 代替，本项目的 workflow 读取的是仓库级 secret。

### 第三步：确认配置并触发

`config.json` 中已预设：

```json
"llm": {
  "enabled": true,
  "provider": "deepseek",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com",
  "api_key_env": "DEEPSEEK_API_KEY",
  "max_tokens": 1600,
  "temperature": 0.3,
  "timeout": 60
}
```

- `enabled` 已为 `true`：secret 一旦配好，下一次每天 9:00（北京）的日报任务会自动使用 DeepSeek，**不需要改代码或再推一次**；
- 想立刻验证：Actions → **每日 9 点生成日报** → **Run workflow**；
- 看运行日志里的 `日报：DeepSeek（deepseek-chat）生成成功` 即成功；若显示 `DeepSeek 失败，降级为规则式（原因）`，按括号里的原因处理（见上面 401/402/404 的说明）；
- 成功后页面报头右侧会显示「DeepSeek 辅助生成 · deepseek-chat」，否则显示「规则口径」。

## 本地运行

**直接双击 `index.html`** 即可打开（页面读取同目录 `data.js` 的内嵌数据，含最新一期与归档索引）。

也可以用本地服务打开（能读到 `data.json` 并切换往期）：

```
python -m http.server 8765
# 浏览器访问 http://127.0.0.1:8765/index.html
```

常用命令：

```
python fetch_news.py                 # 常规抓取：更新列表 + 规则式日报 + 当日归档
python fetch_news.py --daily         # 正式日报：允许调用 DeepSeek，强制归档当日一期
python fetch_news.py --no-translate  # 不翻译，英文保留原文
python fetch_news.py --max 80        # 每板块最多条数（默认 60）
python fetch_news.py --sync-only     # 不联网，仅用当前 data.json 重新生成 data.js
python fetch_news.py --daemon        # 常驻定时抓取（间隔读 config.json，默认 30 分钟）
```

`config.json` 字段：

```json
{
  "interval_minutes": 30,
  "retries": 2,
  "max": 60,
  "per_source": 16,
  "translate": true,
  "llm": { "...": "见上" }
}
```

`interval_minutes` 常驻模式抓取间隔（分钟，默认 30）；`retries` 单轮失败重试次数；`max` 每板块最多条数；`per_source` 单通道每板块条数上限；`translate` 是否把英文标题/摘要翻成中文。

## 页面结构

1. **报头**：`机器人产业日报 · 第 N 期`、日期与星期、国联民生研究所汽车组、数据更新时间、往期下拉、通道状态；
2. **今日主线**：一句话概括当日机器人板块核心变化，并列出收录条数、多家印证条数、媒体数、通道成功率；
3. **重点新闻 TOP 3–5**：大卡片置顶，第一条高亮；每条含「事件 → 摘要 → 影响判断 → 来源链接」，标注「多家印证 · N 家 / 单一来源」；
4. **值得跟踪的信号**：量产/交付、订单、融资、上市、政策等前瞻信息，每条带日期与来源；
5. **分类详表**：国内新闻 / 海外新闻 / 机器人大厂 / 近期段子，保留原有 Tab、搜索、时间与公司筛选、核对徽标；
6. **页脚**：免责声明（通用占位，可替换为机构合规模板）、数据口径、抓取状态与来源清单。

## 抓取来源（共 78 个通道）

- **国内财经与科技媒体**：量子位、雷峰网、IT之家、钛媒体、36氪、虎嗅、机器之心、智东西、爱范儿、界面新闻、新浪科技、中国机器人网、OFweek 机器人；
- **国内检索**：Bing 中文新闻检索（人形机器人、机器人产业链、具身智能、优必选、机器人融资、机器人量产、外骨骼、政策、订单交付、行业研报、上市公司、机器人产业联盟、具身智能融资）；
- **海外机器人垂直媒体**：IEEE Spectrum Robotics、TechCrunch Robotics、The Robot Report、Robotics 24/7、Robohub、VentureBeat AI、The Verge、Engadget、WIRED AI、Ars Technica；
- **海外检索**：Bing 英文检索（humanoid robot、Figure AI、Boston Dynamics、robotics funding、humanoid deployment、dexterous hand、warehouse automation 等）；
- **大厂官方博客 / 新闻室**：OpenAI、NVIDIA、Google DeepMind、Google AI、Meta AI、Microsoft Research、Amazon Science、Apple Machine Learning、Boston Dynamics Blog；
- **大厂与头部公司检索**：OpenAI、英伟达、特斯拉 Optimus、DeepMind、Meta、宇树、优必选、智元、Figure、Boston Dynamics、Agility、1X、Physical Intelligence 等。

每个通道的成败逐条记录在 `data.json.source_report`，页面页脚可展开查看「原始条数 / 入库条数 / 失败原因」；抓不到的通道如实标红，不会用假数据填充。云端抓取受 GitHub 美国机房网络影响，国内部分站点（36氪、虎嗅、机器之心、界面等）可能返回 403/超时，属已知情况。

## 信息核对

每条资讯底部列出该事件的来源清单：同一事件在 **≥2 家独立站点（按域名判定）** 出现时标绿色「✓ 已核」；只有 1 家时标「单一来源」。

- 来源名可点击，直接打开对应原文；
- 核对结果只来自本轮真实抓到的条目，抓不到第二家就如实标单一来源，不会编造「已核」；
- `data.json` 中每条记录带 `verify` 字段（`level: cross/single`、`count`、`sources[]`）。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `index.html` | 日报页面入口，无外部依赖，双击可用 |
| `fetch_news.py` | 抓取 + 候选池 + 日报生成（规则 / DeepSeek）+ 归档 |
| `data.json` | 最新一期完整数据：`report`（日报）+ `boards`（分类详表）+ `source_report`（通道状态） |
| `data.js` | `data.json` 的内嵌副本 + 归档索引，供 `file://` 双击打开时使用 |
| `archive/YYYY-MM-DD.json` | 往期日报快照（完整数据） |
| `archive/index.json` | 归档索引（日期、期号、主线、板块条数） |
| `config.json` | 抓取间隔、条数、翻译开关、DeepSeek 开关 |
| `check_llm.py` | DeepSeek 连通性自检（读环境变量，不写文件、不打印完整 key） |
| `translate_cache.json` | 英文翻译缓存（本地运行加速，不提交仓库） |
| `daemon.log` | 常驻模式日志（运行后生成，不提交） |
| `update.ps1` | 一键 commit + push（Windows） |
| `.github/workflows/deploy.yml` | push 到 main 后发布 Pages |
| `.github/workflows/fetch.yml` | 每 30 分钟抓取 → 提交 → 发布 |
| `.github/workflows/daily.yml` | 每天 UTC 01:00（北京 09:00）生成正式日报 → 归档 → 发布 |

## 免责声明

页面页脚当前为通用占位表述：「本资料仅供交流参考，不构成投资建议」。该文字可由使用方替换为机构正式合规模板，直接修改 `index.html` 页脚 `.disclaimer` 区块即可；本仓库不包含任何机构正式合规条款。

## 已知限制

1. **微信公众号未直接抓取**：搜狗微信与 `mp.weixin.qq.com` 存在反爬与跳转校验，无法稳定拿到可核查的原文链接，公众号内容通过媒体 RSS 与检索通道间接覆盖。
2. **Google 新闻检索未使用**：其 RSS 链接是 `news.google.com` 跳转地址，不如 Bing 的原文直链可靠。
3. **部分站点反爬**：如 36氪、虎嗅、机器之心、界面、Robotics 24/7、Meta AI Blog 等可能 403/404/限流，脚本如实记录失败，不造假。
4. **翻译**：英文条目的中文标题/摘要调用 Google 翻译公开接口，机翻可能有偏差，页面保留英文原标题便于核对；接口不可用时保留英文原文。
5. **信息核对的边界**：只做「多家独立站点是否报道同一事件」的交叉印证，不等于事实核查；转载、通稿会被当作多来源，标题聚类也可能漏配，判断真伪仍需点开原文。
6. **规则式影响判断**：无 key 时「影响判断」是按事件类型生成的通用口径，不是针对具体公司的深度分析，页面会标明「规则口径」。
