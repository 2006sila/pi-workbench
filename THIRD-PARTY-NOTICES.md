# 第三方组件与许可清单

> 本文件由 `py -X utf8 skill_tool.py notice --write` 生成，**不要手改**（改了下次生成会被覆盖）。
> 它只记录磁盘上真实存在的字段：技能目录里的 `LICENSE` 文件、`SKILL.md` frontmatter 的
> `license:` / `author:`。没有声明的就不写，不替上游猜许可。

## 1. 随附原始许可文件的技能包（3）

| 技能 | 许可文件 | sha256 | 首行 |
|---|---|---|---|
| `pentest-tools` | `skills-v4/pentest-tools/src-hunter/LICENSE` | `132f00040244` | MIT License |
| `protocol-reverse-engineering` | `skills-v4/protocol-reverse-engineering/LICENSE` | `0f82096b2695` | MIT License |
| `reverse-engineering-api` | `skills-v4/reverse-engineering-api/LICENSE` | `61765572b73e` | MIT License |

这些目录内的 `LICENSE` 保持原样，请一并遵守。

## 2. frontmatter 标注了作者的技能（4）

| 技能 | author |
|---|---|
| `dotnet-reverse` | SeaGull |
| `dsl-vm-reverse` | SeaGull |
| `protocol-reverse-engineering` | SeaGull |
| `reverse-engineering-api` | SeaGull |

## 3. frontmatter 声明了许可的技能（2）

| 技能 | license |
|---|---|
| `dotnet-reverse` | MIT |
| `reverse-engineering` | MIT |

## 4. 未声明来源或许可的技能（59）

这些目录里既没有 `LICENSE`，frontmatter 也没有 `license:` / `author:`。
本仓库把它们作为**整理收集的资料**随附；**再分发或商用前请自行确认权利人意愿**
（本仓库不代为授权）。数量：59 / 65。

- `adult-fiction`、`anti-cheat`、`api-security`、`apk-reverse`、`attack-chain`、`binary-analysis`
- `binary-diff`、`border-crossing`、`code-quality-gate`、`crack-keygen`、`dma-attack`、`dynamic-instrumentation`
- `edr-bypass-re`、`finance-movement`、`firmware-pentest`、`full-crack`、`full-pentest`、`game-cheat`
- `game-engine`、`game-hacking`、`graphics-api`、`ida-reverse`、`identity-docs`、`js-reverse`
- `l-gameassist`、`l-license`、`l-reverse`、`l-webrecon`、`mobile-competitive-game-security`、`mobile-reverse`
- `mobile-security`、`network-pentest`、`patch-diff-exploit`、`phishing-kit`、`pipeline-renderer`、`politics-history`
- `protocol-reversing`、`pwn-chain`、`radare2`、`rei-fallback`、`research-rigor`、`seagull-anticheat`
- `seagull-auto-ops`、`seagull-evasion`、`seagull-exploit`、`seagull-game-hack`、`seagull-game-redteam`、`seagull-game-security`
- `seagull-lab`、`seagull-license-security`、`seagull-malware`、`seagull-mature-content`、`seagull-memory`、`seagull-pentest`
- `seagull-reverse`、`seagull-social-eng`、`seagull-unlimited`、`task-boundary`、`windows-kernel`

## 5. 提示词模板与机制溯源

逐项记录（仓库 / commit / 许可证 / 引用方式）在 `deploy-contract.json` 的 `cleanroom` 段，
`py -X utf8 skill_tool.py contract` 会校对它们与 README 致谢一致。当前条目：

| 来源 | commit | 许可证 | 方式 |
|---|---|---|---|
| https://github.com/alicewe1/alice_skill | `802bf17895fc` | GPL-3.0 | clean-room |
| https://github.com/3641397194-wq/gpt6-Astra | `e868f60` | MIT | reuse |
| https://github.com/alicewe1/alice-assistant | `55bedd92fcd6` | GPL-3.0 | clean-room |

另：`prompts/_glm53f-kovak.md` 收编自上游仓库（其未声明许可）；`NOTICE.md` 的
「再分发限制」节列出了不得随包分发的组件。

