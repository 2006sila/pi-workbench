# -*- coding: utf-8 -*-
"""技能菜单生成器验收（沙箱，不碰真实配置）

覆盖三件事（都是「技能库要长大」时才会暴露的问题）：

  1. 评分列：菜单/分片表里每个模块带 x/10（来自 skill-ratings.json）；
     评分文件缺失时照样生成，只是这一列写 "-" —— 不许把生成整条卡住。
  2. 分片：类目内模块数超过阈值 → 该类拆到 sections/<NN>-<模块id>.md，
     菜单里只留一行指针。技能库涨到几百个模块时，每轮常驻的菜单不该跟着涨。
     附带测「陈旧分片清理」：类目缩小回内联后，旧分片文件必须被删掉，
     否则它会一直指着已删的模块。
  3. -Check 覆盖分片：分片内容被改动要能被报出来（原来的比对只看菜单正文）。

跑法： py -X utf8 tests\verify_menu.py
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
os.environ['PJ_TEST_NO_KILL'] = '1'

SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjmenu-verify')
HOME = os.path.join(SBX, 'home')
LOCAL = os.path.join(SBX, 'local')
AGENT = os.path.join(HOME, '.dsh')
SKILLS = os.path.join(AGENT, 'skills')
MENU = 'pi-workbench-menu'
MENU_DIR = os.path.join(SKILLS, MENU)
SEC_DIR = os.path.join(MENU_DIR, 'sections')
STATE = os.path.join(LOCAL, 'pi-workbench', 'state', 'dsh.json')

PTR = re.compile(r'^> .*超过分片阈值', re.M)      # 分片指针行（菜单里替代整张表的那一行）
P = F = 0


def module_rows(t):
    # 只数模块行：以 | ` 开头的行（表头与分隔行不算）
    return len([ln for ln in t.splitlines() if ln.startswith('| ' + chr(96))])


def chk(ok, tag, detail=''):
    global P, F
    P, F = (P + 1, F) if ok else (P, F + 1)
    print('  [%s] %-38s %s' % ('PASS' if ok else 'FAIL', tag, detail))


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


def txt(p):
    return open(p, encoding='utf-8').read() if os.path.exists(p) else ''


def menu_text():
    return txt(os.path.join(MENU_DIR, 'SKILL.md'))


def section_files():
    if not os.path.isdir(SEC_DIR):
        return []
    return sorted(os.listdir(SEC_DIR))


def make_skill(root, cat, name, desc='d' * 90, body_lines=40):
    d = os.path.join(root, cat, name)
    os.makedirs(d, exist_ok=True)
    body = chr(10).join('line %d' % i for i in range(body_lines))
    with open(os.path.join(d, 'SKILL.md'), 'w', encoding='utf-8', newline=chr(10)) as fh:
        fh.write('---' + chr(10) + 'name: %s' % name + chr(10) + 'description: %s' % desc + chr(10)
                 + '---' + chr(10) + chr(10) + '# %s' % name + chr(10) + chr(10) + body + chr(10))


def build_lib(count_a, count_b, count_c, lib):
    shutil.rmtree(lib, ignore_errors=True)
    for i in range(count_a):
        make_skill(lib, 'cat-a', 'alpha-%02d' % i)
    for i in range(count_b):
        make_skill(lib, 'cat-b', 'beta-%02d' % i)
    for i in range(count_c):
        make_skill(lib, 'cat-c', 'gamma-%02d' % i)


def write_meta(lib):
    cats = os.path.join(lib, 'cats.json')
    with open(cats, 'w', encoding='utf-8', newline=chr(10)) as fh:
        json.dump({'schema': 1, 'categories': [
            {'name': 'A 类', 'when': '任务属于 A', 'modules': ['alpha-%02d' % i for i in range(40)]},
            {'name': 'B 类', 'when': '任务属于 B', 'modules': ['beta-%02d' % i for i in range(40)]},
            {'name': 'C 类', 'when': '任务属于 C', 'modules': ['gamma-%02d' % i for i in range(40)]},
        ]}, fh, ensure_ascii=False, indent=2)
    rat = os.path.join(lib, 'ratings.json')
    ratings = {}
    for i in range(40):
        ratings['alpha-%02d' % i] = 5 + (i % 5)
        ratings['beta-%02d' % i] = 4 + (i % 3)
        ratings['gamma-%02d' % i] = 9
    with open(rat, 'w', encoding='utf-8', newline=chr(10)) as fh:
        json.dump({'schema': 1, 'shardThreshold': 12, 'ratings': ratings}, fh, ensure_ascii=False, indent=2)
    return cats, rat


shutil.rmtree(SBX, ignore_errors=True)
for d in (HOME, LOCAL, AGENT):
    os.makedirs(d, exist_ok=True)

print('== 1. 真实技能库 · 极简模式（65 模块，阈值 12）==')
code, out = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillMode', 'menu')
chk(code == 0, '菜单模式部署成功', 'exit=%d' % code)
mt = menu_text()
chk(bool(mt), '菜单技能已生成', '%d 字符' % len(mt))
chk('| 模块 | 评分 | 何时用 |' in mt, '菜单表带评分列')
chk(re.search(r'\| [^\n|]+ \| \d+/10 \|', mt) is not None, '评分渲染成 x/10')
secs = section_files()
chk(len(secs) >= 1, '大类目已拆片', 'sections=%s' % secs)
if secs:
    s0 = txt(os.path.join(SEC_DIR, secs[0]))
    chk('| 模块 | 评分 | 何时用 |' in s0, '分片表带评分列')
    sharded_ids = re.findall(r'^\| ' + chr(96) + r'([a-z0-9-]+)' + chr(96), s0, re.M)
    chk(len(sharded_ids) >= 13, '分片列出模块 id', '%d 个' % len(sharded_ids))
    # 一个模块可能同时挂在两个类目里：只对「只属于这一类」的模块要求它不在菜单正文
    from collections import Counter as _C
    _cats = json.load(open(os.path.join(ROOT, 'skill-categories.json'), encoding='utf-8'))['categories']
    _occ = _C(m for c in _cats for m in (c.get('modules') or []))
    _only_here = set(i for i in sharded_ids if _occ.get(i) == 1)
    # 用「反引号包住的模块 id」判定：纯子串会被分片文件名（sections/01-radare2.md）和
    # 同前缀模块 id（reverse-engineering-api）误伤
    leaked = [i for i in _only_here if (chr(96) + i + chr(96)) in mt]
    chk(not leaked, '仅属该类的分片模块不进菜单正文', str(leaked[:3]))

print()
print('== 2. 规模上限：3 类 x 20 模块（阈值 12）==')
lib = os.path.join(SBX, 'lib')
build_lib(20, 20, 20, lib)
cats, rat = write_meta(lib)
shutil.rmtree(AGENT, ignore_errors=True)
os.makedirs(AGENT, exist_ok=True)
code, out = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillMode', 'menu',
                '-SkillsSource', lib, '-CatFile', cats, '-RatingFile', rat)
chk(code == 0, '合成库部署成功', 'exit=%d' % code)
mt2 = menu_text()
secs2 = section_files()
chk(len(secs2) == 3, '3 个类目全部拆片', 'sections=%s' % secs2)
chk(len(mt2) < 3000, '菜单保持小体积', '%d 字符（<3000）' % len(mt2))
chk(len(PTR.findall(mt2)) == 3, '菜单里 3 行分片指针', 'count=%d' % len(PTR.findall(mt2)))
chk('alpha-01' not in mt2, '被拆类目的模块不进菜单')
if secs2:
    big = txt(os.path.join(SEC_DIR, secs2[0]))
    chk(module_rows(big) == 20, '分片列出该类 20 个模块', 'rows=%d' % module_rows(big))
    chk(re.search(r'\| ' + chr(96) + r'alpha-00' + chr(96) + r' \| 5/10 \|', big) is not None, '评分进了分片表格')

print()
print('== 3. -Check 覆盖分片内容 ==')
code, out = run('-Target', 'dsh', '-Check', '-CatFile', cats, '-RatingFile', rat)
chk('生成物一致' in out, '-Check 报生成物一致')
if secs2:
    victim = os.path.join(SEC_DIR, secs2[0])
    with open(victim, 'a', encoding='utf-8', newline=chr(10)) as fh:
        fh.write('<!-- 外部改动 -->' + chr(10))
    code, out = run('-Target', 'dsh', '-Check', '-CatFile', cats, '-RatingFile', rat)
    hit = re.search(r'菜单分片不一致[^\r\n]*', out)
    chk('菜单分片不一致' in out, '分片被改动能被报出', hit.group(0)[:44] if hit else '')

print()
print('== 4. 陈旧分片清理（A 类缩到 3 个模块）==')
build_lib(3, 20, 20, lib)
cats, rat = write_meta(lib)
code, out = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillMode', 'menu',
                '-SkillsSource', lib, '-CatFile', cats, '-RatingFile', rat)
chk(code == 0, '缩小后重新部署成功', 'exit=%d' % code)
secs3 = section_files()
chk(len(secs3) == 2, '分片数跟着降到 2', 'sections=%s' % secs3)
chk(not any(s.startswith('01-') for s in secs3), 'A 类旧分片已删除')
mt3 = menu_text()
chk('alpha-00' in mt3, 'A 类改回内联表（模块回到菜单）')
chk(len(PTR.findall(mt3)) == 2, '菜单只剩 2 行分片指针', 'count=%d' % len(PTR.findall(mt3)))

print()
print('== 5. 评分文件缺失时降级（不阻断生成）==')
code, out = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillMode', 'menu',
                '-SkillsSource', lib, '-CatFile', cats, '-RatingFile', os.path.join(SBX, 'nope.json'))
chk(code == 0, '无评分表也能生成菜单', 'exit=%d' % code)
mt4 = menu_text()
chk('| 模块 | 评分 | 何时用 |' in mt4, '表头仍在')
chk('| - |' in mt4, '评分列写 -（不编数）')

print()
print('结果：%s（%d/%d 通过）' % ('全过' if F == 0 else '有失败', P, P + F))
sys.exit(1 if F else 0)
