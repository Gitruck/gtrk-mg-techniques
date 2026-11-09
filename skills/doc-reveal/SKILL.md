---
name: doc-reveal
description: 文献 / 文档页「叠放 · 发牌 · 局部强调」MG 技法——把论文首页、新闻截图、书页、聊天记录截图等多页图片落成一叠纸，镜头推近某一行或某张图，荧光笔划过，旁边落一张译名 / 出处文案卡；产出 gsap-emit v1 合规、可逐帧 seek 的 html-particle 颗粒（可进 gtrk 工程铺轨或独立出透明 MOV）；合成里用时（内置 AI、新工程推荐）页面作合成的素材槽位，不内联进颗粒。当用户想「引用这篇论文 / 文献展示 / 把这段截图圈出来放大 / 新闻截图叠放 / 发牌式亮出几页资料 / paper citation card」时使用本 skill。栏目无关：栏目色、字体由调用方注入，本 skill 只提供技法与中性底预设。
---

# doc-reveal · 文献页叠放 / 发牌 / 局部强调

三个动作，一条时间线：

| 动作 | 画面 | 用在什么时候 |
|---|---|---|
| **叠放 stack** | 几页纸从上方带一点缩放依次落到桌面上，越落越厚 | 「资料摆上桌」——引出一份档案、一叠证据 |
| **发牌 deal** | 几页纸从画外斜飞入、底页先落顶页最后，落成一把扇 | 「亮出来」——比叠放更有势能，配口播里的「发表了一篇……」 |
| **局部强调 focus** | 顶页转正、镜头推近到一行 / 一段 / 一张图，荧光笔（或框、下划线）划过；旁边落文案卡 | 「看这里」——标题、作者、期刊、关键图；说到哪就推到哪 |

> 参考片（B 站「口播素材动画」三种）就是这三样：第一种叠放、第二种发牌、局部强调。本 skill 把它们做成一份 spec 驱动的确定性时间线。

## 分工：你决定内容与节奏，脚本只做机械活

**你（agent）负责**：哪几页、哪页当顶页、强调哪几处（矩形从原文找）、每一步落在口播的哪一秒、文案卡写什么、用哪套底。
**脚本负责**：图片重编码进预算、页面几何、镜头推近的数学、标记与文案卡的入场、尘点 / 烟雾 / 微漂的有限循环、gsap-emit 发射。

这是**技法**不是模板：脚本里没有示例文案、没有栏目色板、没有默认的强调位置。同一份脚本给不同口播产的颗粒应当长得不一样——差别就在你写的 spec。

## 工作流

> 下面 1–6 步是**颗粒做法**：外部 Agent、旧工程可用；页面图会内联进颗粒，受 `gtrk mg lint` 的 `4-html-size` 管（整颗 2,000,000 字符上限）。
> 第 1–3 步（拿页面图、定强调矩形、按口播定节奏）两种做法通用；**内置 AI 与新工程**做完第 3 步改走下一节「在合成里用」。

### 1. 拿页面图片

- **PDF**：`python scripts/pdf_pages.py <file.pdf> --pages 1-3 --dpi 200 --out <目录> --prefix <名>`（依赖 pymupdf）。
  200 dpi 的 A4 ≈ 1654 px 宽，够 zoom 2.6 不糊。
- **截图**（新闻 / 聊天 / 网页）：直接用 PNG / JPG；先裁掉浏览器边框，歪的先摆正。
- 页数 2–5 页。第一页是顶页（承载全部 focus 与标记），其余是垫在下面的背页，只求「看得出是同一份资料」。
- 只用你有权展示的页面：论文首页作引用、截图作评论是常规做法；整篇翻页不是。
- 页面图片属**输入素材**，放工程目录（如 `<工程>/inputs/papers/`），别放 agent 自有目录。

### 2. 定强调位置（矩形）

- PDF 有文字层：`pdf_pages.py ... --find "<原文片段>" --find "<作者名>"` 直接返回顶页归一化矩形，逐字精确。
- 截图：按 `像素 / 图片尺寸` 手算 `[x0, y0, x1, y1]`。
- 一个 beat 里 focus **不超过 3 处**，每处停留 ≥ 2.2 s；多了观众跟不上，也失去「看这里」的分量。

### 3. 按口播定节奏（最要紧的一步）

拿句级时码（`transcript.json` 的 `st/ed`）按**字数内插**估某个词的时刻：`t_word ≈ st + (词在句中的字序 / 句总字数) × (ed − st)`，再减去颗粒起点 `track_st` 得到颗粒内时刻。经验值：

