# beat-pulse 示例

素材不随仓分发，先用 ffmpeg 内置的程序化信号源合成六段（分形、测试图、生命游戏、元胞自动机、谢尔宾斯基地毯、渐变网格），
外加一条 120 BPM 的合成鼓点（只给预览配声用）：

```bash
python make_clips.py                 # → clips/c01.mp4 … c06.mp4、clips/beat120.wav
python ../scripts/beat_pulse.py punch-120bpm.spec.json --out out/punch.mp4
python ../scripts/measure.py out/punch.mp4 --spec punch-120bpm.spec.json
```

| 文件 | 拍点 | 演示了什么 |
|---|---|---|
| [`punch-120bpm.spec.json`](punch-120bpm.spec.json) | BPM 网格 120、每 4 拍一个重拍 | 每拍切片段；普通拍推一下 + 震，重拍再叠闪白与色散（`punch`）。60fps |
| [`glitch-downbeats.spec.json`](glitch-downbeats.spec.json) | 拍点文件 [`beats-128.json`](beats-128.json)（合成，128 BPM） | 只在 downbeat 上切，其余拍原地打 `glitch`：色散 + 条带错位，重拍负片闪。30fps |

想听着看节奏，把鼓点混进预览（产物本身无声）：

```bash
ffmpeg -i out/punch.mp4 -ss 0 -i clips/beat120.wav -shortest -c:v copy -c:a aac out/punch-with-beat.mp4
```

换成你的片子时：`clips` 换成你自己的镜头（挑一眼能认出来的、本身别太静的），拍点换成你那首 BGM 的。
