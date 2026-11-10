# Steam 美国区促销追踪

本目录追踪经核验的 Steam 美国区游戏购买包：
`catalog.csv` 为 CAPCOM，`catalog_bandai.csv` 为 BANDAI NAMCO，
`catalog_konami.csv` 为 KONAMI，`catalog_koei_tecmo.csv` 为 KOEI TECMO，
`catalog_sega.csv` 为 SEGA，`catalog_square_enix.csv` 为 SQUARE ENIX；
第一批保留 Paradox Interactive 和 Valve；第二批加入 Focus Entertainment、2K、Rockstar Games 和 Warner Bros. Games。当前启用的发行商见 `publisher_config.py`。
每次运行分别请求 Steam 商店的
`packagedetails?packageids=...&cc=us&l=english`，记录**当时观察到的**美元原价、现价、折扣。
不使用 Steam Web API Key。该商店接口没有公开的长期可用性承诺。

## 口径

- 2026-11-06 核对发行商页面的 82 款：原有 49 款 + 补入 24 款 = 当前 73 款付费游戏。另 9 个条目为 4 个免费/工具/试玩、4 个日本区 Z 版本和 1 个美国区停止本体购买（Street Fighter X Tekken），不计入分母。`excluded.csv` 也记录了早已排除的其他下架条目。
- Revelations 2 固定为 Deluxe Edition，Package ID `84647`；DMC5 固定为通常销售的 + Vergil，Package ID `613144`。新增 Resident Evil 5 当前可购 Gold Edition（`62118`）、Resident Evil 6 当前可购 Complete（`50980`）为明确标注的版本例外。
- 不根据 App 页面的 `price_overview` 猜版本，直接请求固定购买包，且核验其包含目标 AppID。
- 已追踪购买包有请求或价格失败时，整次运行不更新 Power BI 表；失败的原始响应留作诊断。新作候选核验失败时单独记录，仍然发布已追踪清单的完整采样。
- 每天采价两次：09:17、21:17 日本时间（cron 为 00:17、12:17 UTC）。早间运行同时查新，手动运行也会查新。GitHub 的实际启动可能延迟。
- **不补齐采样间隔，不声称知道折扣的准确开始/结束时间。** 每天热力图代表这一天任一成功采样发现促销；最大折扣为当天观测到的最大值。

## 发售日元数据

`catalog.csv` 保留首批 73 款作品的 Steam 发售日。原有 49 款按 AppID 与 2026-10-02 的
`CAPCOM_Release_Presale_Analysis.xlsx` 研究表逐项匹配。该研究表以 Steam
`IStoreBrowseService/GetItems/v1/` 的登记时间为主；《Monster Hunter: World》采用
Steam 官方发售公告修正日期。新增 24 款的 UTC 日期取自各游戏的 SteamDB 页面，\n`steam_registered_at_utc` 留空；每行的 `release_date_source` 记录来源。

- `steam_registered_at_utc`：原研究记录的 Steam 登记时间（UTC）；新增旧作暂无该值。
- `steam_release_date_utc`：原研究核定的 UTC 发售日，保留例外修正。
- `steam_release_date_jst`：发售时刻换算成日本时间后的日期；新增 24 款按 SteamDB 的 UTC 时刻计算。

新作自动收录时，`steam_store_release_date` 记 Steam 美国区商店显示的发售日或待定文本；
商店日期不代表可验证的发售时刻，所以精确 UTC/JST 日期字段留空，且发售日不用于计算分母。

## 自动发现新作

- 每天通过 Steam 美国区商店搜索检查各发行商的已知名称，按 AppID 去重。对应排除表和主清单中的 AppID 不会被重新收录。
- 已确认发行商的付费本体，如即将推出或商店日期不早于该发行商的追踪起点，优先选唯一可核验的标准购买包。没有标准版时，对可识别的完整特别版按**美区原价**排序，选择原价最低的一版；独立 DLC、升级包、试玩和其他作品的捆绑包不参与选择。特别版的附属 App 必须能核验为本体 DLC；原价并列或无法确认时保留待核对。
- 选定购买包的美元价格须有效并与商店购买选项一致。首次采价成功后，主清单和当次数据一起提交。特别版的选择理由同时保存到清单的 `tracking_note` 和当轮查新报告；Power BI 导出列不变。
- 已开放美国区付费预购的本体可加入；仅有商店页的作品保留为 `pending`。旧作、版本不明确、地区或价格无法核验的候选留为 `review`，请查看 `data/discovery/latest.json`。免费本体、DLC 等非目标类型标为 `excluded`。
- 折扣比例从新作首次采价成功的日本日期起包含该游戏。当天若采到折扣，同时计入分子；以前的日期保留原来的分母。不会推断未观察到的价格或折扣。

