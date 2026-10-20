# 罢工 · 游行 实时监测（法国 / 意大利）

盯着 5 个地方 + 两国全国层面的罢工、游行、示威，自动出看板，可推到微信。

- 本地：蒙彼利埃、米兰、佛罗伦萨、里米尼、圣马力诺
- 全国：法国全国、意大利全国

## 手机上用（不依赖电脑）

| 用途 | 地址 |
| --- | --- |
| 手机看板（随时打开，自动更新） | <https://alexis11-hub.github.io/strike-watch/> |
| 手机推送（订阅一次，之后自动收） | <https://ntfy.sh/lge-strike-7f2a9c41d5> |
| 运行记录（每 15 分钟一次） | <https://github.com/alexis11-hub/strike-watch/actions> |

推送怎么开：手机浏览器打开上面那个 ntfy 链接 → 点 **Subscribe** → 允许通知；
或装 ntfy App（iOS / Android）→ 添加主题 `lge-strike-7f2a9c41d5`。

- 抓取跑在 GitHub 的服务器上，电脑关机也照常工作
- 只有「7 天内开始的官方交通罢工」或「6 小时内的高危事件」才推送，不会刷屏
- 中国大陆网络可能打不开 ntfy.sh；要微信推送就填仓库 Secret `SERVERCHAN_KEY`（<https://sct.ftqq.com> 扫码拿 SendKey）

## 怎么用

双击 **`run.bat`**。

- 会弹一个黑窗口（别关，关了就不监测了），并自动打开浏览器看板 <http://127.0.0.1:8765/dashboard.html>
- 每 5 分钟自动抓一次，看板每 60 秒自动刷新
- 第一次启动约 10–20 秒出结果

只想抓一次、不要常驻：

```bat
python monitor.py --open
```

## 看板怎么读

| 位置 | 含义 |
| --- | --- |
| 顶部红框 | 近 72 小时的高风险事件（优先显示你所在城市） |
| 「未来两周 · 意大利交通罢工」 | 交通部官方日历里 14 天内的罢工，出行前看这条 |
| 标签页 | 全部 / 各城市 / 法国全国 / 意大利全国；条数是"影响该地"的数量，会重复计数 |
| 高危 / 关注 / 信息 | 有暴力冲突或全国总罢工＝高危；普通罢工、游行＝关注 |
| 📅 日期 | 官方罢工的起止日期；「3 天内」表示即将发生 |

搜索框可以查 `sciopero`、`grève`、`SNCF`、`机场` 等任意关键词。

## 数据源

| 来源 | 用途 |
| --- | --- |
| 意大利交通部 MIT 罢工日历 | 官方、结构化的交通罢工（最权威） |
| ANSA 伦巴第 / 托斯卡纳 / 艾米利亚-罗马涅 | 米兰、佛罗伦萨、里米尼 |
| Il Resto del Carlino · 里米尼 / La Nazione · 佛罗伦萨 | 本地报纸 |
| San Marino RTV / Libertas.sm | 圣马力诺 |
| ICI（France Bleu）埃罗省 / Midi Libre | 蒙彼利埃 |
| CGT / franceinfo / Le Monde / ICI 全国 | 法国全国 |
| ANSA 头条 / 全国新闻 / Il Fatto | 意大利全国 |

全部是公开 RSS，不需要 API key。17 个源并行抓取。

## 文件

```
strike-watch/
├─ monitor.py        主程序（抓取 → 过滤 → 看板）
├─ push.py           推送用：只在有 7 天内官方罢工/6 小时内高危时输出
├─ run.bat           双击启动
├─ dashboard.html    自动生成的看板
└─ data/
   ├─ events.json    全部事件（含 first_seen，用来判断"新增"）
   └─ sources.json   每个源这次抓取是否成功
```

## 想改城市 / 关键词

打开 `monitor.py` 顶部：

- `ZONES` — 城市清单（加一个城市：在这里加一条，并在 `CITY_WORDS` 里写它的地名变体）
- `SOURCES` — 数据源；`mode` 为 `city`（本市报纸，宽松）、`region`（大区通讯社，严格）、`national`（全国媒体，只在标题含罢工词时收）
- `OTHER_PLACES` — 出现这些地名又没命中你的城市时丢掉，防止外地新闻串进来
- `STRIKE_WORDS` / `PROTEST_WORDS` — 关键词（意大利语、法语都做了去重音处理）

改完重跑 `run.bat` 即生效。

## 微信推送（可选）

默认用 ntfy 推送（免费、免注册、手机装个 App 或网页订阅即可）。想改成微信：

1. 打开 <https://sct.ftqq.com> 用微信扫码，拿到 SendKey
2. 在本仓库 Settings → Secrets and variables → Actions 里加 `SERVERCHAN_KEY`

（本机原来那个 Hermes 定时任务已暂停，避免和云端重复推送。）



## 已知局限

- RSS 有几分钟到半小时延迟，不是秒级；真正的突发靠现场，不靠这个
- 意大利语 `manifestazione` 既指游行也指活动，已做上下文过滤，但仍可能漏/多
- 圣马力诺媒体少、罢工更少，多数时候是空的很正常
- 分类靠关键词，不做语义判断；标题党新闻可能被算成"关注"
