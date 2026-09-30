# tests/ · 验收脚本

这些脚本**在沙箱里跑**：临时造 `USERPROFILE` / `LOCALAPPDATA`（`%TEMP%\pjverify*`），
不碰你真实的 `~/.pi/agent`、`~/.dsh` 与 `%LOCALAPPDATA%\pi-workbench`。
每个用例都在 `pass/fail` 后打印关键数值，失败时先看那一行。

```powershell
# 一次跑完（注入核心 + 工程件 + 仓库门禁），发版前最后一道
py -X utf8 tests\run_all.py

# 单跑
py -X utf8 tests\verify_inject.py           # 注入核心
py -X utf8 tests\verify_job_and_deploy.py   # Job Object / 自我部署脚本
```

## 覆盖什么

| 脚本 | 覆盖 |
|---|---|
| `verify_inject.py` | 标记块**只认关键串**（旧版本 v3 / 旧变体载荷 `prompt=x.md` 都能认出并**原地升级**，不追加第二块）· 幂等 · 用户内容保护 · **备份保留**（时间戳备份留 10 份，状态清单引用的唯一原件永不清理）· **占位符断言**（模板残留 `{{...}}` 拒绝写入）· 漂移拦截（`exit 3`）· 卸载还原 |
| `verify_job_and_deploy.py` | **Job Object**：作业可用 / 非法 pid 优雅失败 / 关句柄后内核清掉**孙进程** / Runner 真把子进程挂进作业；**deploy-self.ps1**：`-WhatIfOnly` 零写入、备份 `.rollback-<时间戳>`、打印 SHA256 与回滚命令、回滚备份按 `-KeepRollbacks` 清理、构建产物==目标时拒绝 |
| `run_all.py` | 上面两个 + 仓库门禁（`contract` / `gen --check` / `notice` / `check`） |

## 为什么放在仓库里

这些脚本以前放在 `%TEMP%`（`D:\tmp`）下，被系统清理过一次——测试跟着一起没了。
放进仓库后它们可复现、可随代码走，也方便发版前跑一遍。

## 安全约定（硬性）

**测试不得杀任何真实进程。** 所有套件在导入时设 `PJ_TEST_NO_KILL=1`，
`bj_tool.kill_procs` / `safe_kill_procs` 在该环境下直接空转并返回说明。

> 事故记录：一次自检真的把 PiDeck 的 3 个进程杀了（宿主进程靠 `ancestor_pids()` 才保住）。
> 产品侧据此加了「绝不杀自己的祖先进程」；测试侧加了上面这道硬闸。
> Job Object 的**内核级**回收不受影响 —— 那测的是作业隔离，只影响测试自己 spawn 的子进程。

## 写新用例时的两条约定

1. **沙箱**：用 `%TEMP%\pj<名字>` 造 `USERPROFILE`/`LOCALAPPDATA` 再调 `inject.ps1`，别碰真实配置。
2. **PowerShell 输出解码**：重定向到管道时中文走 OEM 码页（GBK），
   按 utf-8 单解会搜不到中文。参考 `verify_job_and_deploy.py` 的 `ps_capture()`
   （两种都解一次再搜）。