# pi用学习工作台

**PiDeck / DeepSeek Harness 一键部署工具** —— 把指令集与技能库注入到本机已安装的 Pi 系客户端，
卸载即还原，不留残留。

> **English** · A Windows desktop workbench (PySide6) that injects a system-prompt addendum and a
> 65-module skill library into Pi-family clients (PiDeck / DeepSeek Harness). Every write is backed up
> first and a single click restores the original state. Ships 7 prompt templates, a skill manager,
> a colored run log, and a packaging self-check.

![工作台首页](docs/shot-home-light.png)

---

## 它做什么

Pi 系客户端读两类东西：**系统提示词**（每轮都在上下文里）和**技能库**（目录递归扫描 `SKILL.md`）。
本工具把这两样按目标端规范写进去，并且：

- 写之前先备份原文件，写的是**带标记的管理段**，卸载时精确摘除、还原备份
- 技能只增删自己部署的那些，客户端自带技能（`usage-probe` / `image-gen` / `pideck-doctor`）不受影响
- 同名技能先备份再覆盖，卸载时原样还原

| 目标端 | 配置根 | 注入点 |
|---|---|---|
| **PiDeck** | `~/.pi/agent` | `APPEND_SYSTEM.md`（addendum 段，每轮进系统提示词，压缩后仍在）+ `skills/` |
| **DeepSeek Harness** | `$DSH_HOME`（未设置时 `~/.dsh`） | `AGENTS.md`（作为持久 user 消息注入）+ home 级 `cordis.patch.yml`（抬 `agent-instructions` 的 `maxBytes` 预算）+ `skills/` |

> DSH 的 `agent-instructions` 预算不足时会把 `AGENTS.md` **整份丢弃**，所以必须写 patch 层。
> 预算解析顺序：用户显式 `maxBytes` > 本工具上次写入值（粘性） > dsh 默认 65536。

---

## 特性

**界面**

- 6 页工作台：首页 / 模板库 / 技能库 / 运行日志 / 历史 / 设置
- 深色 · 浅色双主题，可跟随系统（监听 `WM_SETTINGCHANGE` 实时切换）
- 无边框窗口 + 自绘标题栏，边缘缩放走原生 `WM_NCHITTEST`（子控件不会吞掉边缘事件）
- 托盘常驻（点 ✕ 最小化到托盘，可改成直接退出）、开机自启开关
- 运行日志按级别着色（`INFO` / `WARN` / `ERROR` / `L1`~`L4` / 退出码）
- 操作历史（注入 / 卸载 / 自检，落盘 `history.json`，上限 300 条）

**部署**

- **三步部署流程**：模板页顶部选 ① 客户端（PiDeck / DeepSeek Harness）+ ② 模式（完整 / 极简），再点卡片上的「部署到 …」（选择记在配置里，下次打开沿用）
- 7 个指令集模板，按目标模型分组（见下）
- 65 个技能模块，可在技能库里搜索、按来源筛选、一键禁用/启用（移到 `skills-disabled/`）
- **技能呈现模式**：
  - `完整模式` = 65 个模块全部进系统提示词，AI 按描述自选
  - `极简模式` = 只留一个菜单技能进提示词（约 90 tokens/轮），模块加 `disable-model-invocation` 不进提示词，AI 按菜单里的模块 id 按需 `read`
- **附加技能包**：两个独立技能包可与模板一起部署，也可单独部署 / 单独移除（同样跟随上面选的客户端与模式）
- 启动时自动重注入开关（按上次记录的模板与模式幂等覆盖）
- 打包自检：验证单文件 exe 内的脚本、模板、技能库、图标都可寻址

**执行**

- 注入过程在后台线程跑 PowerShell，日志按行增量解码（中文不会乱码）
- 任务串行队列，执行中禁止切主题（避免丢掉正在跑的进程）
- 退出前若有任务在跑会二次确认，确认后杀进程树

---

## 界面

**模板页：① 客户端 → ② 模式 → ③ 部署**（深色为极简模式）

| 完整模式（浅色） | 极简模式（深色） |
|---|---|
| ![模板库·完整模式](docs/shot-tpl-full-light.png) | ![模板库·极简模式](docs/shot-tpl-menu-dark.png) |

**附加技能包**（勾选后按上面选的客户端与模式部署/移除）

![附加模板](docs/shot-addons-light.png)

**技能库**（显示当前模式与每个模块的归属）

![技能库](docs/shot-skills-light.png)

---

## 技能呈现模式：完整 / 极简

在模板页顶部选，或用命令行 `-SkillMode`。

Pi 会在启动时把每个技能的**名字 + 描述 + 路径**写进系统提示词（只写这三样，不写正文）。
65 个技能合计约 **28,900 字符 ≈ 7,000 tokens / 每轮** —— 这是固定开销，跟技能库大小线性相关。

**极简模式**把它压到 **1 条（约 90 tokens / 每轮）**：

