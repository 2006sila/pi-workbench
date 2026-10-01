# -*- coding: utf-8 -*-
"""DSH home 级 patch 层验收（沙箱，不碰真实配置）

覆盖 B 修复的缺陷：`Strip-PatchBlock` 的定位正则 `[^\\r\\n]*$` 恒不匹配
（.NET 多行模式下 `$` 匹配 `\\n` 之前的位置，而 `[^\\r\\n]*` 之后还隔着 `\\r`），
导致旧标记块**从来没被摘掉过** —— 每次部署都往里追加一层。

危害：cordis.patch.yml 里堆 N 个重复的 agent-instructions 层；
README 承诺的「重复注入幂等：标记块替换而非叠加」在这一层是失效的。

本用例覆盖：
  1. 连续部署 3 次，BEGIN/END 恒为 1（幂等）
  2. 用户自己写的其它 patch 项必须原样保留（部署不吞用户配置）
  3. 卸载后还原成用户原文（不残留本工具层）
  4. 历史累积块能被一次性清理干净（老用户升级过来也能自愈）

跑法： py -X utf8 tests\verify_patch.py
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

SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjpatch-verify')
HOME = os.path.join(SBX, 'home')
LOCAL = os.path.join(SBX, 'local')
AGENT = os.path.join(HOME, '.dsh')
PATCH = os.path.join(AGENT, 'cordis.patch.yml')

USER_PATCH = (
    '# 用户自己的注释\r\n'
    '- id: my-own-patch\r\n'
    '  config:\r\n'
    '    someKey: 123\r\n'
    '\r\n'
    '- id: another-user-thing\r\n'
    '  config:\r\n'
    '    enabled: true\r\n'
)

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


def rd(p):
    return open(p, encoding='utf-8').read() if os.path.exists(p) else ''


def counts():
    t = rd(PATCH)
    return t.count('# BEGIN pi-workbench'), t.count('# END pi-workbench')


shutil.rmtree(SBX, ignore_errors=True)
for d in (HOME, LOCAL, AGENT):
    os.makedirs(d, exist_ok=True)

print('=== 0 预置：用户自己先写一份 cordis.patch.yml ===')
open(PATCH, 'w', encoding='utf-8', newline='').write(USER_PATCH)
chk('my-own-patch' in rd(PATCH), 'user-patch-seeded', '')

print('=== 1 [B] 连续部署 3 次：标记块恒为 1 对（幂等）===')
seq = []
for i in range(3):
    c, o = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillMode', 'full')
    b, e = counts()
    seq.append((b, e))
    if c != 0:
        print('      第 %d 次部署 exit=%d' % (i + 1, c))
chk(all(b == 1 and e == 1 for b, e in seq), 'B-no-accumulation',
    '三次结果 %s（期望全为 (1,1)）' % seq)

print('=== 2 用户自己的 patch 项必须原样保留 ===')
t = rd(PATCH)
chk('my-own-patch' in t, 'user-item-1-kept', '')
chk('another-user-thing' in t, 'user-item-2-kept', '')
chk('someKey' in t and 'enabled' in t, 'user-config-kept', '')
chk('用户自己的注释' in t, 'user-comment-kept', '')
chk('agent-instructions' in t, 'tool-layer-written', '')

print('=== 3 历史累积块能被一次性清理干净（老用户升级自愈）===')
# 人为堆 3 个块，模拟老版本留下的现场
blk = '# BEGIN pi-workbench v4\r\n# x\r\n- id: agent-instructions\r\n  config:\r\n    maxBytes: 65536\r\n# END pi-workbench v4\r\n\r\n'
open(PATCH, 'w', encoding='utf-8', newline='').write(USER_PATCH + blk * 3)
b0, e0 = counts()
chk(b0 == 3, 'legacy-blocks-seeded', '预置 %d 个块' % b0)
c, o = run('-Target', 'dsh', '-SourcePrompt', SP, '-SkillMode', 'full')
b1, e1 = counts()
chk(b1 == 1 and e1 == 1, 'B-legacy-collapsed',
    '部署后 %d -> %d 对（期望收敛到 1）' % (b0, b1))
chk('my-own-patch' in rd(PATCH), 'user-item-survives-cleanup', '')

print('=== 4 卸载还原成用户原文 ===')
c, o = run('-Target', 'dsh', '-Uninstall', '-Force')
t = rd(PATCH)
chk('agent-instructions' not in t, 'uninstall-removes-tool-layer', '')
chk('my-own-patch' in t and 'another-user-thing' in t, 'uninstall-restores-user', '')
chk('# BEGIN pi-workbench' not in t, 'no-marker-left', '')

print()
print('==== TOTAL pass=%d fail=%d ====' % (P, F))
sys.exit(1 if F else 0)
