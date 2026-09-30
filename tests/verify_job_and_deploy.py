# -*- coding: utf-8 -*-
"""两项「工程件」验收（沙箱）：

  A. Job Object 进程树回收（bj_tool.py 的 JobObject）—— 比 taskkill /T 可靠
     1) 作业可用、能分配、能终止
     2) 分配失败（不存在的 pid）要优雅返回 False，不能让功能整体失效
     3) 关掉作业句柄 → 作业内进程（含孙进程）被内核清掉
     4) Runner 真的把子进程挂进了作业

  B. 自我部署脚本 deploy-self.ps1
     1) -WhatIfOnly 只打印、不做任何写操作
     2) 实际部署：备份 .rollback-<时间戳> → 覆盖 → 打印回滚命令
     3) 重复部署：回滚备份按 -KeepRollbacks 清理
     4) 构建产物与目标同一文件时拒绝（防自我覆盖）

跑法： py -X utf8 tests\verify_job_and_deploy.py
"""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjjob')
PKG = os.path.join(SBX, 'pkg')
DIST = os.path.join(SBX, 'dist')
DEPLOY = os.path.join(ROOT, 'deploy-self.ps1')
EXE = 'pi-workbench-v1.3.exe'
DST = os.path.join(PKG, EXE)

P = F = 0


def chk(ok, tag, detail=''):
    global P, F
    P, F = (P + 1, F) if ok else (P, F + 1)
    print('  [%s] %-30s %s' % ('PASS' if ok else 'FAIL', tag, detail))


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else ''


def ps_capture(args):
    """跑 PowerShell 并把输出按 utf-8 与 GBK 两种方式各解一次拼起来。

    为什么两种都解：PowerShell 重定向到管道时，中文走的是 OEM 码页（GBK），
    而脚本源码/日志是 UTF-8 —— 按固定在 utf-8 去搜中文会全都搜不到。
    （这是测试器的问题，不是产品的问题：产品的退出码与文件效果都正常。）
    """
    pr = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File'] + args,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    raw = pr.stdout
    txt = raw.decode('utf-8', 'replace') + '\n' + raw.decode('gbk', 'replace')
    return pr.returncode, txt


def load_tool():
    spec = importlib.util.spec_from_file_location('bj_tool', os.path.join(ROOT, 'bj_tool.py'))
    m = importlib.util.module_from_spec(spec)
    os.environ['PJ_SKIP_AGREEMENT'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    spec.loader.exec_module(m)
    return m


shutil.rmtree(SBX, ignore_errors=True)
os.makedirs(PKG, exist_ok=True)
os.makedirs(DIST, exist_ok=True)

m = load_tool()

print('=== A1 Job Object：可用 / 分配 / 终止 ===')
job = m.JobObject()
chk(job.available, 'job-available', 'KILL_ON_JOB_CLOSE 作业已建立=%s' % job.available)
chk(job.assign(0) is False, 'assign-invalid-pid-false', '非法 pid 返回 False（不抛异常）')
chk(job.assign(999999) is False, 'assign-missing-pid-false', '不存在的 pid 返回 False（走 taskkill 兜底）')

print('=== A2 关句柄 → 作业内进程（含孙进程）被清掉 ===')
# 子进程再起一个孙进程，两者都进入作业；关句柄后两个都应消失
grand_ok = False
if job.available:
    child = subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-Command',
         '$p = Start-Process powershell -PassThru -ArgumentList "-NoProfile","-Command","Start-Sleep 120"; '
         'Write-Output $p.Id; Start-Sleep 120'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    grand_pid = None
    t0 = time.time()
    while time.time() - t0 < 10:
        line = child.stdout.readline().strip()
        if line.isdigit():
            grand_pid = int(line)
            break
    assigned = job.assign(child.pid)
    if grand_pid:
        job.assign(grand_pid)          # 孙进程也挂进去（真实场景里由内核随父进程继承）
    time.sleep(0.6)

    def alive(pid):
        if not pid:
            return False
        out = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                              '(Get-Process -Id %d -ErrorAction SilentlyContinue) -ne $null' % pid],
                             stdout=subprocess.PIPE, text=True, check=False).stdout.strip()
        return out.lower().startswith('true')

    before_child, before_grand = alive(child.pid), alive(grand_pid)
    job.terminate()                    # 等价于「关句柄」：内核杀作业内全部进程
    job.close()
    time.sleep(1.2)
    after_child, after_grand = alive(child.pid), alive(grand_pid)
    grand_ok = before_child and before_grand and not after_child and not after_grand
    chk(grand_ok, 'job-kills-tree',
        '分配=%s 终止前 子=%s 孙=%s → 终止后 子=%s 孙=%s' % (assigned, before_child, before_grand,
                                                       after_child, after_grand))
    try:
        child.kill()
    except Exception:
        pass