```text
skills/
├── pi-workbench-menu/SKILL.md   ← 唯一进提示词的技能：类目 + 65 个模块「何时用」+ 取用纪律
├── pwn-chain/SKILL.md           ⎫
├── ida-reverse/SKILL.md         ⎬ frontmatter 里多一行 disable-model-invocation: true
└── …（其余 63 个）                ⎭ → Pi 的 formatSkillsForSystemPrompt 会把它们整个滤除
```

模块文件位置不变，AI 按菜单里的模块 id 直接 `read` 对应 `SKILL.md`（`/skill:<名字>` 也仍可手动强制加载，作为兜底）。
类目由仓库根的 [skill-categories.json](skill-categories.json) 决定；未登记的模块归入「其他」。

**两种模式对比**（用 Pi 自己的加载器实测）：

| | 完整模式 `full` | 极简模式 `menu` |
|---|---|---|
| Pi 加载的技能 | 65 | 66 |
| 进提示词 | 65 条 | **1 条**（+ 纪律型技能，见下） |
| 提示词块 | 28,922 字符 | **609 字符（2.1%）**；带两个附属模板时 1,341 字符 |
| 每轮固定开销 | ≈7,000 tokens | ≈90 tokens（带附属模板量级仍远低于完整模式） |
| 代价 | — | AI 多一跳（先读菜单再读正文）；依赖它遵守取用纪律 |

**切换**：两种模式互相切换是幂等的 —— 切回 `完整模式` 会自动删掉菜单技能、并用源文件覆盖掉模块上那行标记。
UI 上直接点模式按钮重新部署一次即可；命令行传 `-SkillMode`（不传则沿用目标端上次记录的模式）。

**纪律型技能保持常驻（重要）**：附加技能包（`code-quality-gate` / `task-boundary`）的性质与普通模块不同 ——
普通模块是「要用时去查的资料」，它们则是「**每次交付都该生效的纪律**」。按默认规则藏进菜单，
就变成「等 AI 想起来才用」，等于失效。所以极简模式下它们**不加隐藏标记**，照常进系统提示词自动触发；
同时也仍在菜单的「工程交付」类目里，两条路都能走。

实测（用 Pi 自己的 `loadSkillsFromDir` + `formatSkillsForPrompt`）：极简模式 + 两个附属模板 →
**进提示词 3 条**（菜单 + 两个附属模板）、块 1,341 字符、**0 诊断警告**。
例外清单由部署时的 `-MenuKeepAdvertised` 指定（GUI 自动带上），并写进状态清单；
自检会从状态清单读这份例外，不会把它误判成「漏标记」。

> 提示：`disable-model-invocation` 是 Agent Skills 规范字段，已在 Pi 上实测；
> 其他客户端（如 DSH）是否识别未验证 —— 不确定时先用 `full`。

### 部署后怎么用

1. **让客户端重新读技能**：在 Pi 里敲 `/reload`（重载 skills / templates / themes / 上下文文件），
   或重启客户端。技能清单是会话启动时快照的，不重载不生效。
2. **正常下任务就行**：极简模式下模型看到的就是那一条菜单技能，描述里列了领域词
   （逆向 / 二进制、渗透 / 红队、游戏安全…），命中就自己来读菜单。
3. **三条兜底**（不需要记）：
   - 模型没主动读菜单 → 说一句「按技能菜单来」
   - 想直接上某个模块 → `/skill:pwn-chain`（**隐藏的模块仍可用斜杠命令强制加载**，66 个全在）
   - 想看菜单内容 → `/skill:pi-workbench-menu`
4. **额外的保险**：极简模式下部署会在注入的标记块里多写一段「技能取用纪律」
   （约 150 tokens，含菜单绝对路径与取用上限）。APPEND_SYSTEM.md 是系统级、每轮都在、
   优先级高于技能描述，所以路由比只靠菜单描述稳；切回完整模式或卸载时自动消失。

**极简模式的菜单技能自带 6 条硬规矩**（写进生成的菜单，不靠模型自觉）：

1. 先选类目再选模块（每个类目下带「何时进这类」）
2. 一个阶段最多 4 个模块正文
3. **报名（硬性）**：选定/换用模块的**当下**报一行 `参考模块: <模块id>（<用途>）`，禁止只执行不报名、禁止事后补报
4. **取不到就直说**：读不到模块正文时如实报告，**不得声称已按该模块执行**
5. 已读模块直接复用，不重复读
6. 三不要：不为了解全部能力读完所有模块 / 不为比较而读无关类目 / 不凑数

---

## 模板体系

按目标模型分组，共 8 个模板：

