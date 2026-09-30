# -*- coding: utf-8 -*-
"""技能来源识别验收（沙箱）：集合 / 一层包装 / zip / 同名冲突 / 深度上限。

对应 alice-assistant 的 import_skill.rs 识别规则（clean-room 学的是规则本身）：
  · 直接子目录带 SKILL.md      → 逐个收
  · 没有但更深处有             → 「技能集合」或 zip 一层包装，自动下钻
  · 找到技能后不再下钻
  · 深度 / 数量封顶，截断要如实报
  · 跨来源同名 → 报出来（不再静默取第一个）

跑法： py -X utf8 tests\verify_import.py
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SB = os.path.join(ROOT, 'inject.ps1')
SP = os.path.join(ROOT, 'prompts', '_v52c-header.md')
os.environ['PJ_TEST_NO_KILL'] = '1'   # 硬闸：测试禁止杀进程
SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjimport')
HOME = os.path.join(SBX, 'home')
LOCAL = os.path.join(SBX, 'local')
AGENT = os.path.join(HOME, '.pi', 'agent')
SKILLS = os.path.join(AGENT, 'skills')
STATE = os.path.join(LOCAL, 'pi-workbench', 'state', 'pideck.json')

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


def mk(path, name, body='正文'):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, 'SKILL.md'), 'w', encoding='utf-8', newline='') as fh:
        fh.write('---\nname: %s\ndescription: 导入识别测试用技能（%s）。\n---\n%s\n' % (name, name, body))


def installed():
    try:
        st = json.load(open(STATE, encoding='utf-8'))
        return sorted(st.get('installedSkills') or [])
    except Exception:
        return []


shutil.rmtree(SBX, ignore_errors=True)
for d in (HOME, LOCAL, AGENT):
    os.makedirs(d, exist_ok=True)

# 造来源
SRC = os.path.join(SBX, 'src')
mk(os.path.join(SRC, 'collection', 'pack-a', 'skill-one'), 'skill-one')
mk(os.path.join(SRC, 'collection', 'pack-a', 'skill-two'), 'skill-two')
mk(os.path.join(SRC, 'wrapper', 'wrap', 'skill-three'), 'skill-three')
mk(os.path.join(SRC, 'flat', 'skill-four'), 'skill-four')
mk(os.path.join(SRC, 'dup-a', 'skill-five'), 'skill-five', '来自 A')
mk(os.path.join(SRC, 'dup-b', 'skill-five'), 'skill-five', '来自 B')
mk(os.path.join(SRC, 'deep', 'l1', 'l2', 'l3', 'l4', 'skill-deep'), 'skill-deep')
ZIP = os.path.join(SBX, 'pack.zip')
with zipfile.ZipFile(ZIP, 'w') as z:
    z.writestr('ziproot/skill-six/SKILL.md',
               '---\nname: skill-six\ndescription: 导入识别测试用技能（skill-six）。\n---\n正文\n')

print('=== 1 常规：直接子目录带 SKILL.md ===')
c, o = run('-Target', 'pideck', '-SourcePrompt', SP, '-SkillsSource', os.path.join(SRC, 'flat'))
chk(c == 0 and installed() == ['skill-four'], 'flat-source', 'exit=%d installed=%s' % (c, installed()))

print('=== 2 技能集合：一包多技能，自动逐个收 ===')
c, o = run('-Target', 'pideck', '-SkillsOnly', '-SkillsSource', os.path.join(SRC, 'collection', 'pack-a'))
got = installed()
# 注：-SkillsOnly 是**合并**语义（保留上次已装的），所以断言看增量
chk(c == 0 and {'skill-one', 'skill-two'} <= set(got), 'collection',
    'exit=%d installed=%s' % (c, got))

print('=== 3 一层包装：自动下钻 ===')
c, o = run('-Target', 'pideck', '-SkillsOnly', '-SkillsSource', os.path.join(SRC, 'wrapper'))
got = installed()
chk(c == 0 and 'skill-three' in got, 'wrapper-descend', 'installed=%s' % got)

print('=== 4 zip 来源：解压后按同样规则识别 ===')
c, o = run('-Target', 'pideck', '-SkillsOnly', '-SkillsSource', ZIP)
got = installed()
chk(c == 0 and 'skill-six' in got, 'zip-source', 'installed=%s' % got)
chk('技能包已解压' in o, 'zip-announced', '日志里说明了解压到临时目录')

print('=== 5 跨来源同名冲突：报出来，取先出现的那个 ===')
c, o = run('-Target', 'pideck', '-SkillsOnly',
           '-SkillsSource', (os.path.join(SRC, 'dup-a') + ';' + os.path.join(SRC, 'dup-b')))
body = open(os.path.join(SKILLS, 'skill-five', 'SKILL.md'), encoding='utf-8').read()
chk(c == 0 and '同名技能冲突' in o, 'dup-reported', 'WARN=%s' % ('同名技能冲突' in o))
chk('来自 A' in body, 'dup-first-wins', '取先出现的来源（内容=%s）' % ('来自 A' if '来自 A' in body else '来自 B'))

print('=== 6 深度上限：超过 3 层不认，且只告警不是失败 ===')
c, o = run('-Target', 'pideck', '-SkillsOnly', '-SkillsSource', os.path.join(SRC, 'deep'))
got = installed()
chk(c == 0 and 'skill-deep' not in got, 'depth-cap',
    'exit=%d 未收进来=%s（只告警不失败）' % (c, 'skill-deep' not in got))
chk('没找到 SKILL.md' in o or '已下钻' in o, 'depth-cap-warned', '有明确告警')

print('=== 7 不存在的来源：告警但不崩、不失败 ===')
c, o = run('-Target', 'pideck', '-SkillsOnly', '-SkillsSource', os.path.join(SBX, 'nope'))
chk(c == 0 and ('技能来源不存在' in o or '技能源目录为空' in o), 'missing-source-warns',
    'exit=%d 有告警=%s' % (c, '技能来源不存在' in o or '技能源目录为空' in o))

print()
print('==== TOTAL pass=%d fail=%d ====' % (P, F))
sys.exit(1 if F else 0)