else:
    chk(False, 'job-kills-tree', '作业不可用，跳过')

print('=== A3 Runner 把子进程挂进作业 ===')
r = m.Runner(['powershell.exe', '-NoProfile', '-Command', 'Start-Sleep 30'])
r.start()
time.sleep(1.5)
assigned = bool(r._job and r._job.assigned)
chk(bool(r._job and r._job.available) and assigned, 'runner-assigns-job',
    'runner._job.assigned=%s' % assigned)
r.close_job()
r.terminate_tree()
time.sleep(0.4)

print('=== B1 -WhatIfOnly：只打印、不写 ===')
open(DST, 'w', encoding='utf-8').write('OLD-CONTENT')
open(os.path.join(DIST, EXE), 'w', encoding='utf-8').write('NEW-CONTENT')
before = sha(DST)
code, out = ps_capture([DEPLOY, '-Pkg', PKG, '-Built', os.path.join(DIST, EXE), '-ExeName', EXE, '-NoStart', '-WhatIfOnly'])
rollbacks = [f for f in os.listdir(PKG) if '.rollback-' in f]
chk(code == 0 and sha(DST) == before and not rollbacks, 'whatif-no-writes',
    'exit=%d 目标未变=%s 无备份=%s' % (code, sha(DST) == before, not rollbacks))
chk('回滚命令' in out and 'WhatIfOnly' in out, 'whatif-prints-rollback', '计划里给了确切回滚命令')

print('=== B2 实际部署：备份 → 覆盖 → 打印回滚命令 ===')
code, out = ps_capture([DEPLOY, '-Pkg', PKG, '-Built', os.path.join(DIST, EXE), '-ExeName', EXE, '-NoStart'])
rollbacks = sorted(f for f in os.listdir(PKG) if '.rollback-' in f)
new_sha = sha(DST)
chk(code == 0 and open(DST, encoding='utf-8').read() == 'NEW-CONTENT', 'deploy-copies',
    'exit=%d 目标已更新=%s' % (code, open(DST, encoding='utf-8').read() == 'NEW-CONTENT'))
chk(len(rollbacks) == 1 and open(os.path.join(PKG, rollbacks[0]), encoding='utf-8').read() == 'OLD-CONTENT',
    'deploy-backs-up-old', '备份=%s 内容=旧版' % (rollbacks[0] if rollbacks else '—'))
chk(new_sha[:12] in out, 'deploy-prints-sha', '输出里带了 SHA256')
chk(rollbacks and ('回滚命令' in out and rollbacks[0] in out), 'deploy-prints-rollback',
    '回滚命令指向刚才那份备份')

print('=== B3 重复部署：回滚备份按 KeepRollbacks 清理 ===')
for _ in range(5):
    subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', DEPLOY,
                    '-Pkg', PKG, '-Built', os.path.join(DIST, EXE), '-ExeName', EXE,
                    '-NoStart', '-KeepRollbacks', '3'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    time.sleep(1.05)      # 让时间戳（秒级）不同，才能看出保留策略
left = [f for f in os.listdir(PKG) if '.rollback-' in f]
chk(len(left) == 3, 'rollback-retention-3', '剩 %d 份（期望 3）' % len(left))

print('=== B4 构建产物 == 目标：拒绝（防自我覆盖）===')
code, out = ps_capture([DEPLOY, '-Pkg', PKG, '-Built', DST, '-ExeName', EXE, '-NoStart'])
chk(code != 0 and '同一个文件' in out, 'same-file-refused',
    'exit=%d 提示=%s' % (code, '同一个文件' in out))

print()
print('==== TOTAL pass=%d fail=%d ====' % (P, F))
sys.exit(1 if F else 0)