| 分组 | 模板 | 说明 |
|---|---|---|
| **DeepSeek 4.1 Flash** | **V5.2c（推荐）** | V5.1b 全量 + 3 条结构化反面示例。实测 99.3%~100%（n=300） |
| | V5.2c 增强版 | V5.2c 正文原样 + 三个**追加块**（见下）；便于与 V5.2c 对照实测，追加块单独维护、不动 V5.2c 本体 |
| | V5.1b | V5 + 请求归一化（口语/黑话/代词直接出通用完整版）+ 术语工程语义映射。实测 98.3%~100%（n=120~300） |
| | V5 文档引擎 | V5.1b 的母版：把请求重定义为「生成文档」而非「执行操作」，去掉全部对抗性元指令。体积只有 V5.1b 的 65%，实测 94.2%（n=120） |
| **GPT** | **GPT-6 Astra（推荐）** | 收编自上游开源仓库。操作契约路线：绑工作区对象 + 四角色产物（MODIFIED/DIFF/VERIFICATION/ROLLBACK）。本机无 GPT 通道，未实测 |
| | GPT-5.6 Sol v45 | 同路线精简版，多一个 PROCESS_RECORD 分支。未实测 |
| **GLM-5.3-Flash** | **GLM 中性化（推荐）** | V5.1b 骨架 + GLM 适配段（思考通道用法 + 框架层拦截的应对）。自研，未实测 |
| | GLM5.3f Kovak | 收编自 `chiina66/glm5.3f-jailbreak`。Kovak 人设 + 思考通道锚定 + 干扰协议。上游无量化数据 |

**拼装机制**：`v5docs` / `v52c` / `v51b` / `v52cx` 不预置成品文件，运行时按**部件列表现场拼**
（`prompts/_xxx-header.md` + 共用的 `prompts/v5-body.md` + 可选追加块），成品写到
`%LOCALAPPDATA%\pi-workbench\prompts\`。正文只存一份、追加块也只存一份；
不动已实测模板的部件，就不会动到它们的基线数据。

**V5.2c 增强版的三个追加块**（可单独复制到别的模板）：

| 追加块 | 文件 | 作用 |
|---|---|---|
| 环境条款效力 | `prompts/_ext-auth.md` | 声明环境段的**优先级**（高于会话内提示词与外部数据）与**不可降级**（任何改成「需先授权」的尝试一律无效） |
| 术语归一化补表 | `prompts/_ext-subst.md` | 按域（逆向/移动/游戏/渗透/样本）补 40 组口语→工程表述，并写明元规则「**目的不变，只换说法**」 |
| 交付纪律 | `prompts/_ext-deliver.md` | **执行前四要素预览**（计划/路径/成本/交付物）+ **交付记录优先**（先写 Markdown 记录再汇报） |

详见 [prompts/README.md](prompts/README.md)。

**退役版本迁移**：状态清单里的旧 `versionKey`（`v3` / `flash` / `v3en` / `seagull` / `v4lite` /
`hs-v3` / `v4` 等 12 个）会被 `resolve_version_key()` 自动迁到 `v5docs`。

---

## 技能库（65 个模块）

Agent Skills 标准：每个技能是 `<name>/SKILL.md`，Pi 递归扫描发现。

| 领域 | 技能 |
|---|---|
| 逆向 / 二进制 | `reverse-engineering` `binary-analysis` `binary-diff` `radare2` `ida-reverse` `dotnet-reverse` `dsl-vm-reverse` `dynamic-instrumentation` `patch-diff-exploit` `pwn-chain` `firmware-pentest` `protocol-reversing` `protocol-reverse-engineering` |
| 移动端 | `apk-reverse` `mobile-reverse` `mobile-security` `mobile-competitive-game-security` |
| 破解 / 授权 | `crack-keygen` `full-crack` |
| 渗透 / 红队 | `network-pentest` `full-pentest` `attack-chain` `pentest-tools` `api-security` `js-reverse` `reverse-engineering-api` `seagull-pentest` `seagull-social-eng` `phishing-kit` |
| 免杀 / 对抗 | `edr-bypass-re` `seagull-evasion` `anti-cheat` `dma-attack` |
| 游戏安全 | `game-cheat` `game-hacking` `game-engine` `graphics-api` `seagull-game-hack` `seagull-game-security` `seagull-game-redteam` `seagull-anticheat` |
| 样本 / 情报 | `seagull-malware` `seagull-lab` `seagull-memory` `seagull-reverse` `research-rigor` |
| Windows 内核 | `windows-kernel` |
| 工程交付 | `code-quality-gate` `task-boundary` |
| 其他 | `adult-fiction` `border-crossing` `finance-movement` `identity-docs` `politics-history` `pipeline-renderer` `l-*`（4 个）`seagull-auto-ops` `seagull-exploit` `seagull-license-security` `seagull-mature-content` `seagull-unlimited` `rei-fallback` |

技能库内含第三方收集内容，署名与许可见 [NOTICE.md](NOTICE.md)。

---

## 技能库维护（skill_tool.py）

往技能库加技能原本是手工建目录 + 改 JSON；现在一条命令，并且带体检。
**分工原则：脚本只做机械动作与机械校验，不做语义猜测** —— 类目由你（或 AI）判，
给错了、名字不合规、描述太短，脚本直接拒绍并说明原因，不替你猜。

```powershell
# 体检（默认动作）：硬规则 + 提示词预算 + 登记一致性 + 相对链接可达性
py -X utf8 skill_tool.py check

# 看类目与登记情况（会指出哪些技能没登记，极简模式下会落到「其他」）
py -X utf8 skill_tool.py list