## 数据文件

| 路径 | 内容 |
|---|---|
| `data/raw/<run_id>.jsonl` | 每次请求完整响应、请求 URL、响应头及观察时间 |
| `data/snapshots/<run_id>.csv` | 当次清单各一行的不可变采样快照 |
| `data/discovery/latest.json` | 最近一次查新的来源、候选和处理结果 |
| `data/powerbi/snapshots.csv` | 所有历史采样，便于分析一天内变化 |
| `data/powerbi/daily_games.csv` | 日期 × 游戏；当天最高折扣、最低观察售价、采样次数 |
| `data/powerbi/daily_publishers.csv` | 日期 × 发行商；当天至少一次处于促销的游戏数 / 监测游戏数 |

新运行时写新 raw 和 snapshot 文件，随后重建三个 Power BI 用的 CSV。历史采样保留在 Git；
`snapshots.csv` 仍保存旧 49 款观察值。每日表从首次 73 款完整采样（2026-11-06）开始，
每个日期根据当日成功采样中的不同 AppID 计算分母。自动扩容不会让旧 49 款口径重回报表，也不会移除已发布的 73 款日期。

## 运行与验证

```bash
python3 capcom-price-tracker/collector.py --discover
python3 -m unittest discover -s capcom-price-tracker/tests -v
```

GitHub Actions 工作流应放在**仓库根目录**的 `.github/workflows/capcom_prices.yml`。
只有它位于默认分支后，定时触发才会开始。也可以在 Actions 页 `Run workflow` 手动执行一次。
此项目使用仓库自带的 `GITHUB_TOKEN` 提交数据；若仓库设置禁止 Actions 写入，需要开启工作流的
`Read and write permissions`。请先确认已有的新快照被成功提交，才认为自动追踪已运行。

## Power BI Desktop：原生矩阵热力图

代码发布在 `shashaxiaozi1/steam-promo-roadmap` 默认分支后，通过 **获取数据 → Web** 分别读取以下固定 URL；
数据源凭据选择匿名（该仓库目前为公开仓库）：

```text
https://raw.githubusercontent.com/shashaxiaozi1/steam-promo-roadmap/main/capcom-price-tracker/data/powerbi/daily_games.csv
https://raw.githubusercontent.com/shashaxiaozi1/steam-promo-roadmap/main/capcom-price-tracker/data/powerbi/daily_publishers.csv
```

Power Query 把第一行提升为标题；`date_jst` 设为日期，`appid` 和 `package_id` 设为文本，
`max_discount_percent`、`games_monitored`、`games_on_sale` 设为整数，`sale_share` 设为小数/百分比。

1. **发行商层**：插入矩阵，行=`publisher`，列=`date_jst`，值=`sale_share`（汇总选择最大值），
   将值格式设为百分比，并对值设置背景色条件格式：0 为浅灰、1 为深红；关闭合计。
2. **游戏层**：另一个矩阵，行=`game_name`，列=`date_jst`，值=`max_discount_percent`（最大值），
   背景色条件格式：0 为浅灰、100 为深红；在此页加 `daily_games[publisher]` 切片器。

如需表格显示当天是否促销，也可在游戏层使用 `is_on_sale_any`。两层的日期均按日本时间分组。
Power BI Service 发布之后，仍需在语义模型中单独配置**计划刷新**；GitHub 更新 CSV 不会主动推送到报表。
Power BI Desktop 的“刷新”可以先手动验证链路。历史 CSV 会随采样增加；此 MVP 暂不做两年滚动窗口或三日合并。

## 万代南梦宫与下架处理

