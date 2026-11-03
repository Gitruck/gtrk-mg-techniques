# lyric-pv 示例

两份示例的歌词都是**原创示意文字**（不是任何现成的歌），拿来就能跑：

```bash
python ../scripts/lyric_pv.py night-city-ink.spec.json --out out/night.html
python ../scripts/lyric_check.py out/night.html --out out/night-sheet.png --video out/night.mp4
```

| 文件 | 画幅 / 配色 / 节奏 | 演示了什么 |
|---|---|---|
| [`night-city-ink.spec.json`](night-city-ink.spec.json) | 1920×1080 · `ink` · 一拍二 | 七句 JSON 歌词；大多部件交给 seed；第 1 句用 `\|` 断行，第 4 句强调「纸飞机」，第 5 句点名叠排，第 6 句同词复现（副歌规则）；句间闪白、色块擦过 |
| [`rain-paper-vertical.spec.json`](rain-paper-vertical.spec.json) + [`rain-paper.lrc`](rain-paper.lrc) | 1080×1920 · `paper`（衬线）· 逐帧 | LRC 输入、`line_overrides` 点名第 1 句竖排与第 5 句居中强调；用 `layouts` / `enters` / `exits` 收窄成安静的调子；句间色块擦过；关掉装饰数字 |

换成你的歌时：先全部交给 seed 跑一遍，对着 stdout 摘要和接触表，只给不满意的那几句点名部件；
断行不对就在歌词里加 `|`，重点词写进 `emph`。
