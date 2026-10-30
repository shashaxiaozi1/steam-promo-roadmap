# CAPCOM Steam 美国区促销追踪：最小版

本目录追踪 `catalog.csv` 中固定的 49 个购买包。每次运行分别请求 Steam 商店的
`packagedetails?packageids=...&cc=us&l=english`，记录**当时观察到的**美元原价、现价、折扣。
不使用 Steam Web API Key。该商店接口没有公开的长期可用性承诺。

## 口径

- 49 个付费购买包计入分母。`excluded.csv` 中的 3 款下架/仅合集游戏和 2 款免费本体不计入。
- Revelations 2 固定为 Deluxe Edition，Package ID `84647`；DMC5 固定为通常销售的 + Vergil，Package ID `613144`。
- 不根据 App 页面的 `price_overview` 猜版本，直接请求固定购买包，且核验其包含目标 AppID。
- 有任意购买包请求/价格失败，整次运行不更新 Power BI 表；失败的原始响应留作诊断。
- 每天两次：09:17、21:17 日本时间（cron 为 00:17、12:17 UTC）。GitHub 的实际启动可能延迟。
- **不补齐采样间隔，不声称知道折扣的准确开始/结束时间。** 每天热力图代表这一天任一成功采样发现促销；最大折扣为当天观测到的最大值。

## 数据文件

| 路径 | 内容 |
|---|---|
| `data/raw/<run_id>.jsonl` | 每次请求完整响应、请求 URL、响应头及观察时间 |
| `data/snapshots/<run_id>.csv` | 49 款各一行的不可变采样快照 |
| `data/powerbi/snapshots.csv` | 所有历史采样，便于分析一天内变化 |
| `data/powerbi/daily_games.csv` | 日期 × 游戏；当天最高折扣、最低观察售价、采样次数 |
| `data/powerbi/daily_publishers.csv` | 日期 × 发行商；当天至少一次处于促销的游戏数 / 监测游戏数 |

新运行时写新 raw 和 snapshot 文件，随后重建三个 Power BI 用的 CSV。目录中所有历史文件保留在 Git。
只有从本次开始采到的真实历史；第一天只有一个日期列，后续运行才逐步形成热力图。

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
