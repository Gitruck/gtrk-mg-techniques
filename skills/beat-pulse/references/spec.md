# beat-pulse · spec.json 字段

> `duration`、拍点、`source.*` 单位是秒；效果的 `len` 单位是**帧**（按 `fps`）。
> 拍点一律是**颗粒时间**：从这段 mp4 的第 0 帧算起。

## 顶层

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `id` | | — | 只作标识 |
| `width` / `height` | | 1920 / 1080 | 画幅。MUST 等于目标工程画幅 |
| `fps` | | 60 | 帧率。卡点建议 ≥ 50：30fps 下一帧 33 ms，冲击的「顶点」容易显得钝 |
| `duration` | ✓ | — | 产物时长（秒）= 坑位长度 |
| `clips` | ✓ | — | 素材路径数组（相对 spec 或绝对）。会被缩放裁切铺满画幅 |
| `hits` / `beats` / `bpm` | 三选一 ✓ | — | 拍点来源，见下 |
| `min_gap` | | 3 | 相邻两拍最少隔几帧，少于就拒 |
| `accent` | | 无 | 重拍：`{"every": 4, "phase": 0}` / 拍点下标数组 / `"downbeats"` |
| `fx` | | `punch` | 预设名 `punch` / `glitch` / `breathe`，或 `{"normal": [...], "accent": [...]}`（`accent` 缺省同 `normal`） |
| `cut` | | `every` | `every` 每拍切 / `accent` 只在重拍切 / `none` 不切（只用第一个素材） |
| `source` | | 见下 | 从素材的哪一段取 |
| `seed` | | 0 | 震动方向、条带位置、选材顺序的种子。同 spec 同 seed ⇒ 逐帧一致（实测成品逐字节一致） |
| `crf` | | 16 | x264 质量。冲击帧高频多，别高于 20 |

## 拍点来源

| 字段 | 写法 | 说明 |
|---|---|---|
| `hits` | `[0.5, 1.25, 2.0]` | 秒列表，原样用 |
| `beats` | `[0.35, 0.82, ...]` 或 `{"list": [...]}` 或 `{"file": "beats.json"}` | 文件里要有 `beats`（秒数组），可选 `downbeats`；也认外层包一层 `output_result` 的回执 |
| `beats.every` / `beats.phase` | 1 / 0 | 从第 `phase` 拍起每 `every` 拍取一拍 |
| `beats.offset` | 0 | 从每个拍点里减去的秒数（把歌曲时间换算成颗粒时间） |
| `bpm` | `120` 或 `{"bpm": 120, "offset": 0.5, "every": 1}` | 均匀网格：第一拍在 `offset` 秒，每 `60/bpm × every` 秒一拍 |

时长外的拍点丢弃；换算成帧号时四舍五入到最近一帧。

## `source`

| 字段 | 缺省 | 说明 |
|---|---|---|
| `lead` | 0.3 | 跳过素材开头多少秒 |
| `stride` | 1.3 | 同一素材每复用一次往后挪多少秒（在素材长度内取模） |

## 效果（配方里的每一项）

所有效果在拍点那一帧幅度最大，`len` 帧内按 (1 − k/len)^2.2 衰减到 0。

| `type` | 参数（缺省） | 画面 |
|---|---|---|
| `shake` | `amp` 20 px、`len` 8、`rot` 1.2° | 每帧换一个方向的位移 + 微旋转；越界处镜像补边 |
| `zoom` | `amp` 0.08、`len` 10、`shape` `punch` | `punch`：拍点那帧放大到 1+amp 再回落；`breathe`：以拍点为中心、前后各 len/2 帧的正弦鼓起 |
| `flash` | `amp` 0.5、`len` 6、`color` `#FFFFFF` | 向 `color` 混合；`#000000` 即黑闪 |
| `rgb` | `amp` 12 px、`len` 8 | R 通道左移、B 通道右移各 amp px |
| `invert` | `len` 2 | 前 len 帧负片（硬切，不衰减） |
| `blur` | `amp` 0.04、`len` 8 | 放射模糊：同中心 4 层放大取平均，amp 是最外层多放大的比例 |
| `glitch` | `amp` 60 px、`len` 6、`bands` 12 | 随机横条带水平错位（位置与偏移由种子决定） |

叠加顺序：变换（推 / 震）→ 放射模糊 → 条带 → 色散 → 负片 → 闪。

## 预设

| 预设 | `normal` | `accent` |
|---|---|---|
| `punch` | zoom 0.07/10，shake 16/8 | zoom 0.13/12，shake 30/10，flash 0.55/6，rgb 10/8 |
| `glitch` | rgb 14/6，glitch 40/5 | invert 2，rgb 22/8，glitch 90/8，shake 20/6 |
| `breathe` | zoom breathe 0.05/16，blur 0.025/8 | zoom breathe 0.09/20，flash 0.3/8，blur 0.05/10 |

## 生成器 stdout 摘要

```json
{"out": "<绝对路径>", "frames": 360, "fps": 60, "duration": 6, "size_mb": 15.3,
 "hits": [{"frame": 30, "t": 0.5, "accent": true, "fx": ["zoom", "shake", "flash", "rgb"]}, ...],
 "segments": [{"clip": "c01.mp4", "start": 0, "frames": 30, "offset": 0.3}, ...]}
```
