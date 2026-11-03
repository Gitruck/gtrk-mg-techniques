# globe-arcs 示例

两份 spec 自带全部内容，拿来就能跑。城市坐标是公开的地理数据；**线路都是示意**，不对应任何真实航线或业务，标题里写明了「示意」。

```bash
python ../scripts/globe_arcs.py hub-routes-dark.spec.json --out /tmp/hub.html
python ../scripts/globe_check.py /tmp/hub.html --out /tmp/hub-sheet.png --video /tmp/hub.mp4
```

| 文件 | 底 | 演示了什么 |
|---|---|---|
| [`hub-routes-dark.spec.json`](hub-routes-dark.spec.json) | `dark` | 亚洲视角下从一个枢纽飞出两条常亮线；随后视角转向西、略推近，以中转城市为中心再飞向欧洲；全程缓慢自转 |
| [`pacific-trail-light.spec.json`](pacific-trail-light.spec.json) | `light` | 不自转；两条跨太平洋的彗尾式飞线（`trail`）；再把视角推近到北美西海岸（zoom 1.5），飞一条短线 |

## 换成你的地点时

- 先给标签一个直觉方向（西边的城市 `left`），脚本查到互叠或出画会告诉你是哪个、在哪一刻。
- 落点在落地时不在正面，脚本会拒绝——在那之前加一个 `views` 把它转过来，或者调小 `spin`。
- 线路涉及真实业务时，在标题或副标题写明「示意」；数据来源、时间口径写在旁边。
