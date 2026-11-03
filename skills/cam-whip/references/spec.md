# cam-whip · spec.json 字段

> `duration`、拍点、`source.*`、动作的 `pre` / `post` 单位是秒（脚本按 `fps` 换算成帧，四舍五入，至少 1 帧）。
> 拍点一律是**颗粒时间**：从这段 mp4 的第 0 帧算起。

## 顶层

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `id` | | — | 只作标识 |
| `width` / `height` | | 1920 / 1080 | 画幅。MUST 等于目标工程画幅（竖屏照样支持） |
| `fps` | | 60 | 帧率 |
| `duration` | ✓ | — | 产物时长（秒） |
| `clips` | ✓ | — | 素材路径数组（相对 spec 或绝对），缩放裁切铺满画幅 |
| `hits` / `beats` / `bpm` | 三选一 ✓ | — | 拍点来源，写法同 `beat-pulse`（`beats` 支持 `file` / `every` / `phase` / `offset`） |
| `moves` | | `["whip"]` | 动作循环列表，见下 |
| `cut` | | `every` | `every` 每个动作都换镜头 / `none` 只用第一个素材 |
| `drift` | | 无 | `{"amp": 0.03}`：每段从 1 缓推到 1+amp |
| `handheld` | | 无 | `{"amp": 4, "rot": 0.2, "freq": 0.6}`：手持微震（两组正弦叠加，px / 度 / Hz） |
| `blur` | | `{"samples": 12, "shutter": 0.75}` | 运动模糊：子采样上限、快门（帧）。`samples: 1` 关掉 |
| `source` | | `{"lead": 0.3, "stride": 1.3}` | 跳过素材开头多少秒 / 同一素材每复用一次往后挪多少秒 |
| `seed` | | 0 | 选材顺序与手持微震相位的种子 |
| `crf` | | 16 | x264 质量 |

## `moves[]`

字符串简写：`"whip"`（缺省向左）、`"whip-left"` / `"whip-right"` / `"whip-up"` / `"whip-down"`、`"pull"`、`"tile"`。
要调参写对象，未给的字段取缺省：

| `type` | 字段（缺省） | 说明 |
|---|---|---|
| `whip` | `dir` `left`、`dist` 0.62、`rot` 6、`pre` 0.09、`post` 0.17 | `dist` 是画幅宽（左右甩）或高（上下甩）的倍数；出镜段 ease-in 三次方、入镜段 ease-out 三次方 |
| `pull` | `zoom` 1.9、`rot` 14、`anchor` [0.5, 0.5]、`pre` 0.12、`post` 0.24 | 出镜段推到 `zoom` 倍、入镜段从 `zoom` 倍拉回；`anchor` 是缩放中心（画幅比例）；转角方向在相邻两次拉镜间自动交替。`zoom` 1.05–4 |
| `tile` | `min` 0.5、`rot` 0、`pre` 0.14、`post` 0.22 | 出镜段缩到 `min` 倍露出镜像平铺、入镜段弹回。`min` 0.34–1 |

窗口：第 i 个动作占帧 `[h − pre, h + post)`。MUST 落在 `[0, 总帧数)` 内，且与相邻动作不重叠。

## 生成器 stdout 摘要

```json
{"out": "<绝对路径>", "frames": 270, "fps": 60, "duration": 4.5, "size_mb": 20.9,
 "motion_blurred_frames": 131, "blur_samples_max": 12,
 "hits": [{"frame": 30, "t": 0.5, "move": "whip", "dir": "left", "window": [25, 40]}, ...],
 "segments": [{"clip": "c01.mp4", "start": 0, "frames": 30, "offset": 0.3}, ...]}
```
