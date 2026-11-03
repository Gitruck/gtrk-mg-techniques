# candle-chart 示例

`synthetic-daily.csv` 是 120 根**合成**日线（固定种子的随机游走：先涨、回撤、再缓升），不对应任何真实标的。
两份 spec 都引用它，拿来就能跑：

```bash
python ../scripts/candle_chart.py daily-marks-dark.spec.json --out /tmp/daily.html
python ../scripts/candle_check.py /tmp/daily.html --out /tmp/daily-sheet.png --video /tmp/daily.mp4
```

| 文件 | 底 / 配色 | 演示了什么 |
|---|---|---|
| [`daily-marks-dark.spec.json`](daily-marks-dark.spec.json) | `dark` / 红涨绿跌 | 120 根日线 6 秒长完，MA5 / MA20 笔尖跟随；随后标阶段高点与阶段低点、亮起两点之间的回撤区间、在前高画压力线 |
| [`weekly-levels-light.spec.json`](weekly-levels-light.spec.json) | `light` / 绿涨红跌 | 同一份数据 `aggregate: 5` 并成 24 根周线、不画量；均线在蜡烛长完后单独描出；支撑线与压力线先后划出；标出最低的一周收盘 |

两份标题都写了「合成数据，非真实行情」——用合成数据做示意时 MUST 这样标。

## 换成你的数据时

- 先跑一遍看 stdout 的 `extremes`，标签里的价位从那里取（写错了脚本会拒绝）。
- 区间文字里的涨跌幅自己算：`(区间末 − 区间首) / 区间首`，用你在口播里说的那个口径（收盘对收盘，还是高点对低点）。
- 根数太多脚本会让你 `aggregate` 或 `slice`；配色约定跟着受众走（`cn` / `us`）。