- 万代南梦宫以 Steam 商店 Publisher 署名为准，D3PUBLISHER 等单独主体不并入。首批清单 127 款，包括明确选择的《Tales of ARISE》Beyond the Dawn Edition（954140）与《DARK SOULS II》DX9 Bundle（58135）。Star Trek（203250）留在排除表，不采价。尚未开售的作品可继续由查新发现。
- 每天 09:17 JST 的任务对各发行商的有效 AppID 检查美国区购买选项；每天 09:17、21:17 JST 均采价。手动运行也检查购买选项。
- 最近五个日本日期里，三个不同日期的有效 Steam 响应都明确显示美国区不可购，且同发行商其他作品的响应通过健康检查，才将该 AppID 从有效清单移入 `data/lifecycle/` 记录。一次缺价、限流、网络故障和仍存在其他购买包均不触发移除；批量异常会暂停下架判断。重新上架需复核购买包后手动恢复。
- 从确认下架的日本日期起，不再计入该发行商当日分子和分母；原始价格记录与此前日期保留。有效清单中仍有作品采价失败时，不发布该发行商该轮的部分汇总。另一发行商可独立更新。
- 各发行商使用单独的 Actions runner，依次写入相同的 Power BI 日表，原有地址和列不变。新增发行商从首个有效采样日开始出现，未采样日期不填 0。

## KONAMI 与 KOEI TECMO

- 首批清单用已有 AppID 与 Steam 美国区搜索结果核对。以商店 Publisher 署名为准：`KONAMI` 和 `KOEI TECMO GAMES CO., LTD.`。不因开发商或母公司相同而并入其他发行商的游戏。
- 2026-11-10 首批核对了 64 个 KONAMI 候选、116 个 KOEI TECMO 候选，分别收入 53 和 107 个标准购买包。共享一个购买包的附属 AppID 不单独计数。
- 付费预购按普通采价处理；观察到折扣即计入促销。免费本体、试玩和非本体内容不计入。核对结果见 `publisher_onboarding_20261110.csv`。
- KOEI TECMO 另补入已确认的 `DYNASTY WARRIORS 9 Special Weapon Edition`（414762）、`WARRIORS OROCHI 4 Ultimate`（426467）及 `NINJA GAIDEN: Master Collection`（575623）。合集的三个 AppID 只按一个购买包计数。

## SEGA 与 SQUARE ENIX

- 按 Steam 商店的 Publisher 署名归属，署名含 `SEGA` 或 `Square Enix` 的作品分别纳入；仅为开发方或仅由其他发行方署名的作品不并入。审核来源是已有 AppID 与 Steam 美国区搜索结果。
- 2026-11-10 首批核对了 126 个 SEGA 候选和 166 个 SQUARE ENIX 候选，分别纳入 107 和 146 个可验证的购买包。
- 首批购买包、版本例外和暂不纳入的原因见 `publisher_onboarding_sega_square_enix_20261110.csv`，长期版本说明保存在各自的 catalog。

## 后续发行商

新增一家时，在 `publisher_config.py` 配置 Steam 发行署名、自动发现起点和文件名词干，并提交核验过的 catalog 与排除表。GitHub Actions 从同一份配置生成采价任务。每家用独立 runner，按顺序发布现有的 Power BI 数据表，避免覆盖其他发行商刚写入的数据。

| 批次 | 发行商 |
|---|---|
| 1 | Paradox Interactive、Valve |
| 2 | Focus Entertainment、2K、Rockstar Games、Warner Bros. Games |
| 3 | Electronic Arts、Ubisoft、Activision、KRAFTON, Inc. |
| 4 | Bethesda Softworks、Xbox Game Studios、PlayStation Publishing LLC |

第一批保留 Paradox Interactive 72 款、Valve 18 款，详见 `publisher_onboarding_west_batch1_20261110.csv`。

第二批核对了 231 个 Steam AppID，纳入 Focus Entertainment 72 款、2K 79 款、Rockstar Games 13 款、Warner Bros. Games 51 款。详见 `publisher_onboarding_west_batch2_20261110.csv`。后续批次是待核验清单，不会自动启动。

永久免费游玩（Free to Play）的本体不采价，也不进入促销率分母；付费本体限时降到零仍属于促销。
