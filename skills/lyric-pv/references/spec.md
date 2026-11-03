# lyric-pv · spec.json 字段

> 时间单位秒，从颗粒 0 起算（= 坑位起点）。字号单位 px（按 1920×1080 写，其他画幅按比例换算）。

## 顶层

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `id` | ✓ | — | `data-composition-id` |
| `width` / `height` | | 1920 / 1080 | 画幅。竖屏直接写 1080 / 1920 |
| `duration` | ✓ | — | 时间线目标总长。MUST ≥ 坑位包络 + 0.3；最后一句驻留到这里 |
| `lines` / `lrc` | 二选一 ✓ | — | 歌词。`lines` 见下；`lrc` 是 LRC 文件路径（相对 spec 或绝对） |
| `line_overrides` | | — | 仅 `lrc` 时用：`{"行序": {字段…}}`，给第几句点名部件 / 强调词 / 段落 |
| `style` | | `ink` | `ink` 墨底 / `paper` 纸色衬线 / `mono` 白底 / `transparent` 透明叠加（字带描边） |
| `palette` | | 预设值 | 覆写 `bg` / `bg_alt` / `ink` / `accent` / `sub` / `band_ink` |
| `pattern` | | 随 style | 底纹 `stripes` / `grid` / `none`（随全片缓移） |
| `serif` | | 随 style | true 用衬线（思源宋体栈），false 用黑体 |
| `fonts` | | 思源栈 | `sans` / `serif` 两个 font-family 字面串 |
| `koma` | | 2 | 作画节奏：1 逐帧 / 2 一拍二（12 张/秒）/ 3 一拍三（8 张/秒） |
| `seed` | | 0 | 抽选种子。同 spec 同 seed ⇒ 逐字节一致 |
| `layouts` / `enters` / `exits` / `decors` | | 全部 | 收窄抽选范围（点名的不受限） |
| `transition` | | `none` | 句间转场的缺省值（第一句之前不转） |
| `decor_numbers` | | true | false 时不出大编号、索引、框角编号 |
| `font_size` / `min_font_size` | | 112 / 46 | 基准字号 / 自动缩字号的下限 |
| `max_cps` | | 20 | 读速上限（字/秒） |
| `margin` | | 0.06 | 安全框边距（画幅比例），字不得排出 |

## `lines[]`

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `t` | ✓ | — | 这句开始（登场起点）。严格递增 |
| `text` | ✓ | — | 歌词。`\|` = 断行处（显示时去掉）；竖排时是分列处 |
| `end` | | 下一句的 `t` | 这句结束（退场在此之前做完） |
| `emph` | | — | 强调词（逐字匹配），放大 1.55 倍、换强调色 |
| `part` | | 沿用上一句 | 段落号；切换时换底色 |
| `layout` / `enter` / `hold` / `exit` | | 抽选 | 点名部件，取值见 SKILL.md |
| `decor` | | 抽选 0–2 个 | 装饰数组 |
| `transition` | | 顶层 `transition` | `none` / `flash` / `wipe` |

抽选规则：相邻两句不抽同一布局；带 `emph` 的不抽 `band` / `ticker`；跑马灯只留不压字的装饰（`marks` / `bigno` / `index`）；
同词句子（副歌）没点名部件时复用第一次的全部部件。

## 时间

| 部件 | 时长 |
|---|---|
| 登场 | `rise` 0.5 · `pop` 0.45 · `wipe` 0.4 · `type` 0.06 × 字数（≤ 0.9）· `scramble` 0.55 · `drop` 0.55 · `zoom` 0.35 · `part` 0.45；且不超过这句时长的 45% |
| 退场 | `fade` 0.3 · `cut` 0 · `wipe` 0.3 · `scatter` 0.4 · `sink` 0.35 · `stretch` 0.3；不超过这句时长的 30%，在 `end` 前做完 |
| 转场 | `flash` 0.18 s 白闪；`wipe` 0.36 s 强调色块横扫，中点对准句子起点 |

## 生成器 stdout 摘要

```json
{"id": "...", "chars": 16197, "duration": 12, "style": "ink", "koma": 2, "seed": 3,
 "lines": [{"i": 0, "t": 0.4, "end": 2.0, "text": "凌晨四点的街灯", "layout": "split", "enter": "wipe", "hold": "tilt",
            "exit": "wipe", "decor": [], "transition": "none", "part": 0, "font_px": 112, "repeat_of": null}, ...],
 "out": "<绝对路径>"}
```

`repeat_of` 不为空 = 这句是副歌复现，部件复用了那一句的。
