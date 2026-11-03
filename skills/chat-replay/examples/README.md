# chat-replay 示例

两份 spec 自带全部内容（对话是虚构的，人物与群名都是编的），拿来就能跑：

```bash
python ../scripts/chat_replay.py phone-group-focus.spec.json --out /tmp/group.html
python ../scripts/chat_check.py /tmp/group.html --out /tmp/group-sheet.png --video /tmp/group.mp4
```

| 文件 | 画框 / 底 | 演示了什么 |
|---|---|---|
| [`phone-group-focus.spec.json`](phone-group-focus.spec.json) | 手机 / `light` | 三人群聊；开场两条上文已在屏上；对方「正在输入」三次；消息排满后上推；最后强调一句（描边 + 荧光笔，不推近）；左栏说明卡 |
| [`panel-dark-compose-zoom.spec.json`](panel-dark-compose-zoom.spec.json) | 桌面窗口 / `dark` | 两人私聊；`stack: bottom`（最新一条贴底、整列上推）；我方在输入框里逐字打字后发出（`compose`）；推近强调，`anchor` 按脚本报的下限避开左栏卡 |

两份都标了 `frame.stamp`（「示意」「情景还原」）——转写或还原的对话 MUST 标。

## 换成你的对话时

- 对白逐字誊写、脱敏；时刻按口播的句级时码估，规则见 SKILL.md「按口播定节奏」。
- `focus.mark` 逐字匹配原文；改了对白记得同步改 mark，不然脚本会拒绝。
- 推近（`zoom` > 1）时如果脚本报「画框钻到文案卡底下」，照它给的 `anchor[0]` 下限调。