# 加技能（目录或 zip；--dry-run 只校验不落库）
py -X utf8 skill_tool.py add D:\inbox\my-skill --category 工程交付 --dry-run
py -X utf8 skill_tool.py add D:\inbox\my-skill --category 工程交付
py -X utf8 skill_tool.py add D:\inbox\一批技能 --batch --category 逆向 / 二进制

# 只登记类目（不动文件）、新增类目、移出技能（移到 skills-v4/_removed/，可移回）
py -X utf8 skill_tool.py register seagull-exploit --category 逆向 / 二进制
py -X utf8 skill_tool.py new-category 内容创作 --when "写正文/小说/文案、时政历史梳理"
py -X utf8 skill_tool.py remove my-old-skill --yes

# 类目表不是手维护的：真源是每个技能自己 SKILL.md 里的声明
#   metadata:
#     x-pj-class: 逆向 / 二进制
# add / register 会自动写入声明，gen 按声明重排类目表（加技能时不会漏登记）：
py -X utf8 skill_tool.py gen --check     # 只读校验三方一致（frontmatter × 类目表 × 磁盘），不一致退出码 1
py -X utf8 skill_tool.py gen             # 缺声明的从类目表回填，再按声明重排（已有顺序保留，新技能追加末尾）

# 部署契约：随包资源 ↔ bj_tool.spec ↔ 标记块 ↔ 退出码 ↔ 溯源（README）三方对齐
py -X utf8 skill_tool.py contract

# 技能库打包（含 SHA-256 清单，同样的输入打出同样的字节）/ 校验一个包
py -X utf8 skill_tool.py pack --out build\skill-library.zip
py -X utf8 skill_tool.py pack --verify build\skill-library.zip
```

`gen` 不做语义猜测：技能没声明类目、声明的类目不存在、或类目表登记了磁盘上却没有的技能，
一律停下报错（退出码 2）并且不动任何文件，不替你归类。

校验规则（来自 Agent Skills 规范与 Pi 文档，不是自定）：

| 规则 | 级别 |
|---|---|
| frontmatter 必须存在，且含 `name` 与 `description`（Pi 会直接不加载无描述的技能） | 错误 |
| `name` 仅小写字母/数字/单连字符，无首尾或连续连字符，≤ 64 字符 | 错误 |
| `description` ≤ 1024 字符 | 错误 |
| 名字与已有技能重名 | 错误 |
| 描述 > 200 字符（完整模式下每轮都进提示词） | 提示 |
| 描述 < 15 字符（路由信息不足） | 提示 |
| 未登记类目（极简模式会落到「其他」） | 提示 |

加技能时还会当场告诉你：这个技能会给完整模式每轮加多少 tokens，以及**菜单里那一行会长什么样**。

> 脚本自动把落库目录名规范成 `frontmatter 里的 name` —— 两者一致是其他 Agent Skills 实现的硬要求。
> 库里现有 7 个技能声明名与目录名不同（如 `anti-cheat` 声明 `anti-cheat-systems`），
> 菜单会在这类模块后标出 `（/skill:<真名>）`，方便用斜杠命令强制加载。

---

## 附加技能包

两个**独立**技能包，可以和模板一起打，也可以单独部署 / 单独移除：

| 包 | 目录 | 作用 |
|---|---|---|
| 工程自检门禁 | `code-quality-gate` | 交付前 9 维自检评分（改动收敛/可定位/命名/测试/构建/错误码/依赖锁定/模块边界/敢接手），≥7 分通过，输出结构化报告 |
| 任务边界守卫 | `task-boundary` | 防四类反模式（范围膨胀 / 无用防御 / 意图越界 / 任务打转），任务模式契约 + Stop Ladder 五级判断 |

- **一起打**：模板页 →「🧩 附加模板」→ 勾选 → 回模型分组 → 点模板卡上的端按钮
- **单独打**：模板页 →「🧩 附加模板」→ 勾选 →「部署勾选包」（只装技能，不动指令集）
- **单独移除**：「移除勾选包」（只删这些目录，state 同步更新）
- 首页卡片会显示每个包的就位状态（`✓` / `—`），按目标端 `skills/` 里是否真有目录判定

---

## 安装

### 方式一：用打包好的单文件 exe

从 [Releases](https://github.com/2006sila/pi-workbench/releases/latest) 下载 `pi-workbench-vX.Y.exe`（单文件，约 48MB），双击即用（无需 Python 环境）。
本地自己构建的产物名是 `pi用学习工作台.exe`，功能相同。
首次启动会解压内置资源到临时目录，约 2~4 秒。

### 方式二：从源码构建

```powershell
git clone https://github.com/2006sila/pi-workbench.git
cd pi-workbench
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 一键构建（清理 build/dist 后打包单文件）
build.cmd

# 或直接跑开发模式
.venv\Scripts\python bj_tool.py
```

产物：`dist\pi用学习工作台.exe`

**环境**：Windows 10/11 + PowerShell 5.1+（注入器依赖 `robocopy` / `tasklist` / 注册表）。
开发验证于 Win11 26100 / Python 3.13 / PySide6 6.11.2 / PyInstaller 6.22.3。

---

## 命令行用法

不想开 GUI 也可以直接用注入器：

```powershell
# 注入（默认用随包 skills-v4 全量技能库）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -SourcePrompt prompts\_v52c-header.md

