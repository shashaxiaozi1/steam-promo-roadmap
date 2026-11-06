# CAPCOM Steam 美国区促销追踪

本目录追踪 `catalog.csv` 中固定的 73 个购买包。每次运行分别请求 Steam 商店的
`packagedetails?packageids=...&cc=us&l=english`，记录**当时观察到的**美元原价、现价、折扣。
不使用 Steam Web API Key。该商店接口没有公开的长期可用性承诺。

## 口径

- 2026-11-06 核对发行商页面的 82 款：原有 49 款 + 补入 24 款 = 当前 73 款付费游戏。另 9 个条目为 4 个免费/工具/试玩、4 个日本区 Z 版本和 1 个美国区停止本体购买（Street Fighter X Tekken），不计入分母。`excluded.csv` 也记录了早已排除的其他下架条目。
- Revelations 2 固定为 Deluxe Edition，Package ID `84647`；DMC5 固定为通常销售的 + Vergil，Package ID `613144`。新增 Resident Evil 5 当前可购 Gold Edition（`62118`）、Resident Evil 6 当前可购 Complete（`50980`）为明确标注的版本例外。
- 不根据 App 页面的 `price_overview` 猜版本，直接请求固定购买包，且核验其包含目标 AppID。
- 有任意购买包请求/价格失败，整次运行不更新 Power BI 表；失败的原始响应留作诊断。
- 每天两次：09:17、21:17 日本时间（cron 为 00:17、12:17 UTC）。GitHub 的实际启动可能延迟。
- **不补齐采样间隔，不声称知道折扣的准确开始/结束时间。** 每天热力图代表这一天任一成功采样发现促销；最大折扣为当天观测到的最大值。

## 发售日元数据

`catalog.csv` 还包含 49 款作品的 Steam 发售日，按 AppID 与 2026-10-02 的
`CAPCOM_Release_Presale_Analysis.xlsx` 研究表逐项匹配。该研究表以 Steam
`IStoreBrowseService/GetItems/v1/` 的登记时间为主；《Monster Hunter: World》采用
Steam 官方发售公告修正日期。每行的 `release_date_source` 记录来源。

- `steam_registered_at_utc`：原研究记录的 Steam 登记时间（UTC）。
- `steam_release_date_utc`：原研究核定的 UTC 发售日，保留例外修正。
- `steam_release_date_jst`：登记时间换算成日本时间后的日期，对应 Power BI 的 `date_jst`。

**当前仅补充主清单元数据**：采集与汇总程序尚未按发售日排除预售作品，
`daily_publishers.csv` 的分母仍是当天成功监测的全部购买包。
启用发售日口径及历史首次促销资格需要后续单独修改汇总逻辑；现有 Power BI CSV 格式未变。

## 数据文件

| 路径 | 内容 |
|---|---|
| `data/raw/<run_id>.jsonl` | 每次请求完整响应、请求 URL、响应头及观察时间 |
| `data/snapshots/<run_id>.csv` | 当次清单各一行的不可变采样快照 |
| `data/powerbi/snapshots.csv` | 所有历史采样，便于分析一天内变化 |
| `data/powerbi/daily_games.csv` | 日期 × 游戏；当天最高折扣、最低观察售价、采样次数 |
| `data/powerbi/daily_publishers.csv` | 日期 × 发行商；当天至少一次处于促销的游戏数 / 监测游戏数 |

新运行时写新 raw 和 snapshot 文件，随后重建三个 Power BI 用的 CSV。目录中所有历史采样文件保留在 Git；`snapshots.csv` 也保留旧 49 款原始观察值。\n`daily_games.csv` 和 `daily_publishers.csv` 只使用**覆盖当前完整 AppID 清单的运行**重算。\n因此第一次 73 款成功采样后，旧 49 款口径的日期会从两张热力图中消失，新口径从该日开始累计；切换前请勿把旧页面的比例与新页面直接比较。\n日后再次增减清单也会重新开始可比口径；价格历史不会被伪造或回填。

## 运行与验证

```bash
python3 capcom-price-tracker/collector.py
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
   将值格式设为百分比，并对值设置背景色条件格式：0 为浅灰、1 为深红；关闭合计。当前只会有 CAPCOM 一行。
2. **游戏层**：另一个矩阵，行=`game_name`，列=`date_jst`，值=`max_discount_percent`（最大值），
   背景色条件格式：0 为浅灰、100 为深红；在此页加 `publisher` 切片器。第一版只含 CAPCOM，未来新增发行商无需改版式。

如需表格显示当天是否促销，也可在游戏层使用 `is_on_sale_any`。两层的日期均按日本时间分组。
Power BI Service 发布之后，仍需在语义模型中单独配置**计划刷新**；GitHub 更新 CSV 不会主动推送到报表。
Power BI Desktop 的“刷新”可以先手动验证链路。历史 CSV 会随采样增加；此 MVP 暂不做两年滚动窗口或三日合并。
