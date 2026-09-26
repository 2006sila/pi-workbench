# prompts/ · 指令集模板

本目录存放注入到客户端提示词里的指令集。除 `archive/` 外，每个文件都被
`bj_tool.py` 的版本表或 `inject.ps1` 直接引用。

## 拼装机制（COMPOSED）

V5 系的四个版本不预置成品文件，运行时用「头部 + 正文（+ 追加块）」现场拼：

| 版本 key | 成品名 | 部件（按顺序） |
|---|---|---|
| `v5docs` | `v5-docs.md` | `_sandbox-v5-header.md` + `v5-body.md` |
| `v51b` | `v5-1b.md` | `_v51b-header.md` + `v5-body.md` |
| `v52c` | `v5-2c.md` | `_v52c-header.md` + `v5-body.md` |
| `v52cx` | `v5-2c-ext.md` | `_v52c-header.md` + `_ext-auth.md` + `v5-body.md` + `_ext-subst.md` + `_ext-deliver.md` |

成品写到 `%LOCALAPPDATA%\pi-workbench\prompts\`，**正文只存一份**，改动不会漏同步。

## 单文件模板（不拼装）

| 文件 | 版本 key | 说明 |
|---|---|---|
| `_gpt6-astra-header.md` | `gptastra` | 收编自上游开源仓库，操作契约路线 |
| `_gpt56sol-header.md` | `gpt56sol` | 同路线精简版 |
| `_glm-neutral-header.md` | `glmneutral` | V5.1b 骨架 + GLM 适配段 |
| `_glm53f-kovak.md` | `glm53f` | 收编自 `chiina66/glm5.3f-jailbreak` |
| `glm-dshpurge.md` | — | GLM 原生格式备用模板；未挂版本表，手动使用（已在随包清单里，`contract` 只会提醒它没被引用） |

## archive/

已退役的历史头部，不被任何代码引用，仅作留存：
`_v51-header.md` / `_v52a-header.md` / `_v52ab-header.md` / `_v52b-header.md`。

---

新增 / 改动模板时要动的地方（以前靠记忆，现在 `py -X utf8 skill_tool.py contract` 会逐个核对）：

1. **`deploy-contract.json`**：`resources`（随包清单，漏了就打包少文件）、`templates.composed` / `templates.standalone`
   / `templates.anchors`（身份锚定串，含「不得出现在其它模板」的交叉检查）
2. **`bj_tool.py`**：`MODEL_GROUPS`（版本表）、`COMPOSED` 或 `_prompt_file()`（拼装部件 / 文件名映射）
3. **`inject.ps1`**：`$VersionMap`（**拼装成品名 → 版本 key 的映射；漏登记会让 state 把版本记成文件名，
   导致「重新注入（按上次模板）」静默回退到默认模板**）
4. **`docs/SECURITY.md`** / 本文件：把新模板的来源与许可写清楚（收编的要进 `NOTICE.md`）

`contract` 会比对 1–3 与磁盘/代码是否一致（含“契约改了代码没改”这种漂移），
重复的 `bundle_check()` 清单已经取消 —— 资源清单只留 `deploy-contract.json` 一份。
