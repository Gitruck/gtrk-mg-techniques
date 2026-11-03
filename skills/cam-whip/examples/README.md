# cam-whip 示例

素材不随仓分发，先用 ffmpeg 内置的程序化信号源合成六段（与 `beat-pulse` 同一份脚本）：

```bash
python make_clips.py                 # → clips/c01.mp4 … c06.mp4、clips/beat120.wav
python ../scripts/cam_whip.py combo-120bpm.spec.json --out out/combo.mp4
python ../scripts/measure.py out/combo.mp4 --spec combo-120bpm.spec.json
```

| 文件 | 画幅 | 演示了什么 |
|---|---|---|
| [`combo-120bpm.spec.json`](combo-120bpm.spec.json) | 1920×1080 · 60fps | 每拍换镜头，轮流用甩镜（左 / 右 / 上）、拉镜、平铺推拉；段内轻微缓推 + 手持微震 |
| [`pull-chain-vertical.spec.json`](pull-chain-vertical.spec.json) | 1080×1920 · 30fps | 不切镜头，同一个素材上每拍一次拉镜，锚点左上 / 右下交替、转角交替——「锚点拉镜连招」 |

带上鼓点看节奏（产物本身无声；`combo` 的拍点与 `beat120.wav` 对齐）：

```bash
ffmpeg -i out/combo.mp4 -i clips/beat120.wav -shortest -c:v copy -c:a aac out/combo-with-beat.mp4
```
