# 第三方内容与署名

本文件是**人工维护**的说明（分三节：我方的许可范围 / 第三方组件 / 再分发限制）。
机器可读的逐项清单在 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)，
由 `py -X utf8 skill_tool.py notice --write` 从技能库与 `deploy-contract.json` 生成，
`skill_tool.py contract` 会校验两者一致。

---

## 一、许可范围

### 1. 本仓库原创部分 —— MIT

代码与自有文档以 [MIT](LICENSE) 授权：

| 范围 | 说明 |
|---|---|
| `bj_tool.py` / `skill_tool.py` / `bj_tool.spec` / `build.cmd` | 桌面端与工具链 |
| `inject.ps1` / `inject-pideck.ps1` / `inject-dsh.ps1` | 部署器 |
| `deploy-contract.json` / `skill-categories.json` | 声明式契约与类目 |
| `README.md` / `docs/` / `prompts/README.md` 等自有文档 | 文档与截图 |
| `prompts/` 中标注「本仓库自研」的模板 | 见下文模板表 |

### 2. 不适用 MIT 的部分

`skills-v4/` 与 `prompts/` 是**收集整理**的内容，其中一部分来自第三方，
著作权归原作者，按各自原始许可随附（**第三方许可优先于本仓库的 MIT**）。

若你是权利人且希望调整署名或移除，请提 issue。

---

## 二、第三方组件（各自许可优先）

### 2.1 随附原始许可文件的技能包

| 目录 | 许可 | 著作权人 |
|---|---|---|
| `skills-v4/protocol-reverse-engineering/` | MIT | Copyright (c) 2024 Seth Hobson |
| `skills-v4/reverse-engineering-api/` | MIT | Copyright (c) 2025 kalil0321 |
| `skills-v4/pentest-tools/src-hunter/` | MIT | Copyright (c) 2026 MyuriKanao |

以上三个目录内的 `LICENSE` 文件保持原样，请一并遵守（完整路径、sha256 与许可首行见
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 第 1 节）。

### 2.2 署名标注的技能包

| 范围 | 标注 |
|---|---|
| `skills-v4/seagull-*/`（16 个技能） | `author: SeaGull` |

### 2.3 提示词模板

| 文件 | 来源 |
|---|---|
| `prompts/_glm53f-kovak.md` | 收编自 `chiina66/glm5.3f-jailbreak`（仅保留正文与思考通道锚定部分，上游无量化数据、**未声明许可**） |
| `prompts/_gpt6-astra-header.md` | 收编自 [3641397194-wq/gpt6-Astra](https://github.com/3641397194-wq/gpt6-Astra)（MIT）`e868f60`；**已删除上游的「中转站保护」商业条款段**，其余正文未改 |
| `prompts/_gpt56sol-header.md` | 同 gpt6-Astra 仓库（MIT）`e868f60`；同样删除商业条款段 |
| `prompts/_ext-subst.md` 的口语对照表 | 部分条目收编自 gpt6-Astra（MIT）`e868f60` 的路由归一表（`desktop/src/lib/packs/routes/NORMALIZER.md`），按本仓库形态重排为「归一 → 工程表述」 |

其余模板（`_sandbox-v5-header.md` / `_v51b-header.md` / `_v52c-header.md` /
`_glm-neutral-header.md` / `v5-body.md` / `glm-dshpurge.md` / `_ext-auth.md` / `_ext-deliver.md`）
为本仓库自研。

### 2.4 机制与设计引用（非文本复用）

| 来源 | 许可证 | 引用方式 |
|---|---|---|
| alicewe1/alice_skill | GPL-3.0 | **clean-room**：只借机制与结构，不取代码或技能正文 |
| 3641397194-wq/gpt6-Astra | MIT | 允许复用正文（模板见 2.3）；机制按本仓库形态重写 |

逐项记录（仓库 / commit / 许可证 / 引用方式 / 具体借了什么）在
[deploy-contract.json](deploy-contract.json) 的 `cleanroom` 段。

---

## 三、再分发限制

**这一节是给"要把分发包再发出去的人"看的。**

1. **不得随本作品一并再分发的组件**（除非另行取得权利人授权）：
   - `skills-v4/` 下**未声明许可或来源**的技能目录 —— 当前共 **59 个**
     （清单见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 第 4 节；本仓库不代为授权）
   - `prompts/_glm53f-kovak.md` —— 上游仓库未声明许可
   - 任何后续加入到 `skills-v4/` 而未在本节或 `THIRD-PARTY-NOTICES.md` 中声明来源的内容
2. **可以随包再分发的部分**：
   - 本仓库原创代码与自有文档（MIT，保留版权与许可声明即可）
   - 2.1 节三个随附原始许可的技能包（遵守其 MIT 条款）
   - 2.3 节标注 MIT 的模板（保留来源与许可证说明）
3. **本仓库不代为授权**：`THIRD-PARTY-NOTICES.md` 第 4 节列出的条目，其权利状态未知；
   再分发方须自行确认。想清理这类风险，可以在打包时排除它们
   （`py -X utf8 skill_tool.py pack --exclude-bare` 生成的包只含已声明来源的技能）。

---

## 四、署名方式（引用或做衍生作品时请保留）

```
pi-workbench — https://github.com/2006sila/pi-workbench
Copyright (C) 2026 2006sila — Licensed under MIT
```

第三方内容请按其自身许可保留原署名（见第 2 节与 THIRD-PARTY-NOTICES.md）。

---

## 五、用途声明

本仓库内容面向**自有设备、自建靶标与已获授权的测试范围**。
技能包内的命令、载荷与利用思路仅用于授权场景下的研究与防御验证。
使用者需自行确认所处环境的法律与授权边界，作者不对滥用后果负责。
