# examples

两份 spec 只示意「口播 → 节奏 → 字段」的映射，**页面图片不随仓分发**（版权与体积）。
把你自己的页面放到 `pages/`（或改 `pages[]` 路径）再生成：

```bash
python ../scripts/doc_reveal.py deal-two-focus.spec.json --out /tmp/deal.html
python ../scripts/doc_reveal.py stack-dark.spec.json   --out /tmp/stack.html
```

- `deal-two-focus.spec.json`：发牌入场 + 作者名 / 标题两处荧光笔 + 左栏小条与译名卡，奶白底（`paper`）。
- `stack-dark.spec.json`：叠放入场 + 标题下划线 + 左栏卡，黑底烟雾尘点（`dark`，最接近参考片）。

矩形来自 `pdf_pages.py --find`，请按你的页面重新取；文案按你的口播重写。
