# prompts/ · 指令集模板

本目录存放注入到客户端提示词里的指令集。除 `archive/` 外，每个文件都被
`bj_tool.py` 的版本表或 `inject.ps1` 直接引用。

## 拼装机制（COMPOSED）

V5 系的三个版本不预置成品文件，运行时用「头部 + 正文」现场拼：

| 版本 key | 成品名 | 头部 | 正文 |
|---|---|---|---|
| `v5docs` | `v5-docs.md` | `_sandbox-v5-header.md` | `v5-body.md` |
| `v51b` | `v5-1b.md` | `_v51b-header.md` | `v5-body.md` |
| `v52c` | `v5-2c.md` | `_v52c-header.md` | `v5-body.md` |

成品写到 `%LOCALAPPDATA%\pi-workbench\prompts\`，**正文只存一份**，改动不会漏同步。

## 单文件模板（不拼装）

| 文件 | 版本 key | 说明 |
|---|---|---|
| `_gpt6-astra-header.md` | `gptastra` | 收编自上游开源仓库，操作契约路线 |
| `_gpt56sol-header.md` | `gpt56sol` | 同路线精简版 |
| `_glm-neutral-header.md` | `glmneutral` | V5.1b 骨架 + GLM 适配段 |
| `_glm53f-kovak.md` | `glm53f` | 收编自 `chiina66/glm5.3f-jailbreak` |
| `glm-dshpurge.md` | — | GLM 原生格式备用模板，未挂版本表，手动使用 |

## archive/

已退役的历史头部，不被任何代码引用，仅作留存：
`_v51-header.md` / `_v52a-header.md` / `_v52ab-header.md` / `_v52b-header.md`。

---

新增模板时改这三处：`bj_tool.py` 的 `MODEL_GROUPS`（版本表）、`COMPOSED` 或
`_prompt_file()`（文件名映射）、`bundle_check()` 的 `need` 列表（打包自检）。
