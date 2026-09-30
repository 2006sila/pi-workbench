# -*- coding: utf-8 -*-
"""pi-workbench 核心注入验收（沙箱，不碰真实配置）

覆盖：
  1. 标记块「只认关键串」—— 旧版本 / 旧变体载荷都能被认出并**原地升级**，不会追加第二块
  2. 备份保留策略 —— 时间戳备份留最近 N 份；被状态清单引用的「唯一原件」永不清理
  3. 生成物占位符断言 —— 模板残留 {{...}} 时拒绝写入
  4. 核心回归 —— 幂等 / 用户内容保护 / 漂移拦截 / 卸载还原

跑法： py -X utf8 tests\verify_inject.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SB = os.path.join(ROOT, 'inject.ps1')
SP = os.path.join(ROOT, 'prompts', '_v52c-header.md')
SK = os.path.join(ROOT, 'skills-v4')
SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjverify')
HOME = os.path.join(SBX, 'home')
LOCAL = os.path.join(SBX, 'local')
AGENT = os.path.join(HOME, '.pi', 'agent')
PROMPT = os.path.join(AGENT, 'APPEND_SYSTEM.md')
SKILLS = os.path.join(AGENT, 'skills')
STATE = os.path.join(LOCAL, 'pi-workbench', 'state', 'pideck.json')
BACKUPS = os.path.join(LOCAL, 'pi-workbench', 'backup', 'pideck')
DRIFT = os.path.join(LOCAL, 'pi-workbench', 'backup', 'drift')

P = F = 0


def chk(ok, tag, detail=''):
    global P, F
    P, F = (P + 1, F) if ok else (P, F + 1)
    print('  [%s] %-30s %s' % ('PASS' if ok else 'FAIL', tag, detail))


def env():
    e = dict(os.environ)
    e['USERPROFILE'] = HOME
    e['LOCALAPPDATA'] = LOCAL
    e['DSH_HOME'] = os.path.join(HOME, '.dsh')
    return e


def run(*args):
    r = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SB] + [str(a) for a in args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env(), check=False)
    return r.returncode, r.stdout.decode('utf-8', 'replace')


def rd(p):
    return open(p, encoding='utf-8').read() if os.path.exists(p) else ''


def st():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def counts(text, key='<!-- BEGIN pi-workbench'):
    return (text.count(key), text.count(key.replace('BEGIN', 'END')))


shutil.rmtree(SBX, ignore_errors=True)
for d in (HOME, LOCAL, AGENT):
    os.makedirs(d, exist_ok=True)

print('=== 1 基线部署：标记块恰一份（按关键串匹配）===')
open(PROMPT, 'w', encoding='utf-8', newline='').write('用户自己原有的一行\n\n原有正文。\n')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK)
b, e = counts(rd(PROMPT))
chk(c == 0 and b == 1 and e == 1, 'deploy-marker-once', 'exit=%d BEGIN=%d END=%d' % (c, b, e))
chk('pi-workbench v4' in rd(PROMPT), 'marker-current-version', '写的是当前版本载荷')

print('=== 2 旧版本 / 旧变体载荷：都能认出并原地升级，不追加第二块 ===')
for legacy in ('<!-- BEGIN pi-workbench v3 -->\nX\n<!-- END pi-workbench v3 -->',
               '<!-- BEGIN pi-workbench prompt=old.md -->\nX\n<!-- END pi-workbench prompt=old.md -->'):
    txt = rd(PROMPT)
    new = re.sub(r'(?s)<!-- BEGIN pi-workbench.*?<!-- END pi-workbench[^>]*-->', legacy, txt, count=1)
    open(PROMPT, 'w', encoding='utf-8', newline='').write(new)
    c, o = run('-Target', 'pideck', '-Check')
    c2, o2 = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK)
    b, e = counts(rd(PROMPT))
    tag = 'legacy-%s' % ('v3' if 'v3' in legacy else 'payload')
    chk(c == 0 and '标记块版本为' in o and b == 1 and e == 1 and 'pi-workbench v4' in rd(PROMPT),
        tag, 'Check报旧版本=%s 升级后 BEGIN=%d END=%d' % ('标记块版本为' in o, b, e))

print('=== 3 幂等：内容相同不重复写 ===')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK)
chk(c == 0 and '未重复写入' in o, 'idempotent', '')

print('=== 4 用户内容保护：标记块外的改动不丢 ===')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK)
body = rd(PROMPT)
chk('用户自己原有的一行' in body and '原有正文。' in body, 'user-content-kept', '')

print('=== 5 备份保留：时间戳备份留 10 份，钉住的唯一原件不删 ===')
pinned = [str(st().get('promptBackup') or '')]
for i in range(15):  # 造一批「比真实备份更新」的假备份，把真实那份挤出保留窗口
    d = os.path.join(BACKUPS, '20991231-1200%02d' % i)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, 'APPEND_SYSTEM.md'), 'w', encoding='utf-8').write('fake\n')
os.makedirs(DRIFT, exist_ok=True)
for i in range(13):
    os.makedirs(os.path.join(DRIFT, '20991231-1300%02d-pideck' % i), exist_ok=True)
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK)
stamp_dirs = [d for d in os.listdir(BACKUPS) if re.match(r'^\d{8}-\d{6}$', d)]
fake_left = [d for d in stamp_dirs if d.startswith('20991231')]
drift_left = [d for d in os.listdir(DRIFT) if d.startswith('20991231')] if os.path.isdir(DRIFT) else []
chk(c == 0 and len(fake_left) == 10, 'prune-keeps-10', '假备份剩 %d 份（期望 10）' % len(fake_left))
chk(pinned[0] and os.path.exists(pinned[0]), 'pinned-original-survives',
    '状态清单指向的备份仍在：%s' % (os.path.basename(os.path.dirname(pinned[0])) if pinned[0] else '—'))
chk(len(drift_left) == 10, 'prune-drift-10', 'drift 剩 %d 份（期望 10）' % len(drift_left))

print('=== 6 占位符断言：模板残留 {{...}} 时拒绝写入 ===')
bad = os.path.join(SBX, 'bad-template.md')
open(bad, 'w', encoding='utf-8', newline='').write('规范正文\n\n未展开：{{SKILLS_ROOT}} 与 {{TOOL}}\n')
before = rd(PROMPT)
c, o = run('-Target', 'pideck', '-SourcePrompt', bad)
chk(c == 1 and '占位符' in o and rd(PROMPT) == before, 'unrendered-refused',
    'exit=%d 提示=%s 文件未动=%s' % (c, '占位符' in o, rd(PROMPT) == before))

print('=== 7 漂移拦截：部署后手改 → 卸载默认拦下（exit 3）===')
open(PROMPT, 'a', encoding='utf-8', newline='').write('\n<!-- 用户后来加的 -->\n')
snap = hashlib.sha256(open(PROMPT, 'rb').read()).hexdigest()
c, o = run('-Target', 'pideck', '-Uninstall')
chk(c == 3 and hashlib.sha256(open(PROMPT, 'rb').read()).hexdigest() == snap, 'drift-refused',
    'exit=%d 文件未动=%s' % (c, hashlib.sha256(open(PROMPT, 'rb').read()).hexdigest() == snap))
c, o = run('-Target', 'pideck', '-Uninstall', '-Force')
# -Force 的语义 = 「明确同意覆盖改动」：目标文件会被还原成安装前内容，
# 而改动本身会先另存到 backup\drift\ —— 所以该查副本，不是查目标文件。
drift_hit = []
if os.path.isdir(DRIFT):
    for d in os.listdir(DRIFT):
        p = os.path.join(DRIFT, d, 'APPEND_SYSTEM.md')
        if os.path.exists(p) and '用户后来加的' in rd(p):
            drift_hit.append(d)
chk(c == 0 and bool(drift_hit) and '用户自己原有的一行' in rd(PROMPT), 'force-keeps-user-line',
    'exit=%d 改动已另存=%s 目标已还原=%s' % (c, bool(drift_hit), '用户自己原有的一行' in rd(PROMPT)))
c, o = run('-Target', 'pideck', '-Uninstall', '-Force')
chk(c == 0 and not os.path.exists(STATE), 'clean-uninstall', 'state 已清')

print()
print('==== TOTAL pass=%d fail=%d ====' % (P, F))
sys.exit(1 if F else 0)
