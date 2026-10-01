# -*- coding: utf-8 -*-
"""扒 alice 四项机制的回归验收（沙箱，不碰真实配置）

覆盖：
  #4  探针匹配「按行精确」：技能名互含时不能误判归属；
      且「隐藏模块泄漏」必须优先于「命中可见技能」判定（否则被掩盖成 pass）
  #3  子进程输出分级：噪声黑名单丢弃 / 关键行白名单优先 / 关键行截断
  #1  重名技能来源选择：默认取先出现 / -SkillSourceMap 改选 / 指定不存在来源时停止
  #2  进度「清单先行」：构造即画全行 / 事件按 key 点亮 / 未知 key 追加 / 未上报标跳过

跑法： py -X utf8 tests\verify_borrowed.py
"""
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys

SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjborrow')
os.environ['PJ_TEST_NO_KILL'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PJ_SKIP_AGREEMENT'] = '1'
os.environ['USERPROFILE'] = os.path.join(SBX, 'home')
os.environ['LOCALAPPDATA'] = os.path.join(SBX, 'LA')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SB = os.path.join(ROOT, 'inject.ps1')
SP = os.path.join(ROOT, 'prompts', '_v52c-header.md')
AGENT = os.path.join(SBX, 'home', '.dsh')
SKILLS = os.path.join(AGENT, 'skills')

shutil.rmtree(SBX, ignore_errors=True)
os.makedirs(os.path.join(SBX, 'home'), exist_ok=True)
os.makedirs(os.path.join(SBX, 'LA'), exist_ok=True)

P = F = 0


def chk(ok, tag, detail=''):
    global P, F
    P, F = (P + 1, F) if ok else (P, F + 1)
    print('  [%s] %-42s %s' % ('PASS' if ok else 'FAIL', tag, detail))


def env():
    e = dict(os.environ)
    e['USERPROFILE'] = os.path.join(SBX, 'home')
    e['LOCALAPPDATA'] = os.path.join(SBX, 'LA')
    e['DSH_HOME'] = AGENT
    return e


def run(*args):
    r = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SB] + [str(a) for a in args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env(), cwd=ROOT, check=False)
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


