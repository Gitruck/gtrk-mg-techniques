# gtrk-mg-techniques · MG 排版技法族

> 「整体排版 motion」这一族视觉传达技法的家——每个 skill 是**一种技法**（一种把东西摆上画面、动起来、把注意力引到某处的方式），
> 不绑定任何栏目、不带任何栏目审美。
>
> 产物有两类，按技法的性质定，**不是二选一的风格偏好**：
>
> | 产物 | 什么时候是这类 | 怎么进工程 |
> |---|---|---|
> | **html-particle**（gsap-emit v1） | 画面元素可数、可用 DOM/SVG 表达、总字符进得了预算 | `gtrk mg` 铺轨 / `gtrk mg render` 出透明 MOV |
> | **视频**（mp4） | 逐帧素材类——帧数或像素量注定超颗粒字符预算 | 在客户端拖进目标视频轨（`gtrk` 没有加任意 clip 的命令） |
>
> 判据很硬：把内容内嵌成 data URI 之后**进不了 1,900,000 字符预算**的，就只能是视频。
> 比如 `band-strobe` 要在 3.6 秒里放 108 张 1920×360 的画面——没有任何压法能塞进颗粒。

## 这个仓为什么单开

成片管线里 MG 颗粒由**栏目生产 skill** 产（它懂栏目的视觉语法），但有一类东西跨栏目通用、又值得反复用：
文献页叠放发牌、新闻截图局部强调、聊天记录回放、数据卡翻面……它们是**技法**而不是模板——
脚本只做机械活（图片重编码、几何、时间线发射），**给什么内容、强调哪里、什么时候动**仍由调用它的 agent 按口播现场决定。
把这一族收在一个仓里，任何栏目 skill 或 agent 都能按需取用，而不必各自重写一遍。

## 技法目录

| skill | 产物 | 一句话 | 典型触发 |
|---|---|---|---|
| [`doc-reveal`](skills/doc-reveal/SKILL.md) | 颗粒 | 文献 / 文档页「叠放 · 发牌 · 局部强调」：多页纸落成一叠，镜头推近某一行，荧光笔划过，旁边落文案卡 | 「引用这篇论文」「新闻截图叠放展示」「把这段文字圈出来放大」 |
| [`band-strobe`](skills/band-strobe/SKILL.md) | 视频 | 横带频闪蒙太奇：画幅横切成几条带，每条带各自快切不同画面、切点相位错开，叠 RGB 错位与洗白调色 | 「做个片头 / OP」「开场频闪」「闪回蒙太奇」「glitch 快切」 |

## 安装

任一 Agent（Claude Code / Codex / Cursor …）：

```bash
npx skills add Gitruck/gtrk-mg-techniques --skill doc-reveal
npx skills add Gitruck/gtrk-mg-techniques --skill band-strobe
```

用 gtrk 的宿主（同一条路，顺带登记进栏目配置 `style.skills`，缺省 `routing:none`——技法不是车道生产者，由 agent 按槽位取用）：

```bash
gtrk skills add Gitruck/gtrk-mg-techniques --skill doc-reveal
gtrk skills add Gitruck/gtrk-mg-techniques --skill band-strobe
```

脚本依赖：Python 3.10+、Pillow；按需 pymupdf（PDF 取页）、playwright（本地 seek 预检）、
ffmpeg + numpy（视频类技法：`band-strobe`）。

## 每个技法必须满足的门槛

**两类产物都要满足的：**

1. **技法 ≠ 模板**：脚本不带示例文案、不带任何栏目色板；预设只有中性的几档，栏目色由调用方注入。
   同一份脚本给不同内容产的东西应当长得不一样——差别在调用方写的 spec。
2. **确定性**：同 spec 同 seed ⇒ 逐帧一致，可复现。
3. **可验收**：附一个能量出客观读数的工具，别让验收停在"我觉得挺像"。
4. **自检不静默**：脚本自己查得出来的错 MUST 当场退出、不产文件。
   宁可炸在生成阶段，也别让一个短了 10% 的产物流到成片。
5. **一技法一目录**：`skills/<name>/{SKILL.md, scripts/, references/spec.md, assets/, examples/}`。

**颗粒类另外要满足的：**

6. **契约**：产物过 `gtrk mg lint`（gsap-emit v1：`<template>` 包裹 / GSAP paused 时间线注册 / 自包含 / 实心底下沉子层 / 占满坑位 + 终态驻留 / 重复图元合并）。
7. **预算**：单颗 < 1,900,000 字符（客户端上限 2,000,000，超了静默拒挂）。图片内嵌 data URI，脚本自动降质保预算。
8. **零卷积**：不用 `filter: blur()` / `drop-shadow()` / `feGaussianBlur`；软影一律 `box-shadow` / 渐变（渲染管线逐帧软光栅，卷积按面积 × 半径 × 帧数计价）。
9. **可预检**：附本地 seek 预检（playwright 抽帧接触表），但真验收永远是真渲染引擎抽帧。

**视频类另外要满足的：**

10. **帧数精确**：每一段、每一条带、成品各查一道帧数。`ffmpeg -ss` 越界会**一帧不出且退出码为 0**，
    按时长截取会在取整上抖——这两条都会让产物静默变短，到成片才发现。
11. **落地方式写清楚**：`gtrk` 没有往工程里加任意视频 clip 的命令，SKILL.md MUST 明说这一步是手动拖，
    别让调用方去裸手改 `.gtrk` JSON。

## 与 gtrk 成片 SOP 的关系

**颗粒类**在 SOP 第 ⑤ 步（`/gtrk-mg`）被取用：某个 MG 槽位需要这类技法时，agent 按槽位派单（`composition_id` / 坑位包络 / `category`）
写一份 spec、跑脚本、产物**直接写到** `<工程>/mg/<composition_id>.html`，再交回 `gtrk mg lint --dispatch` 与 `gtrk mg` 铺轨。

**视频类**不进 MG 轨——它是一段画面，该在视频轨上。产物落工程目录（如 `<工程>/op/`），
由人在客户端拖到目标轨、对齐坑位起点。

两类都一样：本仓不驱动任何 gtrk 命令，也不知道工程在哪。

## 许可

MIT。技法只展示你有权展示的内容：论文首页作引用、新闻截图作评论属常规做法，整篇翻页不是；
频闪蒙太奇用的是你自己片子里的画面。
