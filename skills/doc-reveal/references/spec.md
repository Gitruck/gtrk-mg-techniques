# doc-reveal · spec.json 字段

> 坐标与时间全部字面量；页面归一化矩形 `[x0, y0, x1, y1]` ∈ 0..1，以**顶页**（`pages[0]`）为参照。
> 屏幕锚点 `[ax, ay]` ∈ 0..1，按画幅换算。时间单位秒，从颗粒 0 起算（= 坑位起点）。

## 顶层

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `id` | ✓ | — | `data-composition-id`。派单模式 = `dispatch.mg[].composition_id`（`<工程slug>-<beatId>`） |
| `width` / `height` | | 1920 / 1080 | 画幅。MUST 等于目标工程画幅（gsap-emit v1 铁律 1 画幅注） |
| `duration` | ✓ | — | 时间线目标总长。MUST ≥ 坑位包络 `track_ed − track_st` + 0.3（铁律 7） |
| `bg` | | `dark` | `dark` / `paper` / `transparent` / `#RRGGBB`。`transparent` 时不产实心底层（overlay 用） |
| `palette` | | 预设值 | 覆写 `ink` / `ink_sub` / `accent` / `card` / `rule` / `page_bg` / `shadow`。栏目色从这里注入 |
| `fonts` | | 思源栈 | `sans` / `serif` / `latin_serif` 三个 font-family 字面串。字体名 MUST 命中渲染服务端字体表（铁律 6） |
| `pages` | ✓ | — | 页面图片路径数组（相对 spec 文件或绝对路径）。**第一项是顶页**，会被推近、承载标记 |
| `page_width` | | 620 | 页面在画布上的宽（px，镜头 scale=1 时）；高按图片比例 |
| `top_max_px` / `back_max_px` | | 1700 / 900 | 顶页 / 背页重编码的最大宽。zoom ≥ 2 时顶页 SHOULD ≥ 1600 |
| `quality` | | 82 | WebP 质量。超过字符预算会自动逐级降质降尺寸 |
| `stack` | | 见下 | 叠放几何 |
| `entrance` | | 见下 | 入场方式 |
| `focus[]` | | `[]` | 局部强调序列（镜头推近 + 标记） |
| `captions[]` | | `[]` | 屏幕空间的文案卡 / 小条 |
| `dust` / `smoke` | | 随预设 | 尘点 / 烟雾层开关（dark 预设默认开） |
| `hold_drift` | | `true` | 最后一个 focus 之后镜头微漂（终态驻留仍「活着」） |
| `dust_count` | | 22 | 尘点数量 |

## `stack`

| 字段 | 缺省 | 说明 |
|---|---|---|
| `x` / `y` | 0.62W / 0.52H | 顶页中心（px） |
| `fan` | `[-2, 5, -7, 9, -11, 13]` | 各页旋转角（deg），顶页在前。推近时顶页会转正到 0 |
| `offset` | `[16, 10]` | 每往下一页的位移（px） |

## `entrance`

| 字段 | 缺省 | 说明 |
|---|---|---|
| `mode` | `deal` | `deal` 发牌：从画外斜飞入、底页先落顶页最后；`stack` 叠放：从上方带缩放落到堆上 |
| `start` | 0.2 | 第一张落下的时刻 |
| `gap` | 0.32 | 相邻两张的间隔 |
| `dur` | 0.72 | 单张飞入时长 |
| `from` | `bottom-right` | 发牌来向：`bottom-right` / `bottom-left` / `top-right` / `top-left` |

入场结束时刻 = `start + gap × (页数 − 1) + dur`（生成器会在 stdout 摘要里回报 `entrance_end`）。

## `focus[]`

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `t` | ✓ | — | 镜头开始推近的时刻 |
| `rect` | ✓ | — | 顶页归一化矩形。用 `pdf_pages.py --find "<原文片段>"` 拿；截图页按像素 / 图片尺寸自己算 |
| `zoom` | | 2.2 | 镜头倍率 |
| `anchor` | | `[0.36, 0.5]` | 矩形中心落到屏幕的哪个点 |
| `dur` | | 1.1 | 推近时长 |
| `ease` | | `flow` | `flow` / `void` / `breathe` |
| `mark` | | `marker` | `marker` 荧光笔 / `box` 框 / `underline` 下划线 / `none` |
| `mark_at` | | `t + dur × 0.55` | 标记画入时刻 |
| `mark_dur` | | 0.55 | 标记画入时长 |
| `pad` | | `[8, 4]` | 标记相对矩形的外扩（px，横 / 纵） |

新 focus 的标记画入时，上一枚标记退到 0.4 不清空（状态演进，不归零）。

**避让公式**（决定 `anchor` 时用）：推近后顶页左边缘 `= ax × W − zoom × (rect 中心 x × page_w)`（取顶页无旋转、页左边缘为 0）。
文案卡放左栏时，让这个值 ≥ 卡右边缘 + 16px；放右栏同理算右边缘。

## `captions[]`

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `t` | ✓ | — | 出现时刻 |
| `variant` | | `card` | `card` 文案卡（accent 短横 + 标题 + 英文 + 元信息）/ `chip` 小条（左侧 accent 竖线） |
| `pos` | | `left` | `left` / `right` / `bottom`；或直接给 `x` / `y` |
| `y` | | 300（bottom 为 690） | 顶边（px）。内容底沿 MUST ≤ 0.85H（字幕带） |
| `w` | | 700（chip 560） | 宽（px） |
| `kicker` | | — | 小字眉题（字距开） |
| `title` | | — | 主标题（card 用衬线 900 / 48px；chip 用黑体 700 / 30px） |
| `en` | | — | 英文原题（拉丁衬线斜体） |
| `meta` | | — | 元信息（期刊 · 年份 · 作者……） |
| `from` | | 随 pos | 入场方向 `left` / `right` / `up` |
| `dur` | | 0.7 | 入场时长 |
| `until` | | — | 退场时刻（淡出 + 回撤 0.45 s）。只给**过渡性标签**（如机构小条在推近期刊刊头前让位）；终态主体（译名卡）不给，铁律 7 要它驻留到底 |

## 生成器 stdout 摘要

```json
{"id": "...", "chars": 815447, "duration": 11.7,
 "pages": [{"src": "p1.png", "orig": [1654, 2339], "bytes": 240600}, ...],
 "quality": 82, "entrance_end": 1.45,
 "focus": [{"t": 3.6, "end": 4.7, "zoom": 2.3}, ...],
 "captions": [{"id": "cap0", "t": 1.7}, ...], "out": "<绝对路径>"}
```

`chars` 是产物总字符数，硬预算 `--max-chars`（缺省 1,900,000；客户端颗粒上限 2,000,000 字符，超了会被静默拒挂）。