# ---------------------------------------------------------------- 加载 GUI 模块
spec = importlib.util.spec_from_file_location('bj_tool', os.path.join(ROOT, 'bj_tool.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from PySide6.QtWidgets import QApplication          # noqa: E402
app = QApplication([])
m._apply_ui_font(app)
m._apply_ui_font_px(app, 13)

# ================================================================ #3 输出分级
print('=== [#3] 子进程输出分级（噪声/关键行/截断）===')
r = m.Runner.__new__(m.Runner)
r._dropped = 0
r._important = 0
cases = [
    ('Reading additional input from stdin', 'noise'),
    ('npm notice New minor version', 'noise'),
    ('   42   ', 'noise'),
    ('--------', 'noise'),
    ('Progress: resolved 1200, reused 900', 'noise'),
    ('[INFO] 已部署技能 65 个', 'keep'),
    ('[ERROR] 目标文件被外部改动', 'keep'),
    ('workdir: C:/x  ERROR happened', 'keep'),   # 白名单优先于黑名单
    ('a normal line', 'keep'),
]
_bad = []
for text, want in cases:
    kind, _out = r.classify(text)
    if kind != want:
        _bad.append('%r want=%s got=%s' % (text[:28], want, kind))
chk(not _bad, 'classify-noise-and-keep', '; '.join(_bad) if _bad else '%d 例全对' % len(cases))
_long = 'x' * 300
_kind, _out = r.classify('ERROR ' + _long)
chk(len(_out) <= m.Runner.IMPORTANT_MAX + 1, 'important-line-truncated',
    'len=%d (上限 %d)' % (len(_out), m.Runner.IMPORTANT_MAX))

# ================================================================ #2 进度窗
print('=== [#2] 进度窗：清单先行 / 按 key 点亮 / 未上报标跳过 ===')
plan = [('patch', 'patch'), ('prompt', 'prompt'), ('skills', 'skills'), ('state', 'state')]
dlg = m.ProgressDialog(plan, 'test')
chk(len(dlg._rows) == 4, 'plan-rows-drawn-upfront', '行数=%d' % len(dlg._rows))
chk(all(dlg._rows[k][2].text() == '待办' for k in dlg._rows), 'initial-all-pending', '')
chk(dlg.feed('[STEP] prompt|ok|已写入'), 'step-event-consumed', '')
chk(dlg._rows['prompt'][2].text() == '已写入', 'step-lights-row', dlg._rows['prompt'][2].text())
chk(not dlg.feed('[INFO] 普通日志行'), 'normal-line-passthrough', '')
dlg.feed('[PLAN] extra|后端新增步骤')
chk('extra' in dlg._rows, 'unknown-plan-key-appended', '行数=%d' % len(dlg._rows))
dlg.feed('[STEP] skills|fail|目录被占用')
chk(dlg._rows['skills'][0].text() == '✗', 'fail-marked', dlg._rows['skills'][0].text())
dlg.finish_all(0)
_left = [k for k in dlg._order if dlg._rows[k][2].text() == '待办']
chk(not _left, 'no-row-left-pending', '仍待办: %s' % (_left or '无'))
chk(dlg._rows['state'][2].text() == '跳过', 'unreported-marked-skip', '')

# key 契约：PS 发起的 key 必须都被 GUI 的 plan 覆盖
_ps = io.open(SB, encoding='utf-8-sig').read()
_ps_keys = set(re.findall(r"Emit-PlanItem\s+'([a-z]+)'", _ps))
_w = m.MainWindow(skip_auto=True)
_gui_dsh = set(k for k, _ in _w._plan_for('install', {'key': 'dsh', 'card': 'DSH'}, True))
_gui_pi = set(k for k, _ in _w._plan_for('install', {'key': 'pideck', 'card': 'PiDeck'}, True))
chk(_ps_keys <= _gui_dsh, 'key-contract-ps-subset-of-gui',
    'ps=%s gui=%s' % (sorted(_ps_keys), sorted(_gui_dsh)))
chk('patch' not in _gui_pi, 'pideck-has-no-patch-row', '')
_w.close()

# ================================================================ #4 探针口径
print('=== [#4] 探针匹配口径（按行精确 + 隐藏泄漏优先）===')
# 直接核源码：必须用按行 token 比对，不能再用 -match 子串
chk('$replyTokens' in _ps, 'probe-uses-line-tokens', '按行取词已落地')
chk('-contains' in _ps, 'probe-uses-exact-contains', '')
# 优先级：hid 判断必须在 hit 之前
_i_hid = _ps.find('if ($hid.Count -gt 0)')
_i_hit = _ps.find('} elseif ($hit.Count -gt 0) {')
chk(0 < _i_hid < _i_hit, 'hidden-check-before-hit',
    'hid@%d hit@%d' % (_i_hid, _i_hit))
# 技能名互含的事实仍存在（说明这个修正是必要的）
_names = [d for d in os.listdir(os.path.join(ROOT, 'skills-v4'))
          if os.path.isdir(os.path.join(ROOT, 'skills-v4', d))]
_pairs = [(a, b) for a in _names for b in _names if a != b and a in b]
chk(len(_pairs) > 0, 'substring-name-pairs-exist',
    '%d 对（如 %s ⊂ %s）' % (len(_pairs), _pairs[0][0], _pairs[0][1]) if _pairs else '无')

# ================================================================ #1 重名来源
print('=== [#1] 重名技能来源选择 ===')
_pa = os.path.join(SBX, 'packA')
_pb = os.path.join(SBX, 'packB')
for _p, _tag in ((_pa, 'AAA'), (_pb, 'BBB')):
    _d = os.path.join(_p, 'dup-skill')
    os.makedirs(_d, exist_ok=True)
    with io.open(os.path.join(_d, 'SKILL.md'), 'w', encoding='utf-8', newline='') as f:
        f.write('---\nname: dup-skill\ndescription: duplicated skill variant %s for regression testing\n---\n\nMARKER-%s\n' % (_tag, _tag))

_c, _o = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillMode', 'full')
chk(_c == 0, 'baseline-deploy-for-skillsonly', 'exit=%d（SkillsOnly 的前置）' % _c)

_tgt = os.path.join(SKILLS, 'dup-skill', 'SKILL.md')


def _installed():
    if not os.path.exists(_tgt):
        return 'MISSING'
    t = io.open(_tgt, encoding='utf-8').read()
    mm = re.search(r'MARKER-(\w+)', t)
    return mm.group(1) if mm else '?'


_c, _o = run('-Target', 'dsh', '-SkillsOnly', '-SkillsSource', '%s;%s' % (_pa, _pb))
chk(_installed() == 'AAA', 'dup-default-takes-first', '装入=%s（期望 AAA）' % _installed())
chk('同名技能冲突' in _o and 'SkillSourceMap' in _o, 'dup-warns-with-howto',
    '报了冲突并给出改法')

_c, _o = run('-Target', 'dsh', '-SkillsOnly', '-SkillsSource', '%s;%s' % (_pa, _pb),
             '-SkillSourceMap', 'dup-skill=packB')
chk(_installed() == 'BBB', 'dup-explicit-pick-wins', '装入=%s（期望 BBB）' % _installed())

_c, _o = run('-Target', 'dsh', '-SkillsOnly', '-SkillsSource', '%s;%s' % (_pa, _pb),
             '-SkillSourceMap', 'dup-skill=nonexistent')
chk(_c != 0 and '不存在的来源' in _o, 'dup-bad-source-stops',
    'exit=%d' % _c)
chk(_installed() == 'BBB', 'dup-bad-source-writes-nothing', '装入仍为 %s' % _installed())

# ---------------------------------------------------------------- Copy-Tree
print('=== [Copy-Tree] robocopy 必须真正覆盖「同大小+同时间戳」的文件 ===')
# 背景（实测复现过的真 bug）：robocopy 默认规则是「同大小 + 同 mtime = 已相同」，
# 不看内容。技能包里的 SKILL.md 常是同一秒生成/解压出来的，于是
# 「换个来源重装同名技能」会静默保留旧内容。
# 不走 dot-source 验证（inject.ps1 有 Mandatory 参数，source 会直接执行主体），
# 改为**端到端**：用 -SkillsSource 装一次，再换来源装一次，
# 两次的 SKILL.md 保证「同大小 + 同 mtime」，看第二次有没有真覆盖。
import time as _t
_CT_STAMP = _t.time() - 200


def _mk_pack(root, tag):
    d = os.path.join(root, 'ctskill')
    os.makedirs(d, exist_ok=True)
    body = '---\nname: ctskill\ndescription: copy-tree probe %s with identical byte length\n---\n\nMARKER-%s\n' % (tag, tag)
    p = os.path.join(d, 'SKILL.md')
    with io.open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(body)
    os.utime(p, (_CT_STAMP, _CT_STAMP))     # 钉同一秒，逼出 robocopy 的「已相同」判定
    return root, os.path.getsize(p)


_cta, _sa = _mk_pack(os.path.join(SBX, 'ctA'), 'AAA')
_ctb, _sb = _mk_pack(os.path.join(SBX, 'ctB'), 'BBB')
chk(_sa == _sb, 'ct-fixture-same-size', '两个源同为 %d 字节' % _sa)

_ct_tgt = os.path.join(SKILLS, 'ctskill', 'SKILL.md')


def _ct_get():
    if not os.path.exists(_ct_tgt):
        return 'MISSING'
    t = io.open(_ct_tgt, encoding='utf-8').read()
    mm = re.search(r'MARKER-(\w+)', t)
    return mm.group(1) if mm else '?'


_c, _o = run('-Target', 'dsh', '-SkillsOnly', '-SkillsSource', _cta)
chk(_ct_get() == 'AAA', 'ct-first-install', '装入=%s' % _ct_get())
if os.path.exists(_ct_tgt):     # 把目标也钉到同一秒，确保测的是「同大小同时间」
    os.utime(_ct_tgt, (_CT_STAMP, _CT_STAMP))
_c, _o = run('-Target', 'dsh', '-SkillsOnly', '-SkillsSource', _ctb, '-SkillSourceMap', 'ctskill=ctB')
chk(_ct_get() == 'BBB', 'copytree-overwrites-same-size-same-mtime',
    '装入=%s（期望 BBB；若为 AAA 说明 robocopy 判定「已相同」而跳过）' % _ct_get())

print()
print('==== TOTAL pass=%d fail=%d ====' % (P, F))
sys.exit(1 if F else 0)