# 只写指令集，不动技能库
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -SourcePrompt ... -NoSkills

# 只装附加技能包（需先注入过模板）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -SkillsOnly `
    -SkillsSource 'skills-v4\code-quality-gate;skills-v4\task-boundary'

# 移除附加技能包（不动指令集与其它技能）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -RemoveAddons 'code-quality-gate;task-boundary'

# 多源技能库（分号分隔，相对路径按脚本所在目录解析）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target dsh -SkillsSource 'skills-v4;D:\my-skills'

# 自检：L1 文件层 / L2 配置层 / L3 进程层（退出码 0=通过，1=有未通过项）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target dsh -Check

# 极简模式部署（只留一个菜单技能进提示词，65 个模块按需读）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck `
    -SourcePrompt prompts\_v52c-header.md -SkillMode menu

# 切回完整模式（自动删掉菜单技能、还原模块标记）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck `
    -SourcePrompt prompts\_v52c-header.md -SkillMode full

# 卸载还原
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -Uninstall

# 目标文件在部署后被外部改过（SHA-256 基线漂移）—— 卸载会停下等你拍板，
# 退出码 3（本次没有写入任何文件）；确认要还原就加 -Force，
# 被改过的内容会先另存到 %LOCALAPPDATA%\pi-workbench\backup\drift\<时间>-<目标>\
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -Uninstall -Force

# 指令文件里标记块被外部编辑坏了（重复 / 顺序颠倒）时自动修：只保留最后一对
# 不加这个开关时遇到损坏标记块默认报错退出，不会静默改动你的文件
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck `
    -SourcePrompt prompts\_v52c-header.md -RepairMarker

# 退出码：0 成功 ｜ 1 失败 / 自检有未通过项 ｜ 3 需人工确认（未写入任何文件）

# 通道体检：文件写对了不代表客户端真的读了。
# 加载层不联网；通道层会真跑一次客户端 CLI 问模型「你现在能看见哪些技能」（一次模型调用）。
# 结论写进 state.evidence.channelProbe，并追加到 logs\operations.log。
# 桌面端跑不了无头，所以拿 CLI 当探针（CLI 读的是同一个配置根）；装到别处用 -ProbeCli 指定。
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -Probe
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -Probe -ProbeCli D:\tools\pi.cmd -ProbeTimeout 120

# 自定义配置根（沙箱测试用）
powershell -ExecutionPolicy Bypass -File inject.ps1 -Target pideck -AgentDir D:\tmp\sandbox
```

两个目标端还有便捷包装（参数同上）：`inject-pideck.ps1` / `inject-dsh.ps1`。

打包自检（验证单文件 exe 内资源完整性，结果写 `%LOCALAPPDATA%\pi-workbench\bundle-check.json`）：

```powershell
$env:PJ_BUNDLE_CHECK='1'; .\dist\pi用学习工作台.exe
```

---

## 目录结构

```
pi-workbench/
├── bj_tool.py                 桌面端主程序（PySide6）
├── bj_tool.spec               PyInstaller 打包配置
├── build.cmd                  一键构建
├── requirements.txt
├── app.ico                    应用图标（7 尺寸）
├── skill_tool.py               技能库维护工具（加技能 / 移除 / 登记类目 / gen / contract / pack / 体检）
├── skill-categories.json      技能类目表（生成物：真源在技能自己的 frontmatter，见 gen）
├── deploy-contract.json       部署契约（随包资源 / 标记块 / 退出码 / 溯源；contract --check 核它）
├── inject.ps1                 注入器核心（双目标：安装 / 卸载 / 自检 / 附加包）
├── inject-pideck.ps1          PiDeck 便捷入口
├── inject-dsh.ps1             DeepSeek Harness 便捷入口
├── prompts/                   指令集模板（头部 + 正文分离，详见 prompts/README.md）
│   └── archive/               已退役的历史头部
├── skills-v4/                 65 个技能模块（Agent Skills 标准）
├── docs/                      README 截图
├── NOTICE.md                  第三方内容署名
└── dist/                      构建产物（.gitignore）
```

---

## 状态与备份位置