- 入场（叠放 / 发牌）压在**引出**这份资料的那半句上（「……发表了一篇」），入场结束 ≤ 2 s。
- 每个 focus 的 `t` = 说到那个东西的时刻 **− 0.3 s**（镜头先动，字随后到）；`mark_at` 比 `t` 晚 0.6–0.8 s（推近落定再划）。
- 文案卡（译名 / 出处）在口播**念完那句**的时刻落下，然后**驻留到坑位末尾**——它是这颗粒的终态主体。
- 过渡性小条（机构 · 年份）可以给 `until` 让位——典型场景：接下来要推近页面**远端**（如右上角的期刊刊头），推近后整页会盖住文案栏，小条先退场，主体卡等镜头回来再落。
- 最后一个 focus 之后不再大动：镜头微漂 + 最后一枚标记轻呼吸就够（铁律 7：定格是合法终态）。

### 4. 写 spec

字段全表见 `references/spec.md`。要点：

- `id` = 派单的 `composition_id`；`duration` ≥ 坑位包络 `track_ed − track_st` + 0.3。
- `bg`：`dark`（黑底烟雾尘点，最接近参考片）/ `paper`（奶白档案桌面）/ `transparent`（叠在底轨上的 overlay）/ `#RRGGBB`。
  **栏目有自己的底色与强调色就用 `palette` 注入**，别改脚本预设。
- **避让**：文案卡通常放左栏（x 96–696），推近后的顶页左边缘要落在卡右侧：`ax × W − zoom × (rect中心x × page_width) ≥ 卡右边缘 + 16`。算不清就先生成、看接触表、再调 `anchor`。
- 文案卡内容底沿 ≤ 0.85 H（底部 15% 是字幕带）。
- spec 落在产物旁边（如 `<工程>/mg/<composition_id>.spec.json`），日后改节奏只改 spec 重生成。

### 5. 生成 → lint → 预检

```bash
python scripts/doc_reveal.py <spec.json> --out <工程>/mg/<composition_id>.html
gtrk mg lint <工程>/mg/<composition_id>.html --dispatch <工程>/split/dispatch.json
python scripts/seek_check.py <工程>/mg/<composition_id>.html --times 0.6,1.5,2.4,4.4,7.6,11.5 --out <系统temp>/sheet.png
```

- 产物**直接写最终落点**（派单模式 = `<工程>/mg/<composition_id>.html`），不经任何中转目录。
- 生成器 stdout 的 `chars` 必须 < 1,900,000（客户端颗粒上限 2,000,000 字符，超了会被**静默拒挂**）；脚本会自动降质保预算，仍超就减页 / 降 `top_max_px`。
- lint 的 `x-soft-alpha` 是信息项（本技法必有半透明：阴影、卡底、荧光笔），不用改；`7-*` / `4-*` / `1-cid-expect` 出现就回去改 spec。
- 预检接触表**看三件事**：入场落定的堆够不够大、推近后的页有没有压住文案卡、终态那帧是不是「资料 + 卡」都在。
- **预检不是验收**：本地 playwright 挂载测不出 var()-不解析 / CDN 内联失败 / StaticGuard；真验收 = 真渲染引擎抽帧（`gtrk render` / `gtrk mg render`）。

### 6. 交回铺轨

铺轨不在本 skill 内——派单模式交回 `/gtrk-mg`（`gtrk mg --project`），独立模式走 `gtrk mg render <html> --duration <s>` 出透明 MOV 拖进剪辑软件。

## 在合成里用（内置 AI 与新工程的推荐做法）

资料页、截图是**要看清的大图**：按画面三层（底 / 版 / 叠）它归**版**，用 `/gtrk-composite` 做——页面作合成的**素材槽位**，由客户端 / 烘焙按原图画进去，不内联进颗粒、不受 `4-html-size` 管。这一段窗口要在拆分稿里开成版式窗口（`layouts[]`），精剪包装用 `gtrk composite plan --mode split` 取它（不走拆分的工程用 `--range` 指定区间）。上面第 1–3 步照做（页面图、强调矩形、节奏点），第 4–6 步换成下面这些；`scripts/doc_reveal.py` 在这条路上不用。

### a. 页面登记成 `own` 槽位

