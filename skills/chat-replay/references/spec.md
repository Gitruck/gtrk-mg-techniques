# chat-replay · spec.json 字段

> 时间单位秒，从颗粒 0 起算（= 坑位起点）。坐标单位 px，按画幅给。
> 消息的位置**不用给**：脚本按顺序排版、算滚动；你只给「谁、说什么、什么时候」。

## 顶层

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `id` | ✓ | — | `data-composition-id`。派单模式 = `dispatch.mg[].composition_id` |
| `width` / `height` | | 1920 / 1080 | 画幅。MUST 等于目标工程画幅 |
| `duration` | ✓ | — | 时间线目标总长。MUST ≥ 坑位包络 + 0.3（铁律 7） |
| `bg` | | `light` | `light` / `dark` / `transparent` / `#RRGGBB`。`transparent` 不产实心底层（overlay 用） |
| `palette` | | 预设值 | 覆写配色，键见下表。拼错键名直接报错 |
| `fonts` | | 思源栈 | `sans`（气泡与界面）/ `serif`（文案卡标题）。字体名 MUST 命中渲染服务端字体表 |
| `font_size` | | 26 | 气泡正文字号（px）。其余尺寸（行高、头像、内边距、名字、系统提示）按它等比 |
| `frame` | | 见下 | 画框 |
| `stack` | | `top` | `top` 从视口顶往下排、排满才上推（像真实 App）/ `bottom` 最新一条始终贴视口底、每来一条整列上推 |
| `avatars` | | `true` | 是否画头像 |
| `names` | | 自动 | 是否在左侧气泡上方标名字。缺省：左侧参与者 ≥ 2 人（群聊）时标 |
| `participants` | ✓ | — | 参与者表，见下 |
| `messages` | ✓ | — | 消息序列，见下 |
| `focus` | | `[]` | 强调序列，见下 |
| `captions` | | `[]` | 屏幕空间的文案卡 / 小条，见下 |
| `entrance` | | 见下 | 画框入场 |
| `hold_drift` | | `true` | 最后一个动作之后画框轻浮（有限次 yoyo，按坑位算死） |
| `safe_bottom` | | 0.85 | 字幕带上沿（画幅高的比例）。消息视口底沿、文案卡底沿、推近后的目标气泡都不得越过。确认无字幕可设 1 |
| `allow_emoji` | | `false` | 已在真渲染引擎确认 emoji 能出字时才设 `true` |
| `quality` | | 84 | 头像 / 图片消息的 WebP 质量 |

## `palette` 键

| 键 | light | dark | 用在 |
|---|---|---|---|
| `canvas` | `#E9EBEF` | `#0C0D10` | 画布底（`transparent` 预设为空） |
| `screen` | `#F4F5F7` | `#14161B` | 画框内屏幕底 |
| `header` / `hd_rule` | 白 / 浅线 | 深灰 / 浅线 | 顶栏与输入栏底、分隔线 |
| `ink` / `ink_sub` | 近黑 / 灰 | 近白 / 灰 | 标题、名字、系统提示 |
| `bubble_in` / `ink_in` | 白 / 近黑 | 深灰 / 近白 | 左侧（对方）气泡与字 |
| `bubble_out` / `ink_out` | 蓝 / 白 | 蓝 / 白 | 右侧（我方）气泡与字 |
| `accent` | 琥珀 | 琥珀 | 强调描边、文案卡短横、`stamp` |
| `mark` | 半透明黄 | 半透明黄 | 荧光笔。我方气泡底色深时换成能压住的色 |
| `bezel` | 近黑 | 深灰 | 手机机身 |
| `field` / `send` | 浅灰 / 蓝 | 深灰 / 蓝 | 输入框底 / 发送键 |
| `sys_bg` | 半透明 | 半透明 | 系统提示底 |
| `card` / `rule` | 半透明白 | 半透明黑 | 文案卡底 / 线 |
| `bubble_shadow` / `frame_shadow` | box-shadow 串 | box-shadow 串 | 气泡与画框投影（只许 `box-shadow`，零卷积） |

## `frame`

| 字段 | 缺省 | 说明 |
|---|---|---|
| `kind` | `phone` | `phone` 竖长手机（机身 + 顶栏 + 输入栏）/ `panel` 桌面窗口（顶栏 + 输入栏）/ `none` 只有消息列（无顶栏无输入栏） |
| `x` / `y` / `w` / `h` | 随 kind | 画框外沿（px）。`phone` 缺省高 0.81H、宽 = 高 × 0.52、中心在 0.64W；`panel` 缺省 0.42W × 0.80H 靠右；`none` 720 × 0.78H 居中 |
| `title` / `subtitle` | — | 顶栏标题（群名 / 对方称呼）与副标题（人数 / 状态） |
| `stamp` | — | 顶栏右侧小签，如「示意」「情景还原」「已脱敏」。转写或还原的对话 MUST 给 |
| `header` / `input` | `true` | 设 `false` 去掉顶栏 / 输入栏（去掉输入栏后不能用 `compose`） |

