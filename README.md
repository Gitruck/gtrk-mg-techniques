# gtrk-mg-techniques · MG 排版技法族

> 「整体排版 motion」这一族视觉传达技法的家——每个 skill 是**一种技法**（一种把东西摆上画面、动起来、把注意力引到某处的方式），
> 不绑定任何栏目、不带任何栏目审美。产物是 gsap-emit v1 合规的 html-particle 颗粒，
> 直接进 `gtrk` 成片工程（`gtrk mg` 铺轨）或独立出透明 MOV（`gtrk mg render`）。

## 这个仓为什么单开

成片管线里 MG 颗粒由**栏目生产 skill** 产（它懂栏目的视觉语法），但有一类东西跨栏目通用、又值得反复用：
文献页叠放发牌、新闻截图局部强调、聊天记录回放、数据卡翻面……它们是**技法**而不是模板——
脚本只做机械活（图片重编码、几何、时间线发射），**给什么内容、强调哪里、什么时候动**仍由调用它的 agent 按口播现场决定。
把这一族收在一个仓里，任何栏目 skill 或 agent 都能按需取用，而不必各自重写一遍。

## 技法目录

| skill | 一句话 | 典型触发 |
|---|---|---|
| [`doc-reveal`](skills/doc-reveal/SKILL.md) | 文献 / 文档页「叠放 · 发牌 · 局部强调」：多页纸落成一叠，镜头推近某一行，荧光笔划过，旁边落文案卡 | 「引用这篇论文」「新闻截图叠放展示」「把这段文字圈出来放大」 |

## 安装

任一 Agent（Claude Code / Codex / Cursor …）：

```bash
npx skills add Gitruck/gtrk-mg-techniques --skill doc-reveal
```

用 gtrk 的宿主（同一条路，顺带登记进栏目配置 `style.skills`，缺省 `routing:none`——技法不是车道生产者，由 agent 按槽位取用）：

```bash
gtrk skills add Gitruck/gtrk-mg-techniques --skill doc-reveal
```

脚本依赖：Python 3.10+、Pillow；按需 pymupdf（PDF 取页）、playwright（本地 seek 预检）。

## 每个技法必须满足的门槛

1. **契约**：产物过 `gtrk mg lint`（gsap-emit v1：`<template>` 包裹 / GSAP paused 时间线注册 / 确定性 / 自包含 / 实心底下沉子层 / 占满坑位 + 终态驻留 / 重复图元合并）。
2. **预算**：单颗产物 < 1,900,000 字符（客户端颗粒上限 2,000,000，超了静默拒挂）。图片一律内嵌 data URI，脚本自动降质保预算。
3. **零卷积**：不用 `filter: blur()` / `drop-shadow()` / `feGaussianBlur`；软影一律 `box-shadow` / 渐变（渲染管线逐帧软光栅，卷积按面积 × 半径 × 帧数计价）。
4. **技法 ≠ 模板**：脚本不带示例文案、不带任何栏目色板；预设只有中性底（dark / paper / transparent），栏目色由调用方注入。
5. **可预检**：附本地 seek 预检（playwright 抽帧接触表），但真验收永远是真渲染引擎抽帧。
6. **一技法一目录**：`skills/<name>/{SKILL.md, scripts/, references/spec.md, assets/, examples/}`。

## 与 gtrk 成片 SOP 的关系

技法在 SOP 第 ⑤ 步（`/gtrk-mg`）被取用：某个 MG 槽位需要这类技法时，agent 按槽位派单（`composition_id` / 坑位包络 / `category`）
写一份 spec、跑脚本、产物**直接写到** `<工程>/mg/<composition_id>.html`，再交回 `gtrk mg lint --dispatch` 与 `gtrk mg` 铺轨。
本仓不驱动任何 gtrk 命令，也不知道工程在哪——那是 `/gtrk-mg` 的事。

## 许可

MIT。技法只展示你有权展示的页面：论文首页作引用、新闻截图作评论属常规做法，整篇翻页不是。