页面图放工程目录（如 `<工程>/inputs/papers/p1.png`；联网取回的在 `<工程>/web/` 下），写进 `<工程>/composite/materials.json`。`path` 写绝对路径（相对路径按 `.gtrk` 所在目录、即 `<工程>/gtrk/` 解析，不是按清单所在目录）：

```json
[{ "id": "doc-p1", "path": "D:/工程/inputs/papers/p1.png", "duration": 11.7 },
 { "id": "doc-p2", "path": "D:/工程/inputs/papers/p2.png", "duration": 11.7 }]
```

场景 spec 里每页一个槽位（图片没有入点，`src_in_ms` 留 0，`dur_t` 给窗口长度）：

```jsonc
"slots": {
  "p1": { "bind": { "kind": "own" }, "label": "顶页", "pin": { "material": "doc-p1", "src_in_ms": 0, "dur_t": 11.7 } },
  "p2": { "bind": { "kind": "own" }, "label": "背页", "pin": { "material": "doc-p2", "src_in_ms": 0, "dur_t": 11.7 } },
  "subs": { "bind": { "kind": "text", "source": "subtitles" }, "label": "字幕" }
}
```

⚠️ **图片槽位要新版两端**：新版 gtrk（`lint`）与新版即剪客户端（预览）都认图片槽位，烘焙一直认。用户的客户端还是旧版时预览里页面会空着（出片不受影响）；要在旧版上预览，照 `/gtrk-composite`「已知限制」第 1 条先把每页图转成与窗口等长的静帧视频，再按视频素材登记。例：

```bash
ffmpeg -loop 1 -i p1.png -t 11.7 -r 30 -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:v libx264 -pix_fmt yuv420p p1.mp4
```

### b. 场景怎么写（坐标一律是舞台像素）

舞台 = 工程画布（横屏 1920×1080、竖屏 1080×1920，`scaffold` 写好）。页面框的宽高比 = 页面图的宽高比（A4 约 1 : 1.414），`data-fit="cover"`，这样不留边也不被二次裁切。槽位只有轴对齐的矩形几何（位置、大小、形状、裁切），**没有旋转**；**槽位越出舞台 `lint` 报 `slot-out-of-stage`**（整幅盖满舞台的除外）。时间点用锚点：`"anchors": { "论文": ["u0012", "发表了一篇"], "作者": ["u0012", "<作者名>"] }`，场景里 `A("作者")` 就是说到那个词的局部秒——比字数内插准，第 3 步的经验值照用（镜头先于词 0.3 s，标记晚 0.6–0.8 s）。

```html
<div class="bed dark"></div>
<div class="shadow" data-for="p2" style="border-radius:4px"></div>
<div data-slot="p2" data-z="10" data-fit="cover"></div>
<div class="shadow" data-for="p1" style="border-radius:4px"></div>
<div data-slot="p1" data-z="11" data-fit="cover"></div>
<div id="hl1" class="mark" data-layer="front"></div>
<div id="card" class="card" data-layer="front"><div class="kicker" id="k"></div><div class="h2" id="ttl"></div><div class="tag" id="src"></div></div>
<div id="cap" class="cap dark" data-layer="front"></div>
```

