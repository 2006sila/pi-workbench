# -*- coding: utf-8 -*-
"""附加技能包生命周期验收（沙箱，不碰真实配置）

覆盖 A+C 修复的缺陷（原先是「记了字段但没有任何消费方」的静默不一致）：

  A. `-RemoveAddons` 移除附加包后，状态清单里的 `menuKeepAdvertised`
     必须同步剔除该包 —— 否则它会一直指着磁盘上已不存在的技能，
     下次部署极简模式时对该路径调 Remove-DisableModelInvocation 会静默失败，
     「保持常驻」这个承诺永远无法兑现。
  A2. 移除后菜单技能正文要跟着重生成，不能还列着已删的模块
     （agent 照菜单去 read 会读不到）。
  C. `-Check` 必须能把「menuKeepAdvertised 指向不存在的技能」报出来
     —— 这才是让这个字段进入可校验范围的那道防线。

跑法： py -X utf8 tests\verify_addons.py
"""
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
os.environ['PJ_TEST_NO_KILL'] = '1'   # 硬闸：测试禁止杀进程

SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjaddons-verify')
HOME = os.path.join(SBX, 'home')
LOCAL = os.path.join(SBX, 'local')
AGENT = os.path.join(HOME, '.dsh')
SKILLS = os.path.join(AGENT, 'skills')
STATE = os.path.join(LOCAL, 'pi-workbench', 'state', 'dsh.json')
MENU = 'pi-workbench-menu'

P = F = 0


def chk(ok, tag, detail=''):
    global P, F
    P, F = (P + 1, F) if ok else (P, F + 1)
    print('  [%s] %-34s %s' % ('PASS' if ok else 'FAIL', tag, detail))


def env():
    e = dict(os.environ)
    e['USERPROFILE'] = HOME
    e['LOCALAPPDATA'] = LOCAL
    e['DSH_HOME'] = AGENT
    return e


def run(*args):
    r = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SB] + [str(a) for a in args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env(), check=False)
    # PowerShell 重定向到管道时中文走 OEM 码页：两种都解一次，谁解出中文用谁
    raw = r.stdout
    out = raw.decode('utf-8', 'replace')
    if not re.search(r'[\u4e00-\u9fff]', out):
        try:
            alt = raw.decode('gbk', 'replace')
            if re.search(r'[\u4e00-\u9fff]', alt):
                out = alt
        except Exception:
            pass
    return r.returncode, out


def st():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def menu_text():
    p = os.path.join(SKILLS, MENU, 'SKILL.md')
    return open(p, encoding='utf-8').read() if os.path.exists(p) else ''


shutil.rmtree(SBX, ignore_errors=True)
for d in (HOME, LOCAL, AGENT):
    os.makedirs(d, exist_ok=True)

print('=== 1 极简模式部署：两个附加包常驻 + 菜单就位 ===')
c, o = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillsSource', SK,
           '-SkillMode', 'menu', '-MenuKeepAdvertised', 'code-quality-gate;task-boundary')
s = st()
chk(c == 0 and s.get('skillMode') == 'menu', 'deploy-menu-mode', 'exit=%d mode=%s' % (c, s.get('skillMode')))
chk(s.get('menuKeepAdvertised') == 'code-quality-gate;task-boundary', 'keep-advertised-recorded',
    'keep=%s' % s.get('menuKeepAdvertised'))
chk(MENU in (s.get('installedSkills') or []), 'menu-in-installed-skills', '')
chk(os.path.exists(os.path.join(SKILLS, MENU, 'SKILL.md')), 'menu-skill-on-disk', '')

print('=== 2 -Check 基线：健康状态下必须通过 ===')
c0, o0 = run('-Target', 'dsh', '-Check')
chk(c0 == 0, 'check-healthy-passes', 'exit=%d' % c0)

print('=== 3 移除附加包 code-quality-gate ===')
c, o = run('-Target', 'dsh', '-RemoveAddons', 'code-quality-gate')
s = st()
chk(c == 0 and 'code-quality-gate' not in (s.get('installedSkills') or []), 'removed-from-installed',
    'exit=%d' % c)
chk(not os.path.exists(os.path.join(SKILLS, 'code-quality-gate')), 'removed-from-disk', '')

print('=== 4 [A] menuKeepAdvertised 必须同步剔除被移除的包 ===')
keep = s.get('menuKeepAdvertised') or ''
chk('code-quality-gate' not in keep, 'A-keep-advertised-pruned',
    'keep=%r（不应再含 code-quality-gate）' % keep)
chk('task-boundary' in keep, 'A-other-entry-kept', '仍保留未移除的 task-boundary')

print('=== 5 [A2] 菜单技能正文必须重生成，不再列已删模块 ===')
mt = menu_text()
chk(mt != '', 'menu-still-exists', '')
chk('code-quality-gate' not in mt, 'A2-menu-drops-removed', '')
chk('task-boundary' in mt, 'A2-menu-keeps-remaining', '')

print('=== 6 [C] 人为制造坏状态：-Check 必须报出来 ===')
# 写状态清单前先去只读（部署会把它锁上）
try:
    import stat as _stat
    os.chmod(STATE, _stat.S_IWRITE | _stat.S_IREAD)
except Exception:
    pass
raw = open(STATE, encoding='utf-8').read()
bad = re.sub(r'("menuKeepAdvertised":\s*)"[^"]*"', r'\1"code-quality-gate;zzz-not-exist"', raw)
open(STATE, 'w', encoding='utf-8', newline='').write(bad)
chk('zzz-not-exist' in open(STATE, encoding='utf-8').read(), 'bad-state-injected', '')
c1, o1 = run('-Target', 'dsh', '-Check')
chk(c1 == 1, 'C-check-fails-on-bad-state', 'exit=%d（期望 1）' % c1)
chk('menuKeepAdvertised' in o1, 'C-check-names-the-field', '报出了该字段名')
chk('zzz-not-exist' in o1, 'C-check-names-the-entry', '报出了具体条目')

print('=== 7 卸载收尾干净 ===')
c, o = run('-Target', 'dsh', '-Uninstall', '-Force')
chk(c == 0 and not os.path.exists(STATE), 'clean-uninstall', 'exit=%d state已清=%s' % (c, not os.path.exists(STATE)))

print()
print('==== TOTAL pass=%d fail=%d ====' % (P, F))
sys.exit(1 if F else 0)
