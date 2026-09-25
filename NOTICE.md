# 第三方内容与署名

本仓库的代码（`bj_tool.py` / `bj_tool.spec` / `inject*.ps1` / `build.cmd`）与自有文档
以 [MIT](LICENSE) 授权。

`skills-v4/` 与 `prompts/` 是**收集整理**的内容，其中一部分来自第三方，著作权归原作者，
按原始许可随附。下面逐项列出已知来源；若你是权利人且希望调整署名或移除，请提 issue。

---

## 随附原始许可的技能包

| 目录 | 许可 | 著作权人 |
|---|---|---|
| `skills-v4/protocol-reverse-engineering/` | MIT | Copyright (c) 2024 Seth Hobson |
| `skills-v4/reverse-engineering-api/` | MIT | Copyright (c) 2025 kalil0321 |
| `skills-v4/pentest-tools/src-hunter/` | MIT | Copyright (c) 2026 MyuriKanao |

以上三个目录内的 `LICENSE` 文件保持原样，请一并遵守。

## 署名标注的技能包

| 范围 | 标注 |
|---|---|
| `skills-v4/seagull-*/`（16 个技能） | `author: SeaGull` |

## 提示词模板

| 文件 | 来源 |
|---|---|
| `prompts/_glm53f-kovak.md` | 收编自 `chiina66/glm5.3f-jailbreak`（仅保留正文与思考通道锚定部分，上游无量化数据） |
| `prompts/_gpt6-astra-header.md` | 收编自上游开源仓库模板；**已删除上游的「中转站保护」商业条款段**，正文未改 |
| `prompts/_gpt56sol-header.md` | 同上，收编自上游开源仓库模板；同样删除商业条款段 |

其余模板（`_sandbox-v5-header.md` / `_v51b-header.md` / `_v52c-header.md` / `_glm-neutral-header.md` /
`v5-body.md` / `glm-dshpurge.md`）为本仓库自研。

---

## 用途声明

本仓库内容面向**自有设备、自建靶标与已获授权的测试范围**。
技能包内的命令、载荷与利用思路仅用于授权场景下的研究与防御验证。
使用者需自行确认所处环境的法律与授权边界，作者不对滥用后果负责。