- **叠放**：背页 `data-z` 低、顶页最高，按「底页先、顶页后」依次落：每页从「落位框上移 80–120 px、放大约 6%」插值到落位框，`at("p2", mix(R2hi, R2, prog(t, t2, 0.5)), prog(t, t2, 0.15))`；落位框之间错开 10–18 px 显出厚度。起始框也要在舞台内。
- **发牌**：没有旋转，扇形改成**错位平移**——各页落位框横向错开 40–60 px、纵向错开 10–20 px，从舞台边缘**内侧**快速滑入（起点贴边、透明度 0→1，别从画外飞进来）；底页先落、顶页最后。一定要真旋转的扇形，只能走上面的颗粒做法。
- **推近**：推荐**窗内推近**——顶页框不动（或先平移放大到主画面区），逐帧改它的 `data-crop`（上、右、下、左，源图比例，各值 ∈ [0, 1)）从 `[0,0,0,0]` 收到目标区域：目标矩形 `[x0,y0,x1,y1]`（第 2 步的归一化矩形）先按页面框宽高比外扩，再取 `crop = [y0, 1−x1, 1−y1, x0]`；`p = prog(t, A("作者") − 0.3, 0.8)`，`el.dataset.crop = mix([0,0,0,0], crop, p).join(",")`。两端区域宽高比一致时，中间插值也一致，不会变形。也可以**推到整幅**：把顶页框放大到盖满整个舞台（整幅覆盖不判越界）；放大到一半越出舞台的写法 `lint` 会报。末段的镜头微漂 = 裁切再收 1–2%。
- **荧光笔 / 框 / 下划线**：画在 `data-layer="front"` 的 **div** 上（工具箱没有现成的荧光笔类，在场景 `<style>` 里自己写一个半透明底色的 `.mark`，再 `wipe(el, prog(t, mark_at, 0.5))` 擦出；框用 border；下划线用很矮的 div）。**别用 SVG 的 path / line 压在页面上**——`lint` 会报 `stroke-through-slot`。位置从源图比例换到舞台像素：页面框 `[bx,by,bw,bh]`、裁切 `[ct,cr,cb,cl]` 时，源点 `(u,v)` 落在 `X = bx + (u − cl) / (1 − cl − cr) × bw`，`Y = by + (v − ct) / (1 − ct − cb) × bh`；推近过程中逐帧按当刻裁切重算，标记才跟得住那一行。上一枚标记退到约 0.4 透明度再划下一枚。
- **出处卡**：front 层的 `.card`（`.kicker` / `.h2` / `.tag` 等，配色走栏目的 `theme.css`），`reveal(el, prog(t, A("<念完那句的词>"), 0.5))` 落下后**驻留到窗口末尾**。文字只取有出处的：页面是联网取回的，读工程 `web/sources.jsonl` 里那一条的 `title` / `site_name` / `date`；否则用口播原话（`context.md` 里的句子）。**MUST NOT 编造**作者、期刊、年份。推近时让顶页落在卡的另一侧（卡占左栏就把页面推到右侧区）；竖屏按上下分区摆。
- **字幕**：窗口场景声明 `takeover: ["subtitles","overlay"]` 时字幕由你排（`caption("cap","subs",t,…)`），别与出处卡、页面重点区相撞；卡上已经写着同一句话的那段可以隐藏字幕。

### c. 构建、机检、看静帧

`gtrk composite build --project <工程> --only <cp-wNN>` → `gtrk composite lint --project <工程> --only <cp-wNN> --stills <入场落定>,<推近后>,<出处卡落下后>`。静帧看四件事：堆够不够显眼、推近后的重点那一行是否清楚可读、标记是否压在那一行上、终态「资料 + 卡」都在。之后的铺轨、烘焙照 `/gtrk-composite`。

## 两条口播 → spec 的示例（只示意映射，别照抄文案）

**发牌 + 两处强调 + 译名卡**（11.7 s；口播「2021 年海德堡大学的神经科学家 X 和他的团队发表了一篇重量级论文，标题翻译过来就叫……」）：
0.15 s 起三页发牌（1.45 s 落定）→ 1.7 s 左栏小条「机构 · 年份 / 作者」→ 3.6 s 推近作者名、4.3 s 荧光笔 → 6.3 s 推到标题、7.1 s 荧光笔（上一枚退到 0.4）→ 9.2 s（念完标题时）左栏落译名卡 → 驻留。

**叠放 + 标题下划线 + 出处卡**（dark 预设，10 s）：0.2 s 起三页叠放（1.4 s 落定）→ 1.9 s 左栏卡 → 3.0 s 推到标题、3.8 s 下划线 → 驻留（尘点漂、烟雾缓移、镜头微漂）。

## 硬约束（颗粒做法；不满足别交付——合成里用时以 `/gtrk-composite` 的编排规则与 `lint` 为准）

1. gsap-emit v1 全部铁律（`<template>` 包裹 / `gsap.timeline({paused:true})` 注册 `window.__timelines[id]` / 无 random·Date·rAF / 自包含 / 实心底下沉子层 / 字面值色与字体 / 占满坑位 + 终态驻留 / 重复图元合并）——脚本已按此发射，别手改产物 HTML 去违反它。
2. 零卷积：不加 `filter: blur()`、`drop-shadow()`；软影只用 `box-shadow` 与渐变。
3. 字体名命中渲染服务端字体表；缺省思源栈已命中，换字体要自己核。
4. 画幅 = 目标工程画幅（缺省 1920×1080）；竖屏工程另写一份 spec（`width/height` 改，坐标按新画幅给）。
5. 文案卡不压字幕带（底沿 ≤ 0.85 H）；overlay 用法（`bg: transparent`）时卡与页面都要避开画面里的真人主体。
