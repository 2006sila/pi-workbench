# 发布前安全检查（V1.2）

结论先写：**本工具自身不发任何网络请求**；唯一的对外流量来自「通道体检」时你自己客户端 CLI 的那次模型调用。
下面每一条都附可复现命令，不是自查声明。

## 一、检查范围

| 范围 | 说明 |
|---|---|
| 本仓库源码 | `bj_tool.py` / `skill_tool.py` / `inject*.ps1` / `build.cmd` / `bj_tool.spec` |
| 待提交资源 | `prompts/` `skills-v4/` `skill-categories.json` `deploy-contract.json` |
| git 历史 | 提交内容（非工作区快照） |
| 构建产物 | `dist\pi用学习工作台.exe`（单文件，随包资源由 `bundle_check` 校验） |

**未检查**：第三方技能包内部文本的准确性（它们有自己的来源与许可，见 [NOTICE.md](../NOTICE.md)）、
客户端（PiDeck / DSH）自身的行为、以及任何真实账号与服务端。

## 二、密钥与隐私扫描结果

高置信度模式扫描（工作区 + git 历史）：

```
sk-[A-Za-z0-9]{20,} | ghp_[A-Za-z0-9]{20,} | AKIA[0-9A-Z]{16} | BEGIN ... PRIVATE KEY | xoxb-...
```

- **工作区命中 4 处，全部位于第三方技能包的「载荷/演练文档」内**，是作为示例文本出现的字面量
  （`skills-v4/pentest-tools/src-hunter/references/...`：LLM 训练数据提取演练里的 `"The API key is sk-"`
  与其他密钥格式样例）。不是真实凭据，也不属于本工具代码。
- **git 历史未发现额外的命中**（同一批第三方文档除外）。
- 未被 git 跟踪的敏感文件名检查：无 `.env` / `.pem` / `.key` / `credential*` / `auth.json` 入库。
- 本机真实凭据（`~/.pi/agent/auth.json` 等）在仓库之外的客户端目录里，本工具**只读不写**它们；
  仓库不存放、不导出任何 API Key。

复现：

```powershell
cd <仓库根>
git grep -nE "sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY"
git ls-files | Select-String -Pattern "\.env|credential|secret|\.key$|\.pem$"
```

## 三、读写与网络面

| 面 | 事实 |
|---|---|
| 网络 | 工具自身（Python / PowerShell）**无 HTTP 客户端**：`bj_tool.py` 不含 `urllib/requests/socket`，`inject.ps1` 不含 `Invoke-WebRequest/WebClient`。唯一外联发生在 `-Probe`：它 `Start-Job` 调用你自己的 `pi`/`dsh` CLI，由那个 CLI 发起一次模型调用 |
| 写入 | 只写目标配置根（`~/.pi/agent`、`$DSH_HOME`、或 `-AgentDir` 指定目录）下的指令文件、技能目录、补丁层；本工具的工作目录写状态清单 / 备份 / 日志 / 版本日志 |
| 读取 | 目标配置根内的指令文件与技能、`skill-categories.json`、随包模板；**不读剪贴板、不读环境变量里除配置根之外的任何变量、不遍历用户主目录找凭据** |
| 进程 | `taskkill /T` 只用于结束自己启动的 PowerShell 子进程树（卸载/退出场景），或用户点击「重启」时结束目标客户端进程 |
| 外部链接 | 日志/文本区域 `setOpenExternalLinks(False)`；「操作记录」按钮只 `os.startfile` 打开本机的 `operations.log` |
| 路径守卫 | 写入前检查路径链上的 symlink / junction / 硬链接（穿透写入与回滚改错文件都从这里来）；从状态清单拼出的名字过 `Resolve-Within`，越界拒写 |
| 目录探测 | 配置根优先读客户端自己的环境变量（`PI_CODING_AGENT_DIR` / `DSH_HOME`），并在日志里报出来源；不用猜的路径写文件 |

## 四、已实现的对策（可验证）

| 风险 | 对策 | 验证 |
|---|---|---|
| 写到一半被杀 → 半截文件 | 原子写（同目录临时文件 + 替换，失败回退拷贝） | `verify.py` · `verify_tx.py` |
| 中途失败留下孤儿技能 | 回滚日志 + 脚本级 trap 逆序回滚 | `verify_tx.py`（强制中断 5 项回滚） |
| 把用户手写的改动盖掉 | 基线漂移检测：卸载/恢复前验 `afterHash`，默认停下（`exit 3`），`-Force` 才继续并先另存现场 | `verify_tx.py` · `verify_versions.py` |
| 算完哈希到写入之间被改（TOCTOU） | 写前逐文件再验一次 `beforeHash` | `inject.ps1` `Assert-Unchanged`（代码路径；无独立用例） |
| 同名技能被覆盖 | 覆盖前整目录备份，卸载时还原 | `verify_tx.py`（覆盖后中断 → 还原成原文） |
| 标记块重复/颠倒 | 默认报错退出并给出修复命令；`-RepairMarker` 才自动只留最后一对 | `verify.py` |
| 打包漏文件 | 随包资源清单来自 `deploy-contract.json`，`contract` 校验它与 `bj_tool.spec` 的 DATAS 一致 | `verify_contract.py` |
| 「写对了但客户端没加载」 | 加载层体检（缺 `description` 会被静默跳过）+ `-Probe` 真跑一次问模型 | `verify_probe.py` |
| 界面被输出刷死 / 任务卡死 | 单任务输出上限 20000 行、日志块上限 5000、单任务超 30 分钟杀进程树 | `regress2.py` |

## 五、未验证项（不要当成已通过）

1. **模型侧效果**：文件写对 ≠ 客户端已加载 ≠ 已生效。`state.evidence.channelProbe` 是唯一有实测证据的字段，
   其余情况一律按「未验证」看待（`modelStatus` 字段就是这个意思）。
2. **真实付费请求**：本仓库不发起；`-Probe` 用的是你本机客户端的通道与额度。
3. **服务端行为**：客户端加载顺序、缓存、以及任何中转/服务端配置，超出本工具范围。
4. **第三方技能包内容**：`skills-v4/` 内的命令、载荷与文本按其原始许可随附，未逐条复核准确性。
5. **多用户 / 多账号并发的同一配置根**：本工具按「一台机器一个使用者」假设设计；并发写同一配置文件时靠
   漂移检测拦下，但不做文件锁。

## 六、复现检查

```powershell
py -X utf8 skill_tool.py contract          # 随包资源 ↔ spec ↔ 标记块 ↔ 退出码 ↔ 溯源
py -X utf8 skill_tool.py check             # 技能库硬规则 / 预算 / 串稿 / 链接
py -X utf8 skill_tool.py pack --out build\lib.zip && py -X utf8 skill_tool.py pack --verify build\lib.zip
py -X utf8 D:\tmp\verify.py                # 写入安全 11 项
py -X utf8 D:\tmp\verify_tx.py             # 事务/漂移/留痕 23 项
py -X utf8 D:\tmp\verify_versions.py       # 环境变量配置根 / 版本恢复 18 项
dist\pi用学习工作台.exe                    # 打包自检（PJ_BUNDLE_CHECK=1）
```