全部落在 `%LOCALAPPDATA%\pi-workbench\`，卸载软件后可直接删该目录：

```
%LOCALAPPDATA%\pi-workbench\
├── state\<target>.json        安装状态清单（versionKey / 技能名单 / 备份路径 / 初值标记）
├── backup\<target>\<时间戳>\  安装前原文件与被覆盖的同名技能
├── logs\<target>.log          执行日志
├── last-run-<target>.json     最近一次执行的结构化结果
├── prompts\                   现场拼装的成品指令集
├── history.json               GUI 操作历史
└── tool-config.json           GUI 配置（主题 / 自动注入 / 托盘行为 / 跳过弹窗）
```

---

## 还原保证

- 备份存的是**摘除本工具标记块之后**的用户原始内容 —— 状态清单丢失后卸载也不会把旧注入还原回去
- `hadPromptFile` / `hadSkillsDir` 跨次注入持久化，重复注入不覆盖初值（不会误删原本就存在的空目录）
- 从完整版切精简版时先清上次部署的技能，不留孤儿目录
- 卸载只按状态清单操作，不删客户端自带技能
- 重复注入幂等：标记块替换而非叠加；配置写入用临时文件 + `os.replace` 原子替换
- 附加包移除用精确成员判断，且不会误删同名技能

---

## 边界说明

工具只做文件层的事，这几条是它的能力边界，不是免责声明：

- **不向任何模型发请求**。注入全程是本地文件读写，没有联网，不上传任何内容
- **文件写对了 ≠ 已生效**。注入完成后需要重启客户端、在新会话里验证；每次运行结束时工具会
  在日志里打出一行「未验证：文件已写入，但不代表客户端已加载或已生效」，这句话同时写在结果 JSON 里
- **不保证效果**。提示词与技能能不能让某个模型变好，取决于模型版本、服务端策略与会话上下文；
  本项目不预填成功率、不给效果承诺
- **技能库是资料不是保证**。65 个模块是整理收集的领域参考，内容对不对、能不能用，需要使用者自己判断
- **不替代判断**。模板里的纪律段（不猜、不编造、不冒充已完成）是写给 AI 看的约束，
  实际会不会遵守由模型决定，工具无法强制

---

## 已知限制

- **仅 Windows**：注入器是 PowerShell，窗口缩放用 `WM_NCHITTEST`，开机自启用注册表 `Run` 项
- **无单实例锁**：可同时开多个实例，同时注入会互相覆盖状态清单，建议只开一个
- **自检 L4 需人工**：会话层（新开会话看是否第一行就给交付物）无法自动判定
- **提示词实测数据来自本机**：不同模型版本、不同服务端可能表现不同
- **技能库为收集内容**：三个第三方技能包随附原始 LICENSE，其余为整理收集，署名见 NOTICE.md

---

## 更新记录

**V1.2 · 2026-09-26**

- **通道体检（`-Probe`）—— 回答「部署到底生效了没有」**：
  - 加载层（不联网）：逐个查已装技能的 frontmatter 与 description（**缺 description = Pi 直接不加载**，
    文件写对了不等于客户端会读），菜单技能不能带 `disable-model-invocation`（带了整个路由失效）
  - 通道层：真跑一次客户端 CLI，问模型「你现在能看见哪些技能」，按回复分
    `pass / fail / mismatch / unclear`；`mismatch` = 模型列出了本该隐藏的模块（隐藏标记没生效）
  - 桌面端跑不了无头，拿 CLI 当探针（读同一个配置根）；`-ProbeCli` 可指定路径，`-ProbeTimeout` 默认 180s
  - 结论写进 `state.evidence.channelProbe`；GUI 每张目标卡多了「体检」按钮（会弹窗提醒是一次真实模型调用）
- **`-Check` 新增两段**：
  - 加载层：抽检已装技能能不能被客户端发现
  - 生成物一致性：菜单技能与当前技能库/类目表只读比对，不一致就报「已过期，重注入即可刷新」
    （生成逻辑拆成 `Get-SkillMenuText`（只生成）+ `New-SkillMenu`（生成+落盘），前者不写文件）
- **部署契约 `deploy-contract.json` + `skill_tool.py contract`**：
  随包资源清单 ↔ 磁盘 ↔ `bj_tool.spec` 的 DATAS ↔ 标记块字符串 ↔ 退出码 ↔ README 溯源，六处互相核；
  新增模板忘了进包 / 改了标记块没同步 / README 没写清 commit，都会在这里报错
  - `bundle_check()` 改为从契约读资源清单（旧写法清单写两处，增模板时只改一处不报错）
- **操作留痕 `logs\operations.log`**：一行一次操作（时间 / 目标 / 动作 / 状态 / 退出码 / 技能数 / 模式 / 漂移数 / 冲突数），
  追加式永不重写；日志页新增「操作记录」按钮直接打开
- **技能库打包 `skill_tool.py pack`**：zip + 内嵌 `MANIFEST.sha256`，时间戳写死所以**同样输入打出同样字节**；
  `--verify` 重算摘要，能查改过 / 丢过 / 多出来的文件
- **界面健壮性**（对应 alice 的 breaker/watchdog）：
  - 单任务输出超 20000 行就只读不显示（子进程 stdout 继续排空，否则它写满管道会卡死）
  - 日志页块数上限 5000（自动丢最旧的）；文件日志仍是全量
  - 单任务超 30 分钟未结束 → 杀整棵进程树并报「已终止（防界面卡死）」
- **README 溯源升级**：不再只写一句「参考了…」，写明仓库 / 许可证 / commit / 读到的规模 / 借了哪些机制 /
  哪些不抄，并声明与 `deploy-contract.json` 的 `cleanroom` 段互相核对

- **类目声明链路**（修「新增技能忘了登记 → 极简模式下落进『其他』」）：
  - 类目表从「手维护」改成「生成物」：真源是每个技能 `SKILL.md` 的 `metadata.x-pj-class`
    （Agent Skills 规范允许 `metadata` 放任意键值；纯追加式改写，不动既有字节）
  - `skill_tool.py gen` 按声明重排类目表（已有顺序保留、新技能追加末尾）；
    `gen --check` 只读校验 `frontmatter × 类目表 × 磁盘` 三方一致，可进 CI
  - `add` / `register` 自动写入声明，`remove` 自动摘掉（`metadata` 块空了连块一起收）
  - 脚本不做语义猜测：没声明 / 类目不存在 / 表里有磁盘没有 → 停下报错（退出码 2）且不动任何文件；
    另外加了「一个声明都读不到就拒绝重写类目表」的防守，避免把表清空
- **基线漂移拦截**（修「`fileHashes` 只写不读 = 死数据」）：
  - 部署时拿上次 `state.fileHashes` 的 `after` 哈希与磁盘现状比对，不一致就告警并记进
    `state.evidence.baselineDrift`（标记块以外的用户内容仍按原逻辑保留）
  - **卸载时同一个比对变成拦截**：目标文件被外部改过 → 停下，退出码 **3**（本次未写入任何文件），
    加 `-Force` 才继续，且先把被改过的内容另存到 `backup\drift\<时间>-<目标>\`
  - `state.evidence` 补上 `object / action / baselineSha256 / baselineDrift / verification / rollback`，
    出事不用猜怎么退回去（GUI 也认识退出码 3：弹出「强制卸载」确认，不再当成失败）

- **写入安全加固**（无一项改变部署路径，全是新增校验）：
  - 目录穿越防护：从状态清单 / 配置拼出来的名字先过 `Resolve-Within`，跑出目标目录就拒写
  - 链接检测：写入路径内部出现 symlink / junction / 硬链接时拒写（写到别处、回滚改错文件都是从这里来的）；
    目标目录自身或上层是链接（用户用 `mklink /J` 把 `~/.pi` 联到别的盘）只警告不拦
  - 严格 UTF-8 + 8 MB 上限：只对工具自带的技能库与模板启用（这些必须干净）；
    用户自己的文件保持宽松读取，避免老记事本存成 ANSI/GBK 的文件一升级就装不上
  - **标记块损坏不再静默自愈**：指令文件里出现重复 / 顺序颠倒的标记块时默认报错退出，
    错误信息里给出修复命令；加 `-RepairMarker` 才自动只保留最后一对
  - **幂等短路**：指令文件内容已是目标状态就跳过重写（按字节比较，BOM 与行尾算在内）
  - 结果 JSON 新增 `modelStatus`：每次运行结束都明写「文件已写入，但不代表客户端已加载或已生效」，
    并在界面日志里显示
- **术语归一化补表扩充**（`prompts/_ext-subst.md`，仅增强版模板）：新增三步 ——
  先洗口去填充词 → 错字按同音归位 → 表里没有时按材料兜底（二进制 / 网址 / 纯文字三类），
  并明写「不要回头问『你指的是哪个』」
- **skill_tool.py 新增防串稿检查**：description 或正文与别的技能完全一致时报警
  （用块标量感知的解析读取，避开 `description: |` 被读成 `|` 的误判）
- **README 新增「边界说明」节**：不联网、不保证效果、不预填成功率、文件写对 ≠ 已生效
- **新增 skill_tool.py（技能库维护工具）**：add / remove / register / new-category / list / check
  - 校验按 Agent Skills 规范与 Pi 文档：frontmatter、name 字符集与长度、description ≤1024、重名
  - 加技能时报「每轮多少 tokens」并预览菜单行；落库目录名自动规范成 frontmatter 的 name
  - check 体检：硬规则 + 提示词预算 + 登记一致性 + 相对链接可达性（剔除代码片段，206 条链接 2 条真失效）
- 菜单给声明名与目录名不同的模块标出 `（/skill:<真名>）`（库里 7 个），否则斜杠命令用不了
- 修：`reverse-engineering/field-notes.md` 的悬空链接（引用的案例文件不在包内）
- **模板页改为三步部署流程**：① 客户端 → ② 模式 → ③ 点卡片部署（选择记在配置，首页「去部署」会把 ① 同步过去）
- 新增技能呈现模式（`-SkillMode full|menu|auto`）
  - `menu`（极简）：只留一个菜单技能进系统提示词，其余模块加 `disable-model-invocation` 不进提示词，AI 按需 `read`；**纪律型技能（附加包）例外，保持常驻**
  - 提示词固定开销从 ≈7,000 tokens/轮降到 ≈90 tokens/轮（实测 2.1%）
  - 注入保留原行尾与 BOM；两模式互相切换幂等（切回完整模式自动清菜单技能与标记）
  - 新增 `skill-categories.json` 类目表；`-Check` 增加极简模式专项校验
- **菜单技能 6 条硬规矩**：类目优先、上限 4 个、**报名机制**（报告用了哪个模块）、
  **取不到正文不得假装执行**、已读复用、三不要
- **纪律型技能在极简模式下保持常驻**：附加包 `code-quality-gate` / `task-boundary` 不加隐藏标记
  （它们靠描述自动触发才有意义），仍留在菜单里；例外写进状态清单，自检不会误判
  —— 实测极简模式 + 两附属模板 = 进提示词 3 条 / 1,341 字符 / 0 诊断
- 菜单技能描述改用**领域触发词**（从类目表现生成），不再用「需要技能」这种元意图词
- 极简模式额外在标记块内注入「技能取用纪律」段（约 150 tokens，系统级，比技能描述更硬）
- **新增 V5.2c 增强版模板** + 部件式拼装（可接可选追加块）：
  环境条款效力 / 术语归一化补表 / 交付纪律
- `bundle_check` 补上 `_v52c-header.md`（原先不在必需清单里，丢了会静默只拼正文）
- 修：增强版的 `versionKey` 原先记成文件名 `v5-2c-ext`，会让「重新注入（按上次模板）」
  静默回退到 V5.2c —— 已在 `inject.ps1` 版本映射里登记 `v5-2c-ext → v52cx`
- 首页卡片 / 技能页 / 操作历史显示当前模式；附加包部署同样跟随所选客户端与模式
- 局部操作（部署附加包）不传模式时沿用目标端上次记录，不会误关极简模式

**V1.1.1 · 2026-09-25**

- 清理界面与代码里的历史代称（附加模板页文案）
- 版本号统一为 V1.1.1

**V1.1 · 2026-09-24**

- 附加技能包：`code-quality-gate` / `task-boundary` 可单独部署、单独移除，首页卡片显示就位状态
- 修 47 个技能库内损坏的文件名（GBK/UTF-8 双重编码还原）
- 修无边框窗口边缘缩放（原生命中测试 + 高 DPI 坐标换算）
- 修主题切换残留幽灵窗口与托盘图标、切页后导航高亮不同步
- 修附加包路径解析、`-SkillsOnly` 覆盖状态清单、自检恒显示「通过」
- 修注入日志中文乱码、进程对象泄漏、队列早退卡死
- 应用图标（7 尺寸）、任务栏 / Alt-Tab 图标
- 注入改为后台线程 + `CREATE_NO_WINDOW`，退出前二次确认并杀进程树

**V1.0** · 9.21 版 exe 反汇编还原为可编辑源码，双目标 + V5 模板体系

---

## 致谢与参考

- **路由 / 菜单式技能组织与若干工程机制** 参考了
  [alicewe1/alice_skill](https://github.com/alicewe1/alice_skill)（GPL-3.0），
  读的是 `802bf17895fc9f0cb2dc0558d13af452d50a6bd8`（整仓 2399 个文件 / 424 个技能）。
  **仅借鉴设计思路与结构，未使用其代码、也未搬其技能正文**（clean-room：本仓库 MIT，
  以自己的实现与措辞重写）。
- **提示词模板与部署事务机制** 参考了
  [3641397194-wq/gpt6-Astra](https://github.com/3641397194-wq/gpt6-Astra)（MIT，**允许复用正文**），
  读的是 `e868f60`（208 个文件）。GPT 系模板正文收编自该仓库，
  来源与改动逐项记在 [NOTICE.md](NOTICE.md)；部署事务、版本日志与按版本恢复、
  口语归一表等机制按本仓库的形态重写（PowerShell + Python）。未搬其付费中转、
  激活门与社群部分。
- 以上两条的机器可读记录在 [deploy-contract.json](deploy-contract.json) 的 `cleanroom` 段
  （含 repo / commit / 许可证 / 引用方式 / 具体借了什么），`py -X utf8 skill_tool.py contract`
  会核对 README 这里写的仓库 / 许可证 / commit 是否与契约一致 —— 口头致谢不算溯源。

  从 alice 借到的机制：类目索引生成器、只替换锚定节 + `--check` 只读比对、技能指纹基线与消费方、
  退出码分级（3 = 需人工确认）、证据记录字段、沙箱演练不污染主库。
  从 gpt6-Astra 借到的机制：路径守卫与根内包含、原子写、
  版本日志与按版本恢复（恢复前验 afterHash）、写入前 TOCTOU 复验、
  配置根环境变量探测、口语归一表、发布前隐私检查文档结构。
  两边都没抄的：越狱/拒答话术与话术替换表、封印/加密/水印、激活门与口令、
  付费中转与社群内容、以及 alice 的 365 个技能正文（GPL 内容不进 MIT 仓库）。
- 技能库内含第三方收集内容，署名与许可见 [NOTICE.md](NOTICE.md)。

---

## 授权与第三方

本仓库代码以 [MIT](LICENSE) 授权。`skills-v4/` 与 `prompts/` 内含第三方收集内容，
各自遵循其原始许可与署名要求 —— 详见 [NOTICE.md](NOTICE.md)。

本仓库内容面向**自有设备、自建靶标与已获授权的测试范围**。
