# -*- coding: utf-8 -*-
"""只读保护 + 客户端路径探测 验收（沙箱）。

只读保护（-Readonly，opt-in）：
  1. 部署后技能文件全部只读，state.readonly=true
  2. -Check 报「只读保护生效」；状态与实际属性不符时报「失效」
  3. 重新部署（带 -Readonly）能先解锁再覆盖再上锁（不被只读位挡住）
  4. 重新部署不带 -Readonly → 自动解除只读（开关关掉要真的松绑）
  5. 极简模式打标记（disable-model-invocation）在只读状态下也能写入 —— 本工具会先解锁
  6. 卸载：先解锁再删，清干净

-FindExe（四级探测：进程 → 注册表 → 快捷方式 → 写死路径）：输出必须是真实存在的路径。

跑法： py -X utf8 tests\verify_readonly.py
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SB = os.path.join(ROOT, 'inject.ps1')
SP = os.path.join(ROOT, 'prompts', '_v52c-header.md')
SK = os.path.join(ROOT, 'skills-v4')
os.environ['PJ_TEST_NO_KILL'] = '1'   # 硬闸：测试禁止杀进程
SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjro')
HOME = os.path.join(SBX, 'home')
LOCAL = os.path.join(SBX, 'local')
AGENT = os.path.join(HOME, '.pi', 'agent')
SKILLS = os.path.join(AGENT, 'skills')
STATE = os.path.join(LOCAL, 'pi-workbench', 'state', 'pideck.json')
PROMPT = os.path.join(AGENT, 'APPEND_SYSTEM.md')

P = F = 0


def chk(ok, tag, detail=''):
    global P, F
    P, F = (P + 1, F) if ok else (P, F + 1)
    print('  [%s] %-30s %s' % ('PASS' if ok else 'FAIL', tag, detail))


def env():
    e = dict(os.environ)
    e['USERPROFILE'] = HOME
    e['LOCALAPPDATA'] = LOCAL
    return e


def run(*args):
    r = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SB] +
                       [str(a) for a in args],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env(), check=False)
    raw = r.stdout
    return r.returncode, raw.decode('utf-8', 'replace') + raw.decode('gbk', 'replace')


def st():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def ro_stat():
    """返回 (只读文件数, 总文件数)，只看本工具装的那些技能目录。"""
    ro = tot = 0
    for nm in (st().get('installedSkills') or []):
        p = os.path.join(SKILLS, nm)
        if not os.path.isdir(p):
            continue
        for r, _d, fs in os.walk(p):
            for f in fs:
                tot += 1
                try:
                    if os.stat(os.path.join(r, f)).st_file_attributes & 0x1:   # FILE_ATTRIBUTE_READONLY
                        ro += 1
                except Exception:
                    pass
    return ro, tot


shutil.rmtree(SBX, ignore_errors=True)
for d in (HOME, LOCAL, AGENT):
    os.makedirs(d, exist_ok=True)

print('=== 1 带 -Readonly 部署：技能文件应全部只读 ===')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK, '-Readonly')
ro, tot = ro_stat()
chk(c == 0 and st().get('readonly') is True, 'readonly-flag-recorded', 'exit=%d state.readonly=%s' % (c, st().get('readonly')))
chk(tot > 100 and ro == tot, 'all-skills-readonly', '只读 %d / 共 %d 个文件' % (ro, tot))
chk('只读保护' in o, 'readonly-announced', '日志里有说明')

print('=== 2 -Check：应报「只读保护生效」 ===')
c, o = run('-Target', 'pideck', '-Check')
chk(c == 0 and '只读保护生效' in o, 'check-readonly-ok', 'exit=%d' % c)

print('=== 3 再次部署（带 -Readonly）：先解锁再覆盖再上锁，不应失败 ===')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK, '-Readonly')
ro2, tot2 = ro_stat()
chk(c == 0 and ro2 == tot2 and tot2 > 100, 'relock-works',
    'exit=%d 只读 %d/%d' % (c, ro2, tot2))
chk('解锁' in o or '只读保护' in o, 'unlock-announced', '有解锁/上锁提示')

print('=== 4 不带 -Readonly 再部署：只读应被解除（开关关掉要真的松绑）===')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK)
ro3, tot3 = ro_stat()
chk(c == 0 and ro3 == 0 and st().get('readonly') is False, 'readonly-cleared',
    'exit=%d 剩余只读 %d/%d state=%s' % (c, ro3, tot3, st().get('readonly')))

print('=== 5 极简模式 + 只读：打标记（disable-model-invocation）也要写得进去 ===')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', SK,
           '-SkillMode', 'menu', '-MenuKeepAdvertised', 'code-quality-gate', '-Readonly')
flagged = 0
for nm in (st().get('installedSkills') or []):
    p = os.path.join(SKILLS, nm, 'SKILL.md')
    if os.path.exists(p):
        txt = open(p, encoding='utf-8', errors='replace').read()
        if 'disable-model-invocation: true' in txt:
            flagged += 1
ro4, tot4 = ro_stat()
chk(c == 0 and flagged >= 60, 'menu-flag-under-readonly', 'exit=%d 已标记 %d 个' % (c, flagged))
chk(ro4 == tot4 and tot4 > 100, 'menu-then-readonly', '只读 %d/%d' % (ro4, tot4))

print('=== 6 卸载：先解锁再删，清干净 ===')
c, o = run('-Target', 'pideck', '-Uninstall', '-Force')
left = [x for x in os.listdir(SKILLS)] if os.path.isdir(SKILLS) else []
chk(c == 0 and not left and not os.path.exists(STATE), 'uninstall-cleans-readonly',
    'exit=%d 残留技能=%d state=%s' % (c, len(left), os.path.exists(STATE)))

print('=== 7 -FindExe：输出必须是真实存在的路径（找不到则退出码 1 + 告警）===')
c, o = run('-Target', 'pideck', '-FindExe')
line = [x.strip() for x in o.splitlines() if x.strip().lower().endswith('.exe')]
if c == 0:
    got = line[-1] if line else ''
    chk(bool(got) and os.path.exists(got), 'find-exe-real-path', '输出=%s' % got)
else:
    chk('未找到' in o, 'find-exe-warns-when-missing', 'exit=%d 有告警' % c)

print()
print('==== TOTAL pass=%d fail=%d ====' % (P, F))
sys.exit(1 if F else 0)