画框整体 MUST 在画幅内；消息视口底沿 MUST ≤ `safe_bottom`。

## `participants`

`{ "<key>": { ... } }`，key 是你自己起的短名，`messages[].from` 引用它。

| 字段 | 缺省 | 说明 |
|---|---|---|
| `name` | = key | 显示名（群聊时标在气泡上方） |
| `side` | `left` | `left` 对方 / `right` 我方 |
| `avatar` | — | 头像图片路径（相对 spec 或绝对）。居中裁方。不给就画首字头像 |
| `initial` | `name` 首字 | 首字头像上的字 |
| `color` | 中性色轮换 | 首字头像底色 |

## `messages[]`

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `t` | | — | 出现时刻。**不给 = 开场已在屏上**（只能排在最前面，作为上文） |
| `kind` | | `text` | `text` / `image` / `system`（居中的时间、提示条） |
| `from` | text / image ✓ | — | `participants` 的 key |
| `text` | text / system ✓ | — | 正文。`\n` 显式换行；其余自动断行 |
| `src` | image ✓ | — | 图片路径。按显示尺寸的 2 倍重编码进颗粒 |
| `typing` | | 0 | 出现前「正在输入」的秒数（三点跳动气泡，到 `t` 换成正文） |
| `compose` | | 0 | 出现前在输入框里逐字打出的秒数（需要输入栏）。与 `typing` 二选一 |

相邻两条：后一条的 `t − typing/compose` MUST ≥ 前一条的 `t` + 0.2。最后一条的 `t` MUST ≤ `duration − 0.5`。
第一条（含它的 typing / compose）MUST 晚于画框入场落定。

## `focus[]`

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `t` | ✓ | — | 强调开始。MUST ≥ 目标消息出现 + 0.2 |
| `msg` | ✓ | — | `messages` 下标（0 起，含系统提示与开场消息） |
| `mark` | | — | 荧光笔划过的子串，**逐字匹配**原文第一次出现处；跨行会逐行依次划 |
| `ring` | | `true` | 目标气泡描边 |
| `dim` | | 0.32 | 其余气泡变暗到的不透明度；1 = 不变暗 |
| `zoom` | | 1 | 镜头倍率。> 1 时整个画框推近，目标气泡中心落到 `anchor` |
| `anchor` | | `[0.5, 0.46]` | 推近后目标气泡中心落到屏幕的哪个点（画幅比例） |
| `until` | | — | 让位时刻（复原变暗 / 描边 / 镜头）。不给则驻留到底；下一处 `focus` 开始时上一处自动让位 |

推近自检：目标气泡 MUST 不出画、不进字幕带；推近后的画框 MUST 不钻到当时在屏的文案卡底下（脚本报出 `anchor[0]` 下限）。

## `captions[]`

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `t` | ✓ | — | 出现时刻 |
| `variant` | | `card` | `card` 文案卡（accent 短横 + 衬线大标题）/ `chip` 小条（左侧 accent 竖线） |
| `pos` | | `left` | `left`（x 96）/ `right`；或直接给 `x` |
| `y` | | 300 | 顶边。估算内容底沿 MUST ≤ `safe_bottom` |
| `w` | | 600（chip 520） | 宽 |
| `kicker` / `title` / `meta` | | — | 眉题 / 标题 / 元信息 |
| `dur` | | 0.7 | 入场时长 |
| `until` | | — | 退场时刻。终态主体卡不给（铁律 7 要它驻留到底） |

文案卡 MUST NOT 与画框相交。

## `entrance`

| 字段 | 缺省 | 说明 |
|---|---|---|
| `mode` | `rise` | `rise` 从下方带一点缩放升起 / `fade` 淡入 / `none` 开场即在 |
| `start` | 0.1 | 开始时刻 |
| `dur` | 0.7 | 时长 |

## 生成器 stdout 摘要

```json
{"id": "...", "chars": 13642, "duration": 12.5,
 "frame": {"kind": "phone", "x": 1001, "y": 27, "w": 455, "h": 875},
 "viewport": {"x": 1013, "y": 131, "w": 431, "h": 681, "max_bubble_w": 305},
 "entrance_end": 0.8,
 "messages": [{"i": 2, "kind": "text", "from": "me", "t": 1.2, "lines": 1, "top": 224, "h": 63}, ...],
 "scroll": [{"t": 6.6, "y": -34}, ...],
 "focus": [{"k": 0, "msg": 4, "t": 9.4, "until": null, "zoom": 1, "mark": "..."}],
 "hold_from": 10.4, "out": "<绝对路径>"}
```

`lines` 是断成几行；`scroll` 是消息列在哪些时刻上推到哪；`hold_from` 是最后一个动作结束、开始驻留的时刻。
