# -*- coding: utf-8 -*-
"""界面回归（offscreen，沙箱）：页面 / 主题 / 模式 / 附加包 / 卸载 / 版本窗 / 任务构建 / 只读开关。

这是唯一的界面级验收：所有操作都在离屏 Qt 里跑，不弹真窗口、不碰真实配置。
跑法： py -X utf8 tests\verify_ui.py
"""
import importlib.util
import json
import os
import shutil
import sys

SBX = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'pjui')
os.environ['PJ_TEST_NO_KILL'] = '1'      # 硬闸：测试里禁止杀任何真实进程
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PJ_SKIP_AGREEMENT'] = '1'
os.environ['USERPROFILE'] = os.path.join(SBX, 'home')
os.environ['LOCALAPPDATA'] = os.path.join(SBX, 'LA')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
shutil.rmtree(SBX, ignore_errors=True)
os.makedirs(os.path.join(SBX, 'home'))
os.makedirs(os.path.join(SBX, 'LA'))

spec = importlib.util.spec_from_file_location('bj_tool', os.path.join(ROOT, 'bj_tool.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

from PySide6.QtWidgets import QApplication                    # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer                # noqa: E402

app = QApplication([])
m._apply_ui_font(app)
m._apply_ui_font_px(app, 13)
m._apply_global_qss(app)
m.set_theme('light')
w = m.MainWindow(skip_auto=True)
w.resize(1180, 720)
w.show()
app.processEvents()

ok = True


def chk(label, cond, extra=''):
    global ok
    ok = ok and bool(cond)
    print('%s %-46s %s' % ('✓' if cond else '✗', label, extra))


def run_task(timeout=180000):
    loop = QEventLoop()

    def poll():
        if w._busy:
            QTimer.singleShot(200, poll)
        else:
            QTimer.singleShot(300, loop.quit)
    QTimer.singleShot(200, poll)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()


sk = m.resolve_agent_dir([t for t in m.TARGETS if t['key'] == 'pideck'][0])
sk = os.path.join(sk, 'skills')
tp = w.page_tpl

print('=== 1 页面与导航 ===')
for i in range(6):
    w.sidebar.select(i)
    app.processEvents()
    if w.stack.currentIndex() != i:
        chk('页面 %d 切换' % i, False)
        break
else:
    chk('六页切换 + sidebar 同步', True)

m.set_theme('dark')
for i in range(6):
    w.sidebar.select(i)
    app.processEvents()
chk('切主题后页面/导航同步', w.stack.currentIndex() == 5)

print('=== 2 模板页：客户端 / 模式 / 只读开关 ===')
tp._set_target('dsh')
app.processEvents()
chk('客户端按钮同步', tp._sel_target == 'dsh' and tp._tgt_btns['dsh'].isChecked())
tp._set_target('pideck')
tp._set_mode('menu')
app.processEvents()
chk('模式按钮同步', tp._sel_mode == 'menu' and tp._mode_btns['menu'].isChecked())
chk('极简模式说明', '按需读取' in tp._mode_hint.text())
chk('只读勾选框存在', hasattr(tp, '_ro_chk') and tp._ro_chk.text().startswith('部署后设只读'))
tp._ro_chk.setChecked(True)
app.processEvents()
chk('只读开关落配置', (m.read_app_config() or {}).get('readonlySkills') is True)
tp._ro_chk.setChecked(False)
app.processEvents()

print('=== 3 技能库页：tab / 搜索 / 导入入口 ===')
sp = w.page_skills
sp.switch_target(1)
app.processEvents()
chk('技能库 tab 切换', sp._target_index == 1)
sp._search_box.setText('pwn')
app.processEvents()
sp._search_box.setText('')
app.processEvents()
chk('导入技能按钮存在', hasattr(sp, '_import_btn') and sp._import_btn.text() == '导入技能')
chk('导入处理器存在', callable(getattr(sp, '_import_skills', None)))

print('=== 4 极简部署：菜单技能就位 + 模块被标记 ===')
tp.select_addons()
tp._on_addon_toggle('gate', True, tp._addon_checks['gate'])
tp._on_addon_toggle('boundary', True, tp._addon_checks['boundary'])
tp._deploy_addons()
app.processEvents()
run_task()
menu = os.path.join(sk, 'pi-workbench-menu', 'SKILL.md')

# 极简模式需要先切回完整再切极简（走一遍真实路径）
tp._set_mode('full')
tp.select_group(0)
tp._deploy_template('v52c', 'V5.2c', False)
app.processEvents()
run_task()


def flagged():
    out = set()
    if not os.path.isdir(sk):
        return out
    for n in os.listdir(sk):
        p = os.path.join(sk, n, 'SKILL.md')
        if os.path.isfile(p) and 'disable-model-invocation: true' in open(p, encoding='utf-8', errors='replace').read():
            out.add(n)
    return out


tp._set_mode('menu')
tp.select_group(0)
tp._deploy_template('v52c', 'V5.2c', False)
app.processEvents()
run_task()
chk('极简部署：菜单技能就位', os.path.exists(menu))
f1 = flagged()
chk('极简部署：普通模块带标记', len(f1) >= 60, '带标记 %d 个' % len(f1))
chk('极简部署：附属模板保持常驻', 'code-quality-gate' not in f1 and 'task-boundary' not in f1)

print('=== 5 切回完整：菜单技能删除 + 标记清零 ===')
tp._set_mode('full')
tp.select_group(0)
tp._deploy_template('v52c', 'V5.2c', False)
app.processEvents()
run_task()
chk('切回完整：菜单技能已删', not os.path.exists(menu))
chk('切回完整：标记已清零', len(flagged()) == 0, '剩余 %d' % len(flagged()))

print('=== 6 只读保护：GUI 勾上后部署应带 -Readonly ===')
tp._ro_chk.setChecked(True)
app.processEvents()
captured = []
_old_launch = w._launch
w._launch = lambda args, label, on_done, kind='操作', target_card='': captured.append(list(args))
tp.select_group(0)
tp._deploy_template('v52c', 'V5.2c', False)
app.processEvents()
w._launch = _old_launch
chk('部署参数含 -Readonly', any('-Readonly' in a for a in captured),
    str([a for a in captured for a in a if a == '-Readonly'][:1]))
tp._ro_chk.setChecked(False)
app.processEvents()

print('=== 7 卸载：技能目录清空 + state 删除 ===')
old_q = m.QMessageBox.question
m.QMessageBox.question = staticmethod(lambda *a, **k: m.QMessageBox.Yes)
w._uninstall([t for t in m.TARGETS if t['key'] == 'pideck'][0])
app.processEvents()
run_task()
m.QMessageBox.question = old_q
chk('卸载：技能目录清空', not os.path.isdir(sk) or len(os.listdir(sk)) == 0)
chk('卸载：state 已删', not os.path.exists(m.state_path('pideck')))

print('=== 8 版本窗 / 任务构建 / 体检两端 / 重启探测 ===')
seen = {}
_old_exec = m.VersionsDialog.exec


def _fake_ver(self):
    seen['diff_hidden'] = not self.diff.isVisible()
    seen['has_diff'] = hasattr(self, 'diff')
    return 0


m.VersionsDialog.exec = _fake_ver
w._versions([t for t in m.TARGETS if t['key'] == 'pideck'][0])
app.processEvents()
run_task()
m.VersionsDialog.exec = _old_exec
chk('版本窗：差异面板默认收起', seen.get('has_diff') and seen.get('diff_hidden'))

seen2 = {}
_old_exec2 = m.TaskComposeDialog.exec


def _fake_task(self):
    seen2['p'] = len(self._pbtn)
    seen2['c'] = len(self._cbtn)
    seen2['f'] = len(self._fbtn)
    seen2['presets'] = len(self.PRESETS)
    return 0


m.TaskComposeDialog.exec = _fake_task
w._compose()
m.TaskComposeDialog.exec = _old_exec2
chk('任务构建：档位5/通道7/格式3/预设3',
    seen2 == {'p': 5, 'c': 7, 'f': 3, 'presets': 3}, str(seen2))

enq = []
_old_enq = w._enqueue
w._enqueue = lambda args, label, on_done=None, kind='操作', target_card='': enq.append((list(args), label))
m.QMessageBox.question = staticmethod(lambda *a, **k: m.QMessageBox.Yes)
w._probe_all()
chk('体检两端：两次探针入队', len(enq) == 2 and all('-Probe' in a for a, _ in enq), str([a for a, _ in enq]))
enq.clear()
chk('硬闸生效：测试不可杀进程', m.kill_is_blocked() and m.kill_procs(['PiDeck']) == [])
tgt = [t for t in m.TARGETS if t['key'] == 'pideck'][0]
w._restart(tgt)     # 杀进程已被硬闸拦住（空转），这里只验证它随后走 -FindExe 探测
chk('重启：改为走 -FindExe 探测', any('-FindExe' in a for a, _ in enq), str([a for a, _ in enq]))
m.QMessageBox.question = old_q
w._enqueue = _old_enq
app.processEvents()

print()
print('回归结果:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)