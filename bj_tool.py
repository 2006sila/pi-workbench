#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pi用学习工作台 —— Pi 系客户端一键部署

目标端：PiDeck（APPEND_SYSTEM.md）、DeepSeek Harness（$DSH_HOME/AGENTS.md）
所有注入均先备份，卸载即还原。
"""

import codecs
import json
import os
import subprocess
import sys
import time

from PySide6.QtCore import (Qt, QThread, Signal, QTimer, QUrl, QPropertyAnimation, QEasingCurve,
                            Property, QPoint, QRect)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon,
                           QPainter, QPixmap, QPen)

from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout,
    QGridLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QSystemTrayIcon,
    QTableWidget, QTextBrowser, QTableWidgetItem, QVBoxLayout, QWidget,
)

APP_NAME = 'pi用学习工作台'
APP_SUBTITLE = 'PiDeck / DeepSeek Harness 一键部署 · 注入即用 · 卸载即还原'
APP_VERSION = 'V1.3'
APP_BUILD = '2026-09-26 · v1.3 事务式部署 + 版本恢复 · 通道体检 · 任务构建'

_ACTIVE_WINDOW = None
_THEME_FILTER = None      # 系统主题监听器：必须持引用，否则可能被 GC 后悬垂

IS_WINDOWS = os.name == 'nt'
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

_SKILL_COUNT_CACHE = None      # 随包技能数缓存（bundled_skill_count 用）

# 单任务超时上限（秒）。注入 65 个技能实测不到 1 分钟；体检要跑一次模型调用，
# 所以给得宽。卡死的任务由它兜底杀整棵进程树，不再拖住界面。
TASK_TIMEOUT_SEC = 1800

# ------------------------------------------------------------------ 主题
# 设计语言参考：PyQt-Fluent-Widgets + PyQtDarkTheme
# 深色：Fluent 分层 #191919 < #242424 < #2B2B2B；浅色：Fluent 浅色分层

DARK = {
    'BG':             '#191919',
    'SURFACE':        '#1F1F1F',
    'SIDEBAR_BG':     '#1F1F1F',
    'CARD_BG':        '#242424',
    'CARD_BG_HOVER':  '#2B2B2B',
    'BORDER':         '#333333',
    'BORDER_LIGHT':   '#2A2A2A',
    'TEXT_PRIMARY':   '#F2F2F2',
    'TEXT_SECONDARY': '#A6A6A6',
    'TEXT_MUTED':     '#6E6E6E',
    'ACCENT':         '#4C8DFF',
    'ACCENT_HOVER':   '#3B7AF0',
    'ACCENT_LIGHT':   '#1E3A5F',
    'ACCENT_GLOW':    '#6FA8FF',
    'SUCCESS':        '#6CCB5F',
    'SUCCESS_LIGHT':  '#1F3323',
    'DANGER':         '#FF99A4',
    'DANGER_SOLID':   '#C42B1C',
    'DANGER_HOVER':   '#A82A1F',
    'DANGER_LIGHT':   '#2A1616',
    'WARN':           '#FCE100',
    'WARN_LIGHT':     '#3B331A',
    'ICON_C1':        '#4C8DFF', 'ICON_C2': '#7B5CFF', 'ICON_C2_ALT': '#00B7C3',
    'LOG_BG':         '#121212',
    'LOG_FG':         '#D4D4D4',
    'HOVER_ROW':      '#2B2B2B',
}

LIGHT = {
    'BG':             '#F5F6F8',
    'SURFACE':        '#FFFFFF',
    'SIDEBAR_BG':     '#FFFFFF',
    'CARD_BG':        '#FFFFFF',
    'CARD_BG_HOVER':  '#F0F4FA',
    'BORDER':         '#DEE3EC',
    'BORDER_LIGHT':   '#E9EDF3',
    'TEXT_PRIMARY':   '#1A1F28',
    'TEXT_SECONDARY': '#5C6470',
    'TEXT_MUTED':     '#9AA3AE',
    'ACCENT':         '#2F6FED',
    'ACCENT_HOVER':   '#2559C4',
    'ACCENT_LIGHT':   '#E3EDFF',
    'ACCENT_GLOW':    '#1D4FBF',
    'SUCCESS':        '#107C41',
    'SUCCESS_LIGHT':  '#E6F4EA',
    'DANGER':         '#C42B1C',
    'DANGER_SOLID':   '#C42B1C',
    'DANGER_HOVER':   '#A82A1F',
    'DANGER_LIGHT':   '#FDECEA',
    'WARN':           '#9D5D00',
    'WARN_LIGHT':     '#FFF4CE',
    'ICON_C1':        '#2F6FED', 'ICON_C2': '#7B5CFF', 'ICON_C2_ALT': '#00B7C3',
    'LOG_BG':         '#FFFFFF',
    'LOG_FG':         '#333A45',
    'HOVER_ROW':      '#F0F4FA',
}

THEME_NAME = 'dark'   # 'dark' | 'light'
C = dict(DARK)


def set_theme(name):
    """切换全局调色板。在创建窗口前或热重建窗口时调用。"""
    global THEME_NAME, C
    THEME_NAME = name
    C.clear()
    C.update(LIGHT if name == 'light' else DARK)


def read_app_config():
    return read_json(tool_config_path()) or {}


def write_json(path, obj):
    """原子写 JSON：先写同目录临时文件再 os.replace。
    直接 open(w) 会先截断文件，中途失败（断电/被杀进程）就留下半截 JSON，
    read_json 只能返回 None——表现为「设置自己丢了」。
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_app_config(key, value):
    p = tool_config_path()
    cfg = read_json(p) or {}
    cfg[key] = value
    try:
        write_json(p, cfg)
    except Exception:
        pass


def history_path():
    return os.path.join(work_root(), 'history.json')


def history_add(kind, target_card, detail, ok):
    """追加一条操作历史。kind: 注入/卸载/自检；ok: 布尔。"""
    try:
        p = history_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        records = []
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as fh:
                    records = json.load(fh)
            except Exception:
                records = []
        records.insert(0, {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'kind': kind,
            'target': target_card,
            'detail': detail,
            'ok': bool(ok),
        })
        records = records[:300]   # 上限 300 条
        write_json(p, records)
    except Exception:
        pass


def history_load():
    try:
        with open(history_path(), 'r', encoding='utf-8') as fh:
            return json.load(fh) or []
    except Exception:
        return []


def _autostart_key():
    import winreg
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                          r'Software\Microsoft\Windows\CurrentVersion\Run',
                          0, winreg.KEY_SET_VALUE)


def set_autostart(enabled):
    """开机自启：写入/移除注册表 Run 项。"""
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        # frozen 下 sys.executable 就是 exe 本体全路径；sys.argv[0] 可能是相对路径
        # （从别的工作目录启动时），写进注册表会变成无效项。
        if getattr(sys, 'frozen', False):
            cmd = '"' + os.path.abspath(sys.executable) + '"'
        else:
            cmd = '"' + os.path.abspath(sys.executable) + '" "' + os.path.abspath(sys.argv[0]) + '"'
        if enabled:
            with _autostart_key() as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                with _autostart_key() as k:
                    winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
        return True
    except Exception:
        return False


def is_autostart_on():
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Run') as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _system_theme():
    """读注册表取系统应用主题：'light' / 'dark'（失败回退 dark）。"""
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
                v, _ = winreg.QueryValueEx(k, 'AppsUseLightTheme')
                return 'light' if v else 'dark'
        except Exception:
            pass
    return 'dark'


def _cfg_theme():
    """配置里存的主题档：'dark' / 'light' / 'auto'。"""
    cfg = read_json(tool_config_path()) or {}
    t = cfg.get('theme')
    return t if t in ('dark', 'light', 'auto') else 'dark'


def _install_theme_listener(callback):
    """监听系统主题变更（WM_SETTINGCHANGE broadcast），触发回调。仅 Windows。"""
    if not IS_WINDOWS:
        return None
    from PySide6.QtCore import QAbstractNativeEventFilter
    import ctypes.wintypes as wt

    WM_SETTINGCHANGE = 0x001A
    class _Filter(QAbstractNativeEventFilter):
        def __init__(self):
            super().__init__()
            self._last = _system_theme()
        def nativeEventFilter(self, eventType, message):
            if eventType == b'windows_generic_MSG':
                try:
                    msg = wt.MSG.from_address(int(message))
                    if msg.message == WM_SETTINGCHANGE:
                        cur = _system_theme()
                        if cur != self._last:
                            self._last = cur
                            callback(cur)
                except Exception:
                    pass
            return False, 0
    f = _Filter()
    QApplication.instance().installNativeEventFilter(f)
    return f


def _c(key):
    return C[key]


# ------------------------------------------------------------------ 目标端定义

TARGETS = [
    {
        'key': 'pideck',
        'section': 'PiDeck',
        'card': 'PiDeck',
        'desc': ('配置根 ~/.pi/agent。追加系统提示词 APPEND_SYSTEM.md（每轮进系统提示词，'
                 '压缩后仍在）+ {skills} 个模块技能库（含 2 个附加包）。PiDeck 自带的 usage-probe / image-gen '
                 '技能不受影响。DSH 后端不做处理。原文件先备份，卸载可还原。'),
        'agent_dir': ('~', '.pi', 'agent'),
        'procs': ['PiDeck', 'pi-desktop'],
        'exe_hints': [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'PiDeck', 'PiDeck.exe'),
            os.path.join(os.environ.get('ProgramFiles', ''), 'PiDeck', 'PiDeck.exe'),
        ],
    },
    {
        'key': 'dsh',
        'section': 'DeepSeek Harness',
        'card': 'DeepSeek Harness',
        'desc': ('配置根 $DSH_HOME（未设置时 ~/.dsh）。harness 把 $DSH_HOME/AGENTS.md '
                 '作为持久 user 消息（<system-reminder>）注入提示词；技能走 $DSH_HOME/skills。'
                 '另写一层 home 级 cordis.patch.yml，把 agent-instructions 的 maxBytes '
                 '预算抬到能装下完整指令集（预算不足时 dsh 会整份丢弃 AGENTS.md）。'),
        'procs': ['DeepSeek Harness', 'DeepSeekHarness', 'deepseek-harness', 'dsh'],
        'exe_hints': [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'DeepSeek Harness', 'DeepSeek Harness.exe'),
            os.path.join(os.environ.get('ProgramFiles', ''), 'DeepSeek Harness', 'DeepSeek Harness.exe'),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'dsh', 'dsh.exe'),
        ],
    },
]

# 模板按目标模型分组：(key, 显示名, 描述, no_skills, 推荐)
#   no_skills=True  → 精简版，只注入指令集
MODEL_GROUPS = [
    {
        'id': 'ds41f',
        'label': 'DeepSeek 4.1 Flash',
        'note': '实测基线：25 轮 x 12 用例 = 300 次/模板',
        'versions': [
            ('v52c', 'V5.2c（推荐）',
             'V5.1b 全量 + 3 条结构化反面示例。实测：deepseek-v4.1-flash 99.3%~100%（n=300）。',
             False, True),
            ('v52cx', 'V5.2c 增强版',
             'V5.2c 正文原样 + 三个追加块：环境条款效力（优先级/不可降级）、'
             '术语归一化补表（分域口语词对照）、交付纪律（执行前四要素预览 + 交付记录优先）。'
             '追加块单独维护，不动 V5.2c 本体，便于对照实测。',
             False, False),
            ('v51b', 'V5.1b',
             '自研·通用。V5 + 请求归一化（口语/黑话/代词直接出通用完整版）+ 术语工程语义映射。'
             '实测 98.3%~100%（n=120~300）。V5.2c 的无反面示例版本。',
             False, False),
            ('v5docs', 'V5 文档引擎',
             '自研·通用。V5.1b 的母版：把请求重定义为「生成文档」而非「执行操作」，'
             '去掉全部对抗性元指令。体积只有 V5.1b 的 65%。实测 94.2%（10 轮 x 12 用例 = 120 次）。',
             False, False),
        ],
    },
    {
        'id': 'gpt',
        'label': 'GPT',
        'note': '上游 aimeoa 开源仓库（Astra6 实测：契约级 15+ 次全交付、拒绝率 0%）。'
                '本机无 GPT 通道，未实测。',
        'versions': [
            ('gptastra', 'GPT-6 Astra（推荐）',
             '收编自上游开源仓库。操作契约路线：绑工作区对象 + 四角色产物'
             '（MODIFIED/DIFF/VERIFICATION/ROLLBACK）+ 实测三种行为。needle 0 处。',
             False, True),
            ('gpt56sol', 'GPT-5.6 Sol v45',
             '收编自上游开源仓库。同路线的精简版，'
             '多一个PROCESS_RECORD 分支（叙事/生物类）。needle 0 处。',
             False, False),
        ],
    },
    {
        'id': 'glm53f',
        'label': 'GLM-5.3-Flash',
        'note': '已知该模型会做 frame legitimacy check（逐字复述载荷后整条拒答）。两个候选均未实测。',
        'versions': [
            ('glmneutral', 'GLM 中性化（推荐）',
             'V5.1b 骨架 + GLM 适配段（思考通道用法 + 框架层拦截的应对）。needle 0 处。自研，未实测。',
             False, True),
            ('glm53f', 'GLM5.3f Kovak（上游原味）',
             '收编自 chiina66/glm5.3f-jailbreak。Kovak 人设 + 思考通道锚定 + 干扰协议。'
             'needle 6 处（低密度）。上游无量化数据。',
             False, False),
        ],
    },
]

# 扁平化版本表：key -> (label, desc, no_skills, recommended)
VERSIONS = []
for _g in MODEL_GROUPS:
    for _v in _g['versions']:
        VERSIONS.append(_v)

# COMPOSED：成品文件不预置，运行时用「头部 + 正文」现场拼，
# 正文只在仓库里存一份，改动不会漏同步。
# COMPOSED：成品文件不预置，运行时按「部件列表」顺序拼起来。
# 第一个元素是成品名，第二个是有序部件列表：头部 + 正文（+ 可选追加块）。
# 追加块只写一份，可被多个版本引用；不动已实测模板的头部/正文，就不会污染它们的基线数据。
COMPOSED = {
    'v5docs': ('v5-docs.md',    ['_sandbox-v5-header.md', 'v5-body.md']),
    'v52c':   ('v5-2c.md',      ['_v52c-header.md', 'v5-body.md']),
    'v51b':   ('v5-1b.md',      ['_v51b-header.md', 'v5-body.md']),
    # 增强版 = V5.2c 原样 + 三个追加块（环境条款效力 / 术语归一化补表 / 交付纪律）
    'v52cx':  ('v5-2c-ext.md',  ['_v52c-header.md', '_ext-auth.md', 'v5-body.md',
                                 '_ext-subst.md', '_ext-deliver.md']),
}

# 已退役版本 key -> 现存版本 key（旧状态清单迁移用）
RETIRED_VERSIONS = {
    'v3': 'v5docs',
    'flash': 'v5docs',
    'v3en': 'v5docs',
    'seagull': 'v5docs',
    'seagull12': 'v5docs',
    'v4lite': 'v5docs',
    'hs-v3': 'v5docs',
    'hs-v3-en': 'v5docs',
    'hs-flash-v2': 'v5docs',
    'seagull-zcode': 'v5docs',
    'seagull-v12-full': 'v5docs',
    'v4': 'v5docs',
}


# ------------------------------------------------------------------ 附加模板（独立技能包）

# 附加模板：可单独部署、也可与指令集模板一起部署的技能包。
#   skill_dir  = 源目录（相对随包根；inject.ps1 -SkillsSource 用，可分号分隔多源）
#   skill_name = 目标端 skills/ 下的目录名（inject.ps1 -RemoveAddons 用）
# 两者必须分开：旧版只用一个字段，导致移除时把源路径当目标名拼、部署时又把
# 目标名当源路径拼，两边都不成立。
ADDONS = [
    {
        'key': 'gate',
        'name': '工程自检门禁',
        'desc': ('交付前 9 维自检评分表：改动收敛/可定位/命名/测试/构建/错误码/'
                 '依赖锁定/模块边界/敢接手，+ 扣分项与未证明项，≥7 分通过，'
                 '输出结构化报告。对齐工程自检评分表流程。'),
        'skill_dir': 'skills-v4/code-quality-gate',
        'skill_name': 'code-quality-gate',
        'trigger': '说「按质量门禁自检 / 帮我评分 / 交付前检查」触发',
    },
    {
        'key': 'boundary',
        'name': '任务边界守卫',
        'desc': ('干活时防四类反模式：范围膨胀 / 无用防御 / 意图越界 / 任务打转。'
                 '任务模式契约（review 只读 / change 授权内），Stop Ladder 五级判断，'
                 '多余动作即停。与自检门禁互补：这个管别多干，那个管干得好。'),
        'skill_dir': 'skills-v4/task-boundary',
        'skill_name': 'task-boundary',
        'trigger': '说「只 review 别改 / 别多干 / 防跑偏」触发',
    },
]


# ------------------------------------------------------------------ 工具函数

def _res(rel=''):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel) if rel else base


def bundle_skill_count():
    """随包技能库的顶层技能数（含附加包）。界面上要显示真实数字，不能写死。"""
    d = _res('skills-v4')
    n = 0
    if os.path.isdir(d):
        for name in os.listdir(d):
            if os.path.exists(os.path.join(d, name, 'SKILL.md')):
                n += 1
    return n


def bundled_skill_count():
    """随包技能数。界面文案里不要写死 65 —— 技能库一改就过期。"""
    global _SKILL_COUNT_CACHE
    if _SKILL_COUNT_CACHE is None:
        d = _res('skills-v4')
        try:
            _SKILL_COUNT_CACHE = sum(
                1 for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, 'SKILL.md')))
        except OSError:
            _SKILL_COUNT_CACHE = 0
    return _SKILL_COUNT_CACHE


def home_dir():
    return os.environ.get('USERPROFILE') or os.path.expanduser('~')


def work_root():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'pi-workbench')


def state_path(target_key):
    return os.path.join(work_root(), 'state', target_key + '.json')


def last_run_path(target_key):
    return os.path.join(work_root(), 'last-run-' + target_key + '.json')


def read_model_status(target_key):
    """读 inject.ps1 落盘的 last-run JSON 里的 modelStatus。

    inject.ps1 会显式写出「本工具只处理文件，不代表客户端已加载或已生效」。
    这句话要落在界面上给人看到，而不是只留在文档里当承诺。
    """
    if not target_key:
        return ''
    try:
        with open(last_run_path(target_key), 'r', encoding='utf-8') as fh:
            data = json.load(fh) or {}
        return str(data.get('modelStatus') or '')
    except Exception:
        return ''


def tool_config_path():
    return os.path.join(work_root(), 'tool-config.json')


def dsh_home():
    env = (os.environ.get('DSH_HOME') or '').strip()
    if env:
        return os.path.abspath(env)
    return os.path.join(home_dir(), '.dsh')


def resolve_version_key(version_key):
    if not version_key:
        return 'v52c'
    if version_key in RETIRED_VERSIONS:
        return RETIRED_VERSIONS[version_key]
    known = {v[0] for g in MODEL_GROUPS for v in g['versions']}
    if version_key in known:
        return version_key
    return 'v52c'


def _prompt_file(version_key):
    return {
        'v5docs': 'v5-docs.md',
        'v52c': 'v5-2c.md',
        'v51b': 'v5-1b.md',
        'gptastra': '_gpt6-astra-header.md',
        'gpt56sol': '_gpt56sol-header.md',
        'glmneutral': '_glm-neutral-header.md',
        'glm53f': '_glm53f-kovak.md',
    }.get(version_key, 'v5-docs.md')


def ensure_prompt(version_key):
    """返回可直接给 -SourcePrompt 的指令集文件路径。

    COMPOSED 里的版本不预置成品文件，而是按部件列表现场拼出（头部 + 正文 + 可选追加块），
    这样正文只存一份、追加块也只存一份，改动不会漏同步；
    已实测模板的部件不动，就不会被动到它们的基线数据。
    """
    version_key = resolve_version_key(version_key)
    if version_key not in COMPOSED:
        # 非拼装版本：直接用随包成品文件
        return _res(os.path.join('prompts', _prompt_file(version_key)))

    out_name, parts = COMPOSED[version_key]
    paths = [_res(os.path.join('prompts', p)) for p in parts]

    def _read(p):
        try:
            with open(p, 'r', encoding='utf-8') as fh:
                return fh.read().replace('\r\n', '\n')
        except Exception:
            return ''

    texts = []
    for p in paths:
        if not os.path.exists(p):
            continue
        # 所有部件都去尾空行，拼接时统一用两个换行分隔；非首部件去掉 BOM
        t = _read(p).rstrip('\n')
        if texts:
            t = t.lstrip('\ufeff')
        texts.append(t)
    if not texts:
        return paths[-1]

    out_dir = os.path.join(work_root(), 'prompts')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, out_name)
    text = '\n\n'.join(texts) + '\n'
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    os.replace(tmp, out)
    return out


def resolve_agent_dir(target):
    if target['key'] == 'dsh':
        return dsh_home()
    return os.path.join(home_dir(), '.pi', 'agent')


def prompt_name(target):
    if target['key'] == 'dsh':
        return 'AGENTS.md'
    return 'APPEND_SYSTEM.md'


def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def list_procs(names):
    """返回正在运行的进程名集合（小写比较）。"""
    if not IS_WINDOWS:
        return set()
    try:
        out = subprocess.run(
            ['tasklist', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            creationflags=CREATE_NO_WINDOW, timeout=15, check=False,
        ).stdout or ''
    except Exception:
        return set()
    running = set()
    lowered = [n.lower() for n in names]
    for line in out.splitlines():
        low = line.lower()
        for n in lowered:
            if ('"' + n + '.exe"') in low:
                running.add(n)
    return running


def kill_procs(names):
    killed = []
    for n in names:
        try:
            r = subprocess.run(
                ['taskkill', '/IM', n + '.exe', '/F', '/T'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW, timeout=20, check=False,
            )
            if r.returncode == 0:
                killed.append(n)
        except Exception:
            pass
    return killed


def open_in_explorer(path):
    try:
        if IS_WINDOWS and os.path.exists(path):
            os.startfile(path)  # noqa: S606
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception:
        pass


# ------------------------------------------------------------------ 免责声明

class Runner(QThread):
    """在后台线程里跑 PowerShell。

    为什么不用 QProcess：PySide6 没暴露 setCreateProcessArgumentsModifier，
    QProcess 启动控制台程序时不带 CREATE_NO_WINDOW，GUI 进程（无控制台）会因此
    给子进程新建一个可见的 conhost 窗口——表现为每次注入/自检/卸载闪一个黑窗。
    subprocess 能直接指定 creationflags，同时顺带解决两个老问题：
      · 按行切分 + 增量解码（旧实现逐块 decode，中文多字节被切断会出现乱码）
      · 线程对象随 finished 回收（旧实现每任务泄一个 QProcess）
    """

    line = Signal(str)
    done = Signal(int)

    MAX_TAIL = 400
    # 单任务输出行数上限：超了就只记不显示（子进程的 stdout 还得继续读，
    # 否则它写满管道会阻塞在原地）。防的是「某个环节卡在循环里往外刷」把界面刷死。
    STORM_LIMIT = 20000

    def __init__(self, argv, parent=None):
        super().__init__(parent)
        self._argv = argv
        self._proc = None
        self._tail = []
        self._lines = 0
        self._stormed = False

    def run(self):
        dec = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            self._proc = subprocess.Popen(
                self._argv,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW, bufsize=0,
            )
        except Exception as e:
            self.line.emit('[!] 无法启动 PowerShell: ' + str(e))
            self.done.emit(-1)
            return
        pending = ''
        while True:
            try:
                chunk = self._proc.stdout.read(4096)
            except Exception:
                chunk = b''
            if not chunk:
                break
            pending += dec.decode(chunk)
            while True:
                nl = pending.find('\n')
                if nl < 0:
                    break
                self._emit(pending[:nl])
                pending = pending[nl + 1:]
        pending += dec.decode(b'', True)
        if pending:
            self._emit(pending)
        try:
            code = self._proc.wait()
        except Exception:
            code = -1
        self.done.emit(code)

    def _emit(self, raw):
        text = raw.rstrip('\r')
        if text.strip():
            self._tail.append(text)
            if len(self._tail) > self.MAX_TAIL:
                del self._tail[:len(self._tail) - self.MAX_TAIL]
            self._lines += 1
            if self._lines > self.STORM_LIMIT:
                if not self._stormed:
                    self._stormed = True
                    self.line.emit('[WARN] 输出行数超过 ' + str(self.STORM_LIMIT)
                                   + '，后续只读不显示（防界面被刷死）；文件日志不受影响')
                return
            self.line.emit(text)

    def tail(self):
        return list(self._tail)

    def terminate_tree(self):
        """杀掉整棵进程树（退出程序时用，避免 PowerShell 死在写文件中途）。"""
        p = self._proc
        if p is None or p.poll() is not None:
            return
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                           creationflags=CREATE_NO_WINDOW, timeout=10, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


AGREEMENT_TEXT = """pi用学习工作台 · 使用说明

本工具用于本机已安装的 Pi 系客户端（PiDeck / DeepSeek Harness）的
提示词与技能库部署，适用对象为自有设备与实验室环境。

工作方式
  1. 只写入客户端自己的提示词／配置根：
       · PiDeck     ~/.pi/agent/APPEND_SYSTEM.md 中的一个带标记的管理段
       · DSH        $DSH_HOME/AGENTS.md 中的一个带标记的管理段
                    + $DSH_HOME/cordis.patch.yml 中的一层 maxBytes 预算
       · 两边都会写 skills/ 下由本工具部署的技能目录
  2. 写入前备份原文件，卸载时精确摘除管理段并还原备份。
  3. 不修改任何客户端本体、不写任何可执行代码、不动客户端自带技能。

还原保证
  卸载只按状态清单 (.json) 里记录的条目操作；与客户端自带技能同名的用户技能
  会先备份再覆盖，卸载时原样还原。

请确认你了解以上行为并继续。"""


# ------------------------------------------------------------------ 样式

def _set_px_font(widget, px, bold=False):
    """用 QFont 像素字号设置字体（QSS font-size 不影响布局计算，会造成文字越界重叠）。"""
    f = QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    widget.setFont(f)


def _mk_label(text, px, color_key='TEXT_PRIMARY', bold=False, wrap=False):
    """用 QFont（而非 QSS font-size）设置字号，确保 sizeHint 正确、布局不被文字穿透。"""
    lbl = QLabel(text)
    f = QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    lbl.setFont(f)
    lbl.setStyleSheet('color: ' + C[color_key] + '; background: transparent; border: none;')
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def _btn_style(kind):
    if kind == 'primary':
        return ('QPushButton { background: ' + C['ACCENT'] + '; color: #FFFFFF; border: none;'
                ' border-radius: 8px; font-weight: 600; padding: 0 18px; }'
                'QPushButton:hover { background: ' + C['ACCENT_HOVER'] + '; }'
                'QPushButton:pressed { background: ' + C['ACCENT_HOVER'] + '; }'
                'QPushButton:disabled { background: ' + C['BORDER'] + '; color: ' + C['TEXT_MUTED'] + '; }')
    if kind == 'danger':
        return ('QPushButton { background: transparent; color: ' + C['DANGER'] + ';'
                ' border: 1px solid ' + C['DANGER_SOLID'] + ';'
                ' border-radius: 8px; font-weight: 600; padding: 0 16px; }'
                'QPushButton:hover { background: ' + C['DANGER_SOLID'] + '; color: #FFFFFF; }'
                'QPushButton:pressed { background: ' + C['DANGER_HOVER'] + '; color: #FFFFFF; }'
                'QPushButton:disabled { color: ' + C['TEXT_MUTED'] + '; border-color: ' + C['BORDER'] + '; }')
    if kind == 'ghost':
        return ('QPushButton { background: transparent; color: ' + C['TEXT_SECONDARY'] + ';'
                ' border: 1px solid ' + C['BORDER'] + '; border-radius: 8px;'
                ' font-weight: 500; padding: 0 14px; }'
                'QPushButton:hover { color: ' + C['TEXT_PRIMARY'] + '; border-color: ' + C['ACCENT'] + ';'
                ' background: ' + C['CARD_BG_HOVER'] + '; }'
                'QPushButton:disabled { color: ' + C['TEXT_MUTED'] + '; border-color: ' + C['BORDER_LIGHT'] + '; }')
    if kind == 'link':
        # Qt 的 QSS 不支持 text-decoration（写了也无效），下划线效果靠颜色与 hover 区分
        return ('QPushButton { background: transparent; color: ' + C['ACCENT'] + '; border: none; }'
                'QPushButton:hover { color: ' + C['ACCENT_GLOW'] + '; }')
    if kind == 'chip':
        # 可切换的选择器（任务构建器的档位 / 通道）：必须给 :checked 上色，否则选不选看不出来
        return ('QPushButton { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_SECONDARY'] + ';'
                ' border: 1px solid ' + C['BORDER'] + '; border-radius: 8px; font-weight: 500; padding: 0 10px; }'
                'QPushButton:hover { border-color: ' + C['ACCENT'] + '; color: ' + C['TEXT_PRIMARY'] + '; }'
                'QPushButton:checked { background: ' + C['ACCENT'] + '; color: #FFFFFF;'
                ' border-color: ' + C['ACCENT'] + '; font-weight: 600; }')
    return ('QPushButton { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_PRIMARY'] + ';'
            ' border: 1px solid ' + C['BORDER'] + '; border-radius: 8px;'
            ' font-weight: 500; padding: 0 14px; }'
            'QPushButton:hover { background: ' + C['CARD_BG_HOVER'] + '; border-color: ' + C['ACCENT'] + '; }'
            'QPushButton:pressed { background: ' + C['BORDER_LIGHT'] + '; }'
            'QPushButton:disabled { color: ' + C['TEXT_MUTED'] + '; border-color: ' + C['BORDER_LIGHT'] + '; }')


def _make_icon_pm(size, c1, c2, glyph, gsize):
    """渐变圆角图标底座 + 单字。"""
    from PySide6.QtGui import QLinearGradient
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(c1))
    g.setColorAt(1, QColor(c2))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(1, 1, size - 2, size - 2, int(size * 0.26), int(size * 0.26))
    p.setPen(QColor(255, 255, 255, 70))
    p.setBrush(Qt.NoBrush)
    p.drawArc(int(size*0.18), int(size*0.18), int(size*0.64), int(size*0.64), 30 * 16, 120 * 16)
    p.setPen(QColor('#FFFFFF'))
    f = QFont()
    f.setPointSize(gsize)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, glyph)
    p.end()
    return pm


def _glyph_icon(kind, color):
    """用 QPainter 画简单几何图标（避免引入图标库）。"""
    pm = QPixmap(22, 22)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    if kind == 'home':
        p.drawRoundedRect(3, 9, 7, 10, 2, 2)
        p.drawRoundedRect(12, 3, 7, 16, 2, 2)
    elif kind == 'grid':
        p.drawRoundedRect(3, 3, 7, 7, 2, 2)
        p.drawRoundedRect(12, 3, 7, 7, 2, 2)
        p.drawRoundedRect(3, 12, 7, 7, 2, 2)
        p.drawRoundedRect(12, 12, 7, 7, 2, 2)
    elif kind == 'plug':
        p.drawRoundedRect(9, 3, 4, 7, 2, 2)
        p.drawRoundedRect(5, 8, 12, 8, 3, 3)
        p.drawRoundedRect(8, 16, 2, 3, 1, 1)
        p.drawRoundedRect(12, 16, 2, 3, 1, 1)
    elif kind == 'task':
        # 文档 + 勾选：任务契约
        p.drawRoundedRect(3, 2, 16, 18, 3, 3)
        p.setPen(QPen(QColor(C['BG']), 2.0))
        p.drawLine(7, 8, 15, 8)
        p.drawLine(7, 12, 15, 12)
    elif kind == 'clock':
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(color), 2.0))
        p.drawEllipse(4, 4, 14, 14)
        p.drawLine(11, 11, 11, 6)
        p.drawLine(11, 11, 15, 13)
    elif kind == 'term':
        p.drawRoundedRect(2, 4, 18, 14, 3, 3)
        p.setPen(QColor(C['BG']))
        f = QFont()
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, '>')
    elif kind == 'gear':
        p.drawEllipse(6, 6, 10, 10)
        p.setBrush(QColor(C['BG']))
        p.drawEllipse(9, 9, 4, 4)
    elif kind == 'search':
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(color), 2.0))
        p.drawEllipse(4, 4, 9, 9)
        p.drawLine(12, 12, 18, 18)
    elif kind == 'trash':
        p.drawRoundedRect(5, 7, 12, 12, 2, 2)
        p.drawRoundedRect(4, 4, 14, 2, 1, 1)
        p.drawRoundedRect(9, 2, 4, 2, 1, 1)
    elif kind == 'book':
        p.drawRoundedRect(3, 4, 7, 14, 2, 2)
        p.drawRoundedRect(12, 4, 7, 14, 2, 2)
    elif kind == 'folder':
        p.drawRoundedRect(3, 6, 16, 11, 2, 2)
        p.drawRoundedRect(3, 4, 8, 4, 2, 2)
    p.end()
    return pm


# ------------------------------------------------------------------ 免责确认

class AgreementDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME + ' · 使用说明')
        self.setFixedSize(660, 470)
        self.setStyleSheet('QDialog { background: ' + C['BG'] + '; }')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        title = _mk_label(APP_NAME, 18, 'TEXT_PRIMARY', bold=True)
        lay.addWidget(title)
        sub = _mk_label(APP_SUBTITLE, 12, 'TEXT_SECONDARY', bold=False)
        lay.addWidget(sub)

        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(AGREEMENT_TEXT)
        box.setStyleSheet(
            'QPlainTextEdit { background: ' + C['SURFACE'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 10px; padding: 12px; font-size: 12px; color: ' + C['TEXT_PRIMARY'] + '; }')
        lay.addWidget(box, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_no = QPushButton('退出')
        btn_no.setStyleSheet(_btn_style('ghost'))
        btn_no.setFixedSize(110, 40)
        btn_no.clicked.connect(self.reject)
        row.addWidget(btn_no)
        btn_ok = QPushButton('同意并继续')
        btn_ok.setStyleSheet(_btn_style('primary'))
        btn_ok.setFixedSize(140, 40)
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_ok)
        lay.addLayout(row)


# ------------------------------------------------------------------ 侧边导航

# ------------------------------------------------------------------ 动效组件

class DropShadowAni(QPropertyAnimation):
    """卡片悬浮阴影渐入渐出（抄 Fluent DropShadowAnimation 思路）。"""

    def __init__(self, parent, hover_color, normal_color=None, blur=38, dy=5):
        super().__init__(parent)
        self._normal = normal_color or QColor(0, 0, 0, 0)
        self._hover = hover_color
        self._blur = blur
        self._offset = QPoint(0, dy)
        self._effect = None
        parent.installEventFilter(self)

    def _make_effect(self):
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        self._effect = QGraphicsDropShadowEffect(self)
        self._effect.setOffset(self._offset)
        self._effect.setBlurRadius(self._blur)
        self._effect.setColor(self._normal)
        self.setTargetObject(self._effect)
        self.setStartValue(self._effect.color())
        self.setPropertyName(b'color')
        self.setDuration(150)
        self.setEasingCurve(QEasingCurve.OutQuad)
        return self._effect

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.parent() and self.parent().isEnabled():
            if e.type() == QEvent.Type.Enter:
                if self.state() != QPropertyAnimation.State.Running:
                    self.parent().setGraphicsEffect(self._make_effect())
                self.stop()
                self.setStartValue(self._effect.color())
                self.setEndValue(self._hover)
                self.start()
            elif e.type() == QEvent.Type.Leave:
                if self.parent().graphicsEffect():
                    self.stop()
                    self.setStartValue(self._effect.color())
                    self.setEndValue(self._normal)
                    self.start()
        return super().eventFilter(obj, e)


class FadeStack(QStackedWidget):
    """带淡入过渡的页面栈。

    注意：windowOpacity 只对顶层窗口生效，对子控件做这个动画等于没做
    （旧实现正是如此，所谓「淡入」从未生效）。这里改用 QGraphicsOpacityEffect。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ani = QPropertyAnimation(self, b'opacity')
        self._ani.setDuration(140)
        self._ani.setEasingCurve(QEasingCurve.OutQuad)
        self._ani.finished.connect(self._drop_effect)
        self._effect = None

    def _drop_effect(self):
        """动画结束就把 effect 摘掉：留着会让整个页面持续走离屏渲染。"""
        self._effect = None
        w = self.currentWidget()
        if w is not None:
            w.setGraphicsEffect(None)

    def setCurrentIndex(self, index):
        old = self.currentIndex()
        super().setCurrentIndex(index)
        if old == index:
            return
        w = self.currentWidget()
        if w is None:
            return
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self._ani.stop()
        eff = QGraphicsOpacityEffect(w)
        eff.setOpacity(1.0)
        w.setGraphicsEffect(eff)
        self._effect = eff
        self._ani.setTargetObject(eff)
        self._ani.setStartValue(0.30)
        self._ani.setEndValue(1.0)
        self._ani.start()


class ToggleSwitch(QAbstractButton):
    """iOS 式滑块开关（自绘）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0                      # 滑块位置 0~1
        self._ani = QPropertyAnimation(self, b'slidePos')
        self._ani.setDuration(150)
        self._ani.setEasingCurve(QEasingCurve.OutQuad)
        self.setFixedSize(44, 24)

    def getSlidePos(self):
        return self._pos

    def setSlidePos(self, v):
        self._pos = v
        self.update()

    slidePos = Property(float, getSlidePos, setSlidePos)

    def _start_ani(self, target):
        self._ani.stop()
        self._ani.setStartValue(self._pos)
        self._ani.setEndValue(target)
        self._ani.start()

    def nextCheckState(self):
        self.setChecked(not self.isChecked())
        self._start_ani(1.0 if self.isChecked() else 0.0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.height() / 2
        # 轨道
        if self.isChecked():
            track = QColor(C['ACCENT'])
        else:
            track = QColor(C['BORDER'])
        if not self.isEnabled():
            track = QColor(C['BORDER_LIGHT'])
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, self.width(), self.height(), r, r)
        # 滑块
        margin = 3
        d = self.height() - margin * 2
        x = margin + self._pos * (self.width() - d - margin * 2)
        p.setBrush(QColor('#FFFFFF'))
        p.drawEllipse(QRect(int(x), margin, d, d))
        p.end()

    def sizeHint(self):
        return self.size()


class NavButton(QPushButton):
    def __init__(self, icon_kind, text, parent=None):
        super().__init__(parent)
        self._kind = icon_kind
        self._text = text
        self._selected = False
        self.setFixedSize(60, 52)
        self.setCursor(Qt.PointingHandCursor)
        self._refresh()

    def set_selected(self, sel):
        self._selected = sel
        self._refresh()

    def _refresh(self):
        icon = _glyph_icon(self._kind, C['ACCENT_GLOW'] if self._selected else C['TEXT_MUTED'])
        self.setIcon(icon)
        self.setIconSize(icon.size())
        bg = C['CARD_BG_HOVER'] if self._selected else 'transparent'
        self.setStyleSheet(
            'NavButton { background: ' + bg + '; border: none; border-radius: 10px; }'
            'NavButton:hover { background: ' + C['CARD_BG_HOVER'] + '; }')


class SideBar(QFrame):
    switched = None  # 外部赋值 callable(int)

    IND_W = 3          # 指示条宽
    IND_H = 26         # 指示条高

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(72)
        sb_bg = C['SIDEBAR_BG']
        self.setStyleSheet(
            'SideBar { background: ' + sb_bg + '; border-right: 1px solid '
            + C['BORDER_LIGHT'] + '; }')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 14, 6, 12)
        lay.setSpacing(6)

        logo = QLabel()
        logo.setPixmap(_make_icon_pm(40, C['ICON_C1'], C['ICON_C2'], '学', 16))
        logo.setFixedSize(40, 40)
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo, 0, Qt.AlignHCenter)
        lay.addSpacing(10)

        self._btns = []
        items = [('home', '首页'), ('grid', '模板'), ('plug', '技能'),
                 ('term', '日志'), ('clock', '历史'), ('gear', '设置')]
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for i, (kind, text) in enumerate(items):
            b = NavButton(kind, text)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, idx=i: self.select(idx))
            self._group.addButton(b, i)
            lay.addWidget(b, 0, Qt.AlignHCenter)
            self._btns.append(b)
        self._btns[0].setChecked(True)
        self._btns[0].set_selected(True)

        lay.addStretch(1)
        ver = QLabel(APP_VERSION.replace('V', 'v'))
        ver.setAlignment(Qt.AlignCenter)
        _set_px_font(ver, 10)
        ver.setStyleSheet('color: ' + C['TEXT_MUTED'] + ';')
        lay.addWidget(ver)

        # 滑动指示条（Fluent NavigationIndicator 同款）
        self._indicator = QFrame(self)
        self._indicator.setStyleSheet(
            'background: ' + C['ACCENT'] + '; border-radius: 2px; border: none;')
        self._indicator.setFixedWidth(self.IND_W)
        self._ani = QPropertyAnimation(self._indicator, b'pos')
        self._ani.setDuration(160)
        self._ani.setEasingCurve(QEasingCurve.OutQuad)
        self.select(0, animate=False)

    def _target_pos(self, idx):
        b = self._btns[idx]
        return QPoint(1, b.y() + (b.height() - self.IND_H) // 2)

    def _on_switch(self, idx):
        for i, b in enumerate(self._btns):
            b.set_selected(i == idx)
        if self.switched:
            self.switched(idx)

    def select(self, idx, animate=True, emit=True):
        """选中指定导航项。
        emit=False 表示由外部（switch_page）发起，不再回调 switched，避免回环。"""
        self._btns[idx].setChecked(True)
        for i, b in enumerate(self._btns):
            b.set_selected(i == idx)
        if animate and self.isVisible():
            self._ani.stop()
            self._ani.setStartValue(self._indicator.pos())
            self._ani.setEndValue(self._target_pos(idx))
            self._ani.start()
        else:
            self._indicator.move(self._target_pos(idx))
            self._indicator.show()
        if emit and self.switched:
            self.switched(idx)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        idx = self._btns.index(next(b for b in self._btns if b.isChecked()))
        self._indicator.move(self._target_pos(idx))


# ------------------------------------------------------------------ 通用组件

class PageHeader(QFrame):
    """页头：大标题 + 副标题 + 右侧动作区（self.actions）。"""
    def __init__(self, title, subtitle, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = _mk_label(title, 19, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        s = _mk_label(subtitle, 12, 'TEXT_SECONDARY', bold=False)
        col.addWidget(s)
        lay.addLayout(col)
        lay.addStretch(1)
        self.actions = lay


class StatChip(QFrame):
    """状态胶囊：圆点 + 文本。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            'StatChip { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; }')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(7)
        self._dot = _mk_label('●', 10, 'TEXT_MUTED', bold=False)
        lay.addWidget(self._dot)
        self._text = _mk_label('未检测', 12, 'TEXT_SECONDARY', bold=False)
        lay.addWidget(self._text)

    def set_state(self, text, color):
        self._dot.setStyleSheet('color: ' + color + ';')
        self._text.setText(text)
        self._text.setStyleSheet('color: ' + C['TEXT_PRIMARY'] + ';')


class TargetCard(QFrame):
    """首页大卡：渐变图标 + 名称 + 状态 + 模板名 + 路径 + 操作按钮组。"""
    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target
        self.setStyleSheet(
            'TargetCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 14px; }'
            'TargetCard:hover { border: 1px solid ' + C['ACCENT'] + '; }')
        # 悬浮阴影动画（深色蓝晕，浅色黑影）
        if THEME_NAME == 'dark':
            shadow = QColor(77, 141, 255, 60)
        else:
            shadow = QColor(30, 60, 120, 55)
        self._shadow_ani = DropShadowAni(self, shadow, blur=42, dy=6)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        if target['key'] == 'dsh':
            pm = _make_icon_pm(44, C['ICON_C1'], C['ICON_C2'], 'D', 18)
        else:
            pm = _make_icon_pm(44, C['ICON_C1'], C['ICON_C2_ALT'], 'P', 18)
        icon = QLabel()
        icon.setPixmap(pm)
        head.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = _mk_label(target['card'], 16, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        self._st = _mk_label('未检测', 11, 'TEXT_MUTED', bold=False)
        col.addWidget(self._st)
        head.addLayout(col)
        head.addStretch(1)
        self._chip = StatChip()
        head.addWidget(self._chip)
        lay.addLayout(head)

        self._tpl = _mk_label('模板：—', 12, 'TEXT_SECONDARY', bold=False)
        lay.addWidget(self._tpl)

        # 附加技能包就位状态（按目标端 skills/ 里是否真有目录判定，不只看清单）
        self._addon_lbl = QLabel('')
        self._addon_lbl.setTextFormat(Qt.RichText)
        _set_px_font(self._addon_lbl, 11)
        self._addon_lbl.setStyleSheet('background: transparent; border: none;')
        lay.addWidget(self._addon_lbl)

        self._path = QLabel(resolve_agent_dir(target))
        _set_px_font(self._path, 11)
        self._path.setStyleSheet(
            'color: ' + C['TEXT_MUTED'] + '; background: ' + C['SURFACE'] + ';'
            ' border-radius: 6px; padding: 5px 9px;')
        lay.addWidget(self._path)

        desc = QLabel(target['desc'].replace('{skills}', str(bundle_skill_count())))
        desc.setWordWrap(True)
        _set_px_font(desc, 11)
        desc.setStyleSheet('color: ' + C['TEXT_MUTED'] + ';')
        lay.addWidget(desc, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_go = QPushButton('去部署')
        self.btn_go.setStyleSheet(_btn_style('primary'))
        self.btn_go.setFixedHeight(36)
        self.btn_go.setCursor(Qt.PointingHandCursor)
        btns.addWidget(self.btn_go, 2)
        self.btn_restart = QPushButton('重启')
        self.btn_restart.setStyleSheet(_btn_style('ghost'))
        self.btn_restart.setFixedHeight(36)
        btns.addWidget(self.btn_restart, 1)
        self.btn_uninstall = QPushButton('卸载')
        self.btn_uninstall.setStyleSheet(_btn_style('danger'))
        self.btn_uninstall.setFixedHeight(36)
        btns.addWidget(self.btn_uninstall, 1)
        # 通道体检：确认部署到底生效了没有（会真跑一次模型调用，所以单列一个按钮）
        self.btn_probe = QPushButton('体检')
        self.btn_probe.setStyleSheet(_btn_style('ghost'))
        self.btn_probe.setFixedHeight(36)
        btns.addWidget(self.btn_probe, 1)
        # 版本历史：每次部署留一条可恢复版本（退回那次写入之前的指令文件）
        self.btn_versions = QPushButton('版本')
        self.btn_versions.setStyleSheet(_btn_style('ghost'))
        self.btn_versions.setFixedHeight(36)
        btns.addWidget(self.btn_versions, 1)
        lay.addLayout(btns)
        self._refresh_addons()

    def _refresh_addons(self):
        """刷新附加包状态行：✓ = 目标端 skills/ 里确有该包，— = 未部署。"""
        base = os.path.join(resolve_agent_dir(self.target), 'skills')
        parts = []
        for a in ADDONS:
            ok = os.path.exists(os.path.join(base, a['skill_name'], 'SKILL.md'))
            color = C['SUCCESS'] if ok else C['TEXT_MUTED']
            parts.append('<span style="color:' + color + ';">' + a['name']
                         + (' ✓' if ok else ' —') + '</span>')
        self._addon_lbl.setText('<span style="color:' + C['TEXT_SECONDARY'] + ';">附加包：</span>'
                                + '　'.join(parts))

    def update_state(self, installed, version_label, skill_mode=None):
        self._refresh_addons()
        mode_txt = ''
        if installed and skill_mode in ('full', 'menu'):
            mode_txt = ' · ' + ('极简模式' if skill_mode == 'menu' else '完整模式')
        if installed:
            self._chip.set_state('已注入', C['SUCCESS'])
            self._st.setText('已部署 · ' + (version_label or '') + mode_txt)
            _set_px_font(self._st, 11)
            self._st.setStyleSheet('color: ' + C['SUCCESS'] + ';')
            self._tpl.setText('当前模板：' + (version_label or '—'))
            self.btn_go.setText('重新注入')
        else:
            self._chip.set_state('未注入', C['TEXT_MUTED'])
            self._st.setText('尚未部署')
            _set_px_font(self._st, 11)
            self._st.setStyleSheet('color: ' + C['TEXT_MUTED'] + ';')
            self._tpl.setText('模板：—')
            self.btn_go.setText('去部署')


class QuickCard(QPushButton):
    """首页底部快捷入口卡。"""
    def __init__(self, icon_kind, title, sub, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(72)
        self.setStyleSheet(
            'QuickCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; }'
            'QuickCard:hover { border-color: ' + C['ACCENT'] + '; background: ' + C['CARD_BG_HOVER'] + '; }')
        shadow = QColor(77, 141, 255, 55) if THEME_NAME == 'dark' else QColor(30, 60, 120, 50)
        self._shadow_ani = DropShadowAni(self, shadow, blur=32, dy=4)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(_glyph_icon(icon_kind, C['ACCENT']))
        ic.setFixedSize(26, 26)
        ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = _mk_label(title, 13, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        s = _mk_label(sub, 11, 'TEXT_MUTED', bold=False)
        col.addWidget(s)
        lay.addLayout(col)
        lay.addStretch(1)


def _human(n):
    if n < 1024:
        return str(int(n)) + ' B'
    if n < 1024 * 1024:
        return '%.1f KB' % (n / 1024.0)
    return '%.1f MB' % (n / 1048576.0)
# ------------------------------------------------------------------ 页面基类

class Page(QFrame):
    """页面基类：统一内边距与背景。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_bg_solid = True
        self.setStyleSheet('Page { background: ' + C['BG'] + '; }')

    def on_enter(self):
        """切到该页时刷新（子类覆写）。"""


# ------------------------------------------------------------------ 首页

class HomePage(Page):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        t = _mk_label('工作台', 21, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        s = _mk_label(APP_SUBTITLE, 12, 'TEXT_SECONDARY', bold=False)
        col.addWidget(s)
        hdr.addLayout(col)
        hdr.addStretch(1)
        self._chip_all = StatChip()
        hdr.addWidget(self._chip_all)
        b_check = QPushButton('自检两端')
        b_check.setStyleSheet(_btn_style('ghost'))
        b_check.setFixedHeight(34)
        b_check.setCursor(Qt.PointingHandCursor)
        b_check.clicked.connect(self.main._check_all)
        hdr.addWidget(b_check)
        b_probe = QPushButton('体检两端')
        b_probe.setStyleSheet(_btn_style('ghost'))
        b_probe.setFixedHeight(34)
        b_probe.setCursor(Qt.PointingHandCursor)
        b_probe.clicked.connect(self.main._probe_all)
        hdr.addWidget(b_probe)
        b_task = QPushButton('任务构建')
        b_task.setStyleSheet(_btn_style('ghost'))
        b_task.setFixedHeight(34)
        b_task.setCursor(Qt.PointingHandCursor)
        b_task.clicked.connect(self.main._compose)
        hdr.addWidget(b_task)
        lay.addLayout(hdr)

        # 两张目标卡
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.cards = {}
        for target in TARGETS:
            card = TargetCard(target)
            card.btn_go.clicked.connect(lambda _=False, tg=target: self.main.go_deploy(tg))
            card.btn_restart.clicked.connect(lambda _=False, tg=target: self.main._restart(tg))
            card.btn_uninstall.clicked.connect(lambda _=False, tg=target: self.main._uninstall(tg))
            card.btn_probe.clicked.connect(lambda _=False, tg=target: self.main._probe(tg))
            card.btn_versions.clicked.connect(lambda _=False, tg=target: self.main._versions(tg))
            cards.addWidget(card, 1)
            self.cards[target['key']] = card
        lay.addLayout(cards, 1)

        # 快捷入口
        quick = QHBoxLayout()
        quick.setSpacing(16)
        q1 = QuickCard('grid', '模板库', str(len(VERSIONS)) + ' 个模板 · 按目标模型分组')
        q1.clicked.connect(lambda: self.main.switch_page(1))
        q2 = QuickCard('plug', '技能库', '查看 / 禁用 / 启用')
        q2.clicked.connect(lambda: self.main.switch_page(2))
        q3 = QuickCard('term', '运行日志', '注入与自检输出')
        q3.clicked.connect(lambda: self.main.switch_page(3))
        q4 = QuickCard('task', '任务构建', '一句话 → 任务契约')
        q4.clicked.connect(lambda: self.main._compose())
        for q in (q1, q2, q3, q4):
            quick.addWidget(q, 1)
        lay.addLayout(quick)

        # 教程入口
        foot = QHBoxLayout()
        tip = _mk_label('第一次使用？看一眼 3 步上手：选模板 → 注入 → 重启客户端。', 12, 'TEXT_MUTED', bold=False)
        foot.addWidget(tip)
        foot.addStretch(1)
        b_tut = QPushButton('查看教程')
        b_tut.setStyleSheet(_btn_style('link'))
        b_tut.setCursor(Qt.PointingHandCursor)
        b_tut.clicked.connect(lambda: TutorialDialog(self).exec())
        foot.addWidget(b_tut)
        lay.addLayout(foot)

    def on_enter(self):
        self.main._refresh_status()


# ------------------------------------------------------------------ 模板页

class TemplatePage(Page):
    """部署流程：① 选客户端 → ② 选模式 → ③ 选模板（卡片上单一「部署」按钮）。"""
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        # 附加包勾选状态存这里（key -> bool）。
        # 不能直接读控件：切分组时卡片会被 deleteLater，_addon_checks 里留下的
        # 是已销毁的 C++ 对象，isChecked() 会抛 RuntimeError。
        self._addon_picked = {}
        self._addon_checks = {}
        # 部署设置：客户端 + 技能呈现模式（记在配置里，下次打开沿用）
        cfg = read_app_config()
        self._sel_target = cfg.get('deployTarget') if cfg.get('deployTarget') in ('pideck', 'dsh') else 'pideck'
        self._sel_mode = cfg.get('deployMode') if cfg.get('deployMode') in ('full', 'menu') else 'full'
        self._cur_group = 0          # 当前分组下标；-1 表示在附加模板页
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        hdr = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        t = _mk_label('模板库', 21, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        s = _mk_label('按目标模型分组 · 先选客户端与模式，再点卡片上的部署（含 '
                      + str(bundle_skill_count()) + ' 个模块技能库）', 12, 'TEXT_SECONDARY', bold=False)
        col.addWidget(s)
        hdr.addLayout(col)
        hdr.addStretch(1)
        b_reinject = QPushButton('重新注入全部（按上次模板）')
        b_reinject.setStyleSheet(_btn_style('ghost'))
        b_reinject.setFixedHeight(34)
        b_reinject.setCursor(Qt.PointingHandCursor)
        b_reinject.clicked.connect(self.main._reinject)
        hdr.addWidget(b_reinject)
        outer.addLayout(hdr)

        outer.addWidget(self._build_setup_bar())

        body = QHBoxLayout()
        body.setSpacing(16)

        # 左分组列
        self._group_col = QVBoxLayout()
        self._group_col.setSpacing(8)
        gb = QFrame()
        gb.setFixedWidth(190)
        gb.setStyleSheet('background: transparent; border: none;')
        gb.setLayout(self._group_col)
        body.addWidget(gb, 0, Qt.AlignTop)

        self._group_btns = []
        for gi, grp in enumerate(MODEL_GROUPS):
            b = QPushButton(grp['label'])
            _set_px_font(b, 13, bold=True)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(38)
            b.setStyleSheet(
                'QPushButton { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_SECONDARY'] + ';'
                ' border: 1px solid ' + C['BORDER'] + '; border-radius: 9px; text-align: left;'
                ' padding-left: 14px; font-weight: 600; }'
                'QPushButton:hover { border-color: ' + C['ACCENT'] + '; color: ' + C['TEXT_PRIMARY'] + '; }'
                'QPushButton:checked { background: ' + C['ACCENT_LIGHT'] + '; color: ' + C['ACCENT_GLOW']
                + '; border-color: ' + C['ACCENT'] + '; }')
            b.clicked.connect(lambda _=False, idx=gi: self.select_group(idx))
            self._group_btns.append(b)
            self._group_col.addWidget(b)

        # 附加模板入口（独立分区）
        self._addon_btn = QPushButton('🧩 附加模板')
        self._addon_btn.setCheckable(True)
        self._addon_btn.setCursor(Qt.PointingHandCursor)
        self._addon_btn.setFixedHeight(38)
        self._addon_btn.setStyleSheet(
            'QPushButton { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_SECONDARY'] + ';'
            ' border: 1px dashed ' + C['ACCENT'] + '; border-radius: 9px; text-align: left;'
            ' padding-left: 14px; font-weight: 600; }'
            'QPushButton:hover { border-color: ' + C['ACCENT'] + '; color: ' + C['TEXT_PRIMARY'] + '; }'
            'QPushButton:checked { background: ' + C['ACCENT_LIGHT'] + '; color: ' + C['ACCENT_GLOW']
            + '; border-style: solid; border-color: ' + C['ACCENT'] + '; }')
        self._addon_btn.clicked.connect(self.select_addons)
        self._group_col.addSpacing(6)
        self._group_col.addWidget(self._addon_btn)
        self._group_btns[0].setChecked(True)

        # 右卡片区（滚动）
        self._cards_area = QVBoxLayout()
        self._cards_area.setSpacing(12)
        holder = QFrame()
        holder.setStyleSheet('background: transparent; border: none;')
        holder.setLayout(self._cards_area)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        body.addWidget(scroll, 1)

        outer.addLayout(body, 1)

        self._note = QLabel('')
        self._note.setWordWrap(True)
        _set_px_font(self._note, 11)
        self._note.setStyleSheet(
            'color: ' + C['TEXT_MUTED'] + '; background: ' + C['SURFACE'] + ';'
            ' border: 1px solid ' + C['BORDER_LIGHT'] + '; border-radius: 8px; padding: 8px 12px;')
        outer.addWidget(self._note)

        self._grid_frames = []
        self.select_group(0)

    # ---------------------------------------------------------- 部署设置（客户端 / 模式）

    def _seg_btn(self, text, checked=False):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setChecked(checked)
        _set_px_font(b, 12, bold=True)
        b.setFixedHeight(30)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            'QPushButton { background: transparent; color: ' + C['TEXT_SECONDARY'] + ';'
            ' border: 1px solid ' + C['BORDER'] + '; border-radius: 7px; padding: 0 14px; }'
            'QPushButton:hover { border-color: ' + C['ACCENT'] + '; color: ' + C['TEXT_PRIMARY'] + '; }'
            'QPushButton:checked { background: ' + C['ACCENT'] + '; color: #FFFFFF;'
            ' border-color: ' + C['ACCENT'] + '; }')
        return b

    def _build_setup_bar(self):
        """① 客户端 + ② 技能呈现模式：两排分段选择，带一行效果说明。"""
        bar = QFrame()
        bar.setObjectName('SettingCard')
        bar.setStyleSheet(
            'QFrame#SettingCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER']
            + '; border-radius: 12px; }')
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(9)

        # ① 客户端
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        lb1 = _mk_label('① 客户端', 12, 'TEXT_SECONDARY', bold=True)
        lb1.setFixedWidth(72)
        r1.addWidget(lb1)
        self._tgt_btns = {}
        self._tgt_group = QButtonGroup(self)
        self._tgt_group.setExclusive(True)
        for tg in TARGETS:
            b = self._seg_btn(tg['card'], tg['key'] == self._sel_target)
            b.clicked.connect(lambda _=False, k=tg['key']: self._set_target(k))
            self._tgt_group.addButton(b)
            self._tgt_btns[tg['key']] = b
            r1.addWidget(b)
        r1.addStretch(1)
        self._tgt_path = _mk_label('', 11, 'TEXT_MUTED', bold=False)
        r1.addWidget(self._tgt_path)
        lay.addLayout(r1)

        # ② 模式
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        lb2 = _mk_label('② 模式', 12, 'TEXT_SECONDARY', bold=True)
        lb2.setFixedWidth(72)
        r2.addWidget(lb2)
        self._mode_btns = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for key, label in (('full', '完整模式'), ('menu', '极简模式')):
            b = self._seg_btn(label, key == self._sel_mode)
            b.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            self._mode_group.addButton(b)
            self._mode_btns[key] = b
            r2.addWidget(b)
        r2.addStretch(1)
        self._mode_hint = _mk_label('', 11, 'TEXT_MUTED', bold=False)
        r2.addWidget(self._mode_hint)
        lay.addLayout(r2)

        self._refresh_setup()
        return bar

    def _refresh_setup(self):
        tg = next(t for t in TARGETS if t['key'] == self._sel_target)
        self._tgt_path.setText('配置根 ' + resolve_agent_dir(tg))
        if self._sel_mode == 'menu':
            self._mode_hint.setText('只 1 个菜单技能进系统提示词（每轮 ≈90 tokens），'
                                    + str(bundled_skill_count()) + ' 个模块按需读取')
        else:
            self._mode_hint.setText(str(bundled_skill_count()) + ' 个模块全进系统提示词'
                                    '（每轮 ≈7,000 tokens），AI 按描述自选')

    def _set_target(self, key):
        self._sel_target = key
        write_app_config('deployTarget', key)
        for k, b in self._tgt_btns.items():
            b.setChecked(k == key)      # 程序化调用也要同步分段按钮
        self._refresh_setup()
        self._rerender()

    def _set_mode(self, key):
        self._sel_mode = key
        write_app_config('deployMode', key)
        for k, b in self._mode_btns.items():
            b.setChecked(k == key)
        self._refresh_setup()
        self._rerender()

    def _rerender(self):
        """换客户端/模式后重建当前视图（卡片上的按钮文字与说明跟着变）"""
        if self._cur_group < 0:
            self.select_addons()
        else:
            self.select_group(self._cur_group)

    def _target_card(self):
        return next(t for t in TARGETS if t['key'] == self._sel_target)['card']

    def _mode_label(self):
        return '极简模式' if self._sel_mode == 'menu' else '完整模式'

    def _target_obj(self):
        return next(t for t in TARGETS if t['key'] == self._sel_target)

    def _deploy_template(self, key, name, no_skills):
        mode = self._sel_mode
        if no_skills:
            mode = 'full'        # 精简模板不含技能库，模式不生效
        self.main._run_install(self._target_obj(), key, name, no_skills,
                               addon_keys=self.main._picked_addons(), skill_mode=mode)

    def select_group(self, gi):
        self._cur_group = gi
        for i, b in enumerate(self._group_btns):
            b.setChecked(i == gi)
        self._addon_btn.setChecked(False)
        grp = MODEL_GROUPS[gi]
        self._note.setText('『' + grp['label'] + '』 ' + grp['note'])
        # 清空旧卡
        while self._cards_area.count():
            it = self._cards_area.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        if not grp['versions']:
            empty = _mk_label('这个分类暂时是空的。', 12, 'TEXT_MUTED', bold=False)
            self._cards_area.addWidget(empty)
            self._cards_area.addStretch(1)
            return
        for key, name, desc, no_skills, rec in grp['versions']:
            card = self._make_tpl_card(key, name, desc, no_skills, rec)
            self._cards_area.addWidget(card)
        self._cards_area.addStretch(1)

    def select_addons(self):
        self._cur_group = -1
        self._addon_btn.setChecked(True)
        for b in self._group_btns:
            b.setChecked(False)
        self._note.setText('『附加模板』独立技能包：可与指令集模板一起部署，也可单独部署（需先注入过任一模板）。')
        while self._cards_area.count():
            it = self._cards_area.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._addon_checks = {}

        # --- 附加包卡片（带勾选态高亮）---
        for a in ADDONS:
            card = self._make_addon_card(a)
            self._cards_area.addWidget(card)

        # --- 部署操作卡：按端分组，一行解决部署/移除 ---
        ops = QFrame()
        ops.setObjectName('SettingCard')
        ops.setStyleSheet(
            'QFrame#SettingCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER']
            + '; border-radius: 12px; }')
        ov = QVBoxLayout(ops)
        ov.setContentsMargins(18, 14, 18, 14)
        ov.setSpacing(10)

        head = QHBoxLayout()
        ht = _mk_label('部署', 13, 'TEXT_PRIMARY', bold=True)
        head.addWidget(ht)
        hint = _mk_label('勾选上方技能包后，按上面选定的客户端操作', 11, 'TEXT_MUTED', bold=False)
        head.addWidget(hint)
        head.addStretch(1)
        ov.addLayout(head)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet('background: ' + C['BORDER_LIGHT'] + '; border: none;')
        ov.addWidget(line)

        row = QHBoxLayout()
        row.setSpacing(10)
        dest = _mk_label('→ ' + self._target_card() + ' · ' + self._mode_label(),
                         12, 'TEXT_PRIMARY', bold=True)
        dest.setFixedWidth(240)
        row.addWidget(dest)
        b_dep = QPushButton('部署勾选包')
        b_dep.setStyleSheet(_btn_style('primary'))
        b_dep.setFixedHeight(32)
        b_dep.setFixedWidth(120)
        b_dep.setCursor(Qt.PointingHandCursor)
        b_dep.clicked.connect(self._deploy_addons)
        row.addWidget(b_dep)
        b_rm = QPushButton('移除勾选包')
        b_rm.setStyleSheet(_btn_style('ghost'))
        b_rm.setFixedHeight(32)
        b_rm.setCursor(Qt.PointingHandCursor)
        b_rm.clicked.connect(self._remove_addons)
        row.addWidget(b_rm)
        hint2 = _mk_label('部署 = 只装技能（沿用已部署的模式）；移除 = 只删这些包', 11, 'TEXT_MUTED', bold=False)
        row.addWidget(hint2)
        row.addStretch(1)
        ov.addLayout(row)

        self._cards_area.addWidget(ops)

        tip = QLabel('提示：与指令集模板一起打 → 勾选后回左侧选模型分组，点卡片上的部署按钮（同样遵循上面选的客户端与模式）。')
        tip.setWordWrap(True)
        _set_px_font(tip, 11)
        tip.setStyleSheet('color: ' + C['TEXT_MUTED'] + '; padding: 2px 2px;')
        self._cards_area.addWidget(tip)
        self._cards_area.addStretch(1)

    def _addon_selected(self):
        """当前勾选的附加包 key 列表（读状态字典，不碰可能已销毁的控件）。"""
        return [a['key'] for a in ADDONS if self._addon_picked.get(a['key'])]

    def _addon_card_style(self, card, on):
        card.setStyleSheet(
            'QFrame#TplCard { background: ' + C['ACCENT_LIGHT'] + '; border: 1px solid ' + C['ACCENT'] + ';'
            ' border-radius: 12px; }' if on else
            'QFrame#TplCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; }'
            'QFrame#TplCard:hover { border-color: ' + C['ACCENT'] + '; }')

    def _on_addon_toggle(self, key, on, card):
        self._addon_picked[key] = bool(on)
        self._addon_card_style(card, on)

    def _remove_addons(self):
        picked = self._addon_selected()
        if not picked:
            QMessageBox.information(self, APP_NAME, '先勾选要移除的附加技能包。')
            return
        dirs = ';'.join(a['skill_name'] for a in ADDONS if a['key'] in picked)
        names = ' + '.join(a['name'] for a in ADDONS if a['key'] in picked)
        ans = QMessageBox.question(
            self, APP_NAME,
            '从 ' + self._target_card() + ' 移除：' + names + '？\n\n只移除这些技能包，不影响指令集与其它技能。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        self.main._remove_addons(self._target_obj(), dirs, names)

    def _deploy_addons(self):
        picked = self._addon_selected()
        if not picked:
            QMessageBox.information(self, APP_NAME, '先勾选至少一个附加技能包。')
            return
        dirs = ';'.join(a['skill_dir'] for a in ADDONS if a['key'] in picked)
        names = ' + '.join(a['name'] for a in ADDONS if a['key'] in picked)
        self.main._run_skills_only(self._target_obj(), dirs, names, skill_mode=self._sel_mode)

    def _make_addon_card(self, a):
        card = QFrame()
        card.setObjectName('TplCard')
        card.setStyleSheet(
            'QFrame#TplCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; }'
            'QFrame#TplCard:hover { border-color: ' + C['ACCENT'] + '; }')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(_make_icon_pm(34, C['ICON_C1'], C['ICON_C2_ALT'], '附', 13))
        top.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        nm = _mk_label(a['name'], 14, 'TEXT_PRIMARY', bold=True)
        col.addWidget(nm)
        sub = _mk_label('附加技能包 · 独立部署', 10, 'TEXT_MUTED', bold=False)
        col.addWidget(sub)
        top.addLayout(col)
        top.addStretch(1)
        chk = QCheckBox('附加')
        chk.setStyleSheet(
            'QCheckBox { font-size: 12px; color: ' + C['TEXT_SECONDARY'] + '; spacing: 6px; }'
            'QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;'
            ' border: 1px solid ' + C['TEXT_MUTED'] + '; background: ' + C['SURFACE'] + '; }'
            'QCheckBox::indicator:checked { background: ' + C['ACCENT'] + '; border-color: ' + C['ACCENT'] + '; }')
        chk.setChecked(bool(self._addon_picked.get(a['key'])))    # 先恢复状态再接信号
        self._addon_card_style(card, chk.isChecked())
        chk.toggled.connect(lambda on, c=card, k=a['key']: self._on_addon_toggle(k, on, c))
        top.addWidget(chk)
        self._addon_checks[a['key']] = chk
        lay.addLayout(top)
        d = QLabel(a['desc'])
        d.setWordWrap(True)
        _set_px_font(d, 12)
        d.setStyleSheet('color: ' + C['TEXT_SECONDARY'] + ';')
        lay.addWidget(d, 1)
        tr = QLabel('触发：' + a['trigger'])
        tr.setWordWrap(True)
        _set_px_font(tr, 11)
        tr.setStyleSheet('color: ' + C['TEXT_MUTED'] + ';')
        lay.addWidget(tr)
        return card

    def _make_tpl_card(self, key, name, desc, no_skills, rec):
        card = QFrame()
        card.setObjectName('TplCard')
        card.setStyleSheet(
            'QFrame#TplCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; }'
            'QFrame#TplCard:hover { border-color: ' + C['ACCENT'] + '; }')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        nm = _mk_label(name, 14, 'TEXT_PRIMARY', bold=True)
        top.addWidget(nm)
        if rec:
            r = QLabel('推荐')
            _set_px_font(r, 10, bold=True)
            r.setStyleSheet(
                'font-weight: 600; color: ' + C['ACCENT_GLOW'] + '; background: '
                + C['ACCENT_LIGHT'] + '; border-radius: 8px; padding: 2px 8px;')
            top.addWidget(r)
        if no_skills:
            ns = QLabel('精简 · 不含技能库')
            _set_px_font(ns, 10)
            ns.setStyleSheet(
                'color: ' + C['WARN'] + '; background: ' + C['WARN_LIGHT']
                + '; border-radius: 8px; padding: 2px 8px;')
            top.addWidget(ns)
        top.addStretch(1)
        lay.addLayout(top)

        d = QLabel(desc)
        d.setWordWrap(True)
        _set_px_font(d, 12)
        d.setStyleSheet('color: ' + C['TEXT_SECONDARY'] + ';')
        lay.addWidget(d, 1)

        if no_skills:
            eff = _mk_label('本模板不含技能库（呈现模式不生效）', 11, 'WARN', bold=False)
            lay.addWidget(eff)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        b = QPushButton('部署到 ' + self._target_card())
        b.setStyleSheet(_btn_style('primary'))
        b.setFixedHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, k=key, l=name, ns=no_skills: self._deploy_template(k, l, ns))
        btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        return card


# ------------------------------------------------------------------ 技能页

class SkillFilterChip(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)
        self.setStyleSheet(
            'QPushButton { background: transparent; color: ' + C['TEXT_SECONDARY'] + ';'
            ' border: 1px solid ' + C['BORDER'] + '; border-radius: 14px; padding: 0 14px;'
            ' }'
            'QPushButton:hover { color: ' + C['TEXT_PRIMARY'] + '; border-color: ' + C['ACCENT'] + '; }'
            'QPushButton:checked { background: ' + C['ACCENT_LIGHT'] + '; color: ' + C['ACCENT_GLOW']
            + '; border-color: ' + C['ACCENT'] + '; }')


class SkillsPage(Page):
    FILTERS = [('all', '全部'), ('on', '启用'), ('off', '已禁用'), ('ours', '本工具部署')]

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self._target_index = 0
        self._filter = 'all'
        self._search = ''
        self._pending_search = ''
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(260)
        self._search_timer.timeout.connect(self._apply_search)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        hdr = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        t = _mk_label('技能库', 21, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        s = _mk_label('Pi 的技能发现是「目录递归扫描 SKILL.md」——「禁用」= 移出扫描路径（skills-disabled），可随时移回。', 12, 'TEXT_SECONDARY', bold=False)
        col.addWidget(s)
        hdr.addLayout(col)
        hdr.addStretch(1)
        self._stat = _mk_label('', 12, 'TEXT_SECONDARY', bold=False)
        hdr.addWidget(self._stat)
        outer.addLayout(hdr)

        # 目标 tab + 搜索
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self._tabs = QButtonGroup(self)
        self._tabs.setExclusive(True)
        for i, tg in enumerate(TARGETS):
            b = QPushButton(tg['card'])
            _set_px_font(b, 12, bold=True)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                'QPushButton { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_SECONDARY'] + ';'
                ' border: 1px solid ' + C['BORDER'] + '; border-radius: 8px; padding: 0 16px;'
                ' font-weight: 600; }'
                'QPushButton:checked { background: ' + C['ACCENT_LIGHT'] + '; color: ' + C['ACCENT_GLOW']
                + '; border-color: ' + C['ACCENT'] + '; }')
            b.clicked.connect(lambda _=False, idx=i: self.switch_target(idx))
            self._tabs.addButton(b, i)
            bar.addWidget(b)
        bar.addStretch(1)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText('搜索技能名…')
        self._search_box.setFixedSize(220, 32)
        self._search_box.setStyleSheet(
            'QLineEdit { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_PRIMARY'] + ';'
            ' border: 1px solid ' + C['BORDER'] + '; border-radius: 8px; padding: 0 10px;'
            ' font-size: 12px; }'
            'QLineEdit:focus { border-color: ' + C['ACCENT'] + '; }')
        self._search_box.textChanged.connect(self._on_search_changed)
        bar.addWidget(self._search_box)
        outer.addLayout(bar)

        # 筛选 chips
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for i, (fid, label) in enumerate(self.FILTERS):
            c = SkillFilterChip(label)
            c.setChecked(i == 0)
            c.clicked.connect(lambda _=False, f=fid: self._set_filter(f))
            self._filter_group.addButton(c, i)
            chips.addWidget(c)
        chips.addStretch(1)
        outer.addLayout(chips)

        # 表格
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(['技能', '来源', '状态', '大小'])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setFixedHeight(34)
        self._table.setStyleSheet(
            'QTableWidget { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; font-size: 12px; color: ' + C['TEXT_PRIMARY'] + '; }'
            'QTableWidget::item { padding: 6px 8px; border-bottom: 1px solid ' + C['BORDER_LIGHT'] + '; }'
            'QTableWidget::item:selected { background: ' + C['ACCENT_LIGHT'] + '; color: ' + C['TEXT_PRIMARY'] + '; }'
            'QHeaderView::section { background: ' + C['SURFACE'] + '; border: none;'
            ' border-bottom: 1px solid ' + C['BORDER'] + '; padding: 6px 8px; font-size: 12px;'
            ' color: ' + C['TEXT_SECONDARY'] + '; }')
        outer.addWidget(self._table, 1)

        # 批量操作条
        foot = QHBoxLayout()
        foot.setSpacing(10)
        b_dis = QPushButton('禁用选中')
        b_dis.setStyleSheet(_btn_style('ghost'))
        b_dis.setFixedHeight(34)
        b_dis.clicked.connect(self._disable_selected)
        foot.addWidget(b_dis)
        b_en = QPushButton('启用选中')
        b_en.setStyleSheet(_btn_style('ghost'))
        b_en.setFixedHeight(34)
        b_en.clicked.connect(self._enable_selected)
        foot.addWidget(b_en)
        foot.addStretch(1)
        b_open = QPushButton('打开目录')
        b_open.setStyleSheet(_btn_style('secondary'))
        b_open.setFixedHeight(34)
        b_open.clicked.connect(self._open_dir)
        foot.addWidget(b_open)
        outer.addLayout(foot)

        self._reload()

    # ---- 数据

    _size_cache = {}          # 目录路径 -> (mtime, 字节数)，避免每次输入都全盘 walk

    def _dir_size(self, p):
        try:
            stamp = os.path.getmtime(p)
        except OSError:
            stamp = 0
        hit = self._size_cache.get(p)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        size = 0
        for root, _d, files in os.walk(p):
            for f in files:
                try:
                    size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        self._size_cache[p] = (stamp, size)
        return size

    def _dirs(self):
        t = TARGETS[self._target_index]
        base = resolve_agent_dir(t)
        return base, os.path.join(base, 'skills'), os.path.join(base, 'skills-disabled')

    def switch_target(self, idx):
        self._target_index = idx
        self._reload()

    def _set_filter(self, f):
        self._filter = f
        self._reload()

    def _on_search_changed(self, text):
        """搜索去抖：旧写法每敲一个字就全量 os.walk 重算体积，输入会卡。"""
        self._pending_search = (text or '').strip().lower()
        self._search_timer.start()

    def _apply_search(self):
        self._search = getattr(self, '_pending_search', '')
        self._reload()

    def _reload(self):
        _base, act, dis = self._dirs()
        st = read_json(state_path(TARGETS[self._target_index]['key']))
        ours = set(st.get('installedSkills') or []) if st else set()
        mode_txt = ''
        if st and st.get('skillMode') in ('full', 'menu'):
            mode_txt = ' · ' + ('极简模式' if st['skillMode'] == 'menu' else '完整模式')
        rows = []
        for prefix, state in ((act, '启用'), (dis, '已禁用')):
            if not os.path.isdir(prefix):
                continue
            for name in sorted(os.listdir(prefix)):
                p = os.path.join(prefix, name)
                if not os.path.isdir(p) or not os.path.exists(os.path.join(p, 'SKILL.md')):
                    continue
                size = self._dir_size(p)
                rows.append((name, '本工具' if name in ours else '客户端自带', state, _human(size)))

        # 过滤
        if self._filter == 'on':
            rows = [r for r in rows if r[2] == '启用']
        elif self._filter == 'off':
            rows = [r for r in rows if r[2] == '已禁用']
        elif self._filter == 'ours':
            rows = [r for r in rows if r[1] == '本工具']
        if self._search:
            rows = [r for r in rows if self._search in r[0].lower()]

        self._table.setRowCount(len(rows))
        for r, (name, src, state, size) in enumerate(rows):
            self._table.setItem(r, 0, QTableWidgetItem(name))
            self._table.setItem(r, 1, QTableWidgetItem(src))
            item = QTableWidgetItem(state)
            item.setForeground(QColor(C['SUCCESS'] if state == '启用' else C['WARN']))
            self._table.setItem(r, 2, item)
            self._table.setItem(r, 3, QTableWidgetItem(size))
        self._stat.setText(TARGETS[self._target_index]['card'] + ' · 共 ' + str(len(rows)) + ' 个技能'
                           + mode_txt)

    def on_enter(self):
        self._reload()

    # ---- 操作

    def _selected_names(self):
        out = []
        for idx in self._table.selectionModel().selectedRows():
            out.append(self._table.item(idx.row(), 0).text())
        return out

    def _move(self, names, src_dir, dst_dir):
        if not names:
            QMessageBox.information(self, APP_NAME, '请先选中要操作的技能。')
            return
        os.makedirs(dst_dir, exist_ok=True)
        ok, fail = 0, []
        for n in names:
            s = os.path.join(src_dir, n)
            d = os.path.join(dst_dir, n)
            if not os.path.isdir(s):
                continue
            try:
                if os.path.isdir(d):
                    fail.append(n)
                    continue
                os.rename(s, d)
                ok += 1
            except Exception:
                fail.append(n)
        self._reload()
        msg = '已处理 ' + str(ok) + ' 个'
        if fail:
            msg += '；失败 ' + str(len(fail)) + ' 个：' + '、'.join(fail)
        QMessageBox.information(self, APP_NAME, msg)

    def _disable_selected(self):
        _b, act, dis = self._dirs()
        self._move(self._selected_names(), act, dis)

    def _enable_selected(self):
        _b, act, dis = self._dirs()
        self._move(self._selected_names(), dis, act)

    def _open_dir(self):
        base, _a, _d = self._dirs()
        os.makedirs(base, exist_ok=True)
        open_in_explorer(base)


# ------------------------------------------------------------------ 日志页

class LogPage(Page):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        hdr = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        t = _mk_label('运行日志', 21, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        s = _mk_label('注入 / 自检 / 卸载的 PowerShell 输出。日志同时落盘到 %LOCALAPPDATA%\\pi-workbench\\logs', 12, 'TEXT_SECONDARY', bold=False)
        col.addWidget(s)
        hdr.addLayout(col)
        hdr.addStretch(1)
        b_copy = QPushButton('复制全部')
        b_copy.setStyleSheet(_btn_style('ghost'))
        b_copy.setFixedHeight(34)
        b_copy.clicked.connect(self._copy_all)
        hdr.addWidget(b_copy)
        b_clear = QPushButton('清空')
        b_clear.setStyleSheet(_btn_style('ghost'))
        b_clear.setFixedHeight(34)
        b_clear.clicked.connect(self.clear)
        hdr.addWidget(b_clear)
        b_ops = QPushButton('操作记录')
        b_ops.setStyleSheet(_btn_style('ghost'))
        b_ops.setFixedHeight(34)
        b_ops.setCursor(Qt.PointingHandCursor)
        b_ops.clicked.connect(self._open_ops)
        hdr.addWidget(b_ops)
        outer.addLayout(hdr)

        self._box = QTextBrowser()
        # 界面侧环形上限：块数超了自动丢最旧的。
        # 长任务（部署 65 个技能 + 日志）不会把界面越；文件日志仍是全量。
        self._box.document().setMaximumBlockCount(5000)
        self._box.setOpenExternalLinks(False)
        self._box.setStyleSheet(
            'QTextBrowser { background: ' + C['LOG_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; padding: 12px; font-size: 12px; color: ' + C['LOG_FG'] + ';'
            ' font-family: Consolas, monospace; }')
        outer.addWidget(self._box, 1)

        self._status = _mk_label('', 12, 'TEXT_MUTED', bold=False)
        outer.addWidget(self._status)

    _COLORS = {
        'ok':    '#6CCB5F', 'FAIL': '#FF6B6B', 'WARN': '#FCE100',
        'ERROR': '#FF6B6B', 'L1':   '#6FA8FF', 'L2':   '#6FA8FF',
        'L3':    '#6FA8FF', 'L4':   '#6FA8FF', 'INFO': '#8AB4F7',
    }

    def append(self, text):
        import html as _html
        from PySide6.QtGui import QTextCursor
        esc = _html.escape(text)
        color = None
        stripped = text.strip()
        # 识别 [INFO]/[L1]/[FAIL] 等前缀
        if stripped.startswith('['):
            head = stripped.split(']', 1)[0].lstrip('[').upper()
            color = self._COLORS.get(head)
        if stripped.startswith('退出码'):
            color = '#6CCB5F' if stripped.endswith(': 0') else '#FF6B6B'
        elif stripped.startswith('> powershell'):
            color = C['TEXT_MUTED']
        elif stripped.startswith('[提示]'):
            color = '#FCE100'
        sb = self._box.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        cur = self._box.textCursor()
        cur.movePosition(QTextCursor.End)
        if color:
            cur.insertHtml('<span style="color:' + color + ';">' + esc + '</span><br>')
        else:
            cur.insertHtml(esc + '<br>')
        if at_bottom:
            sb.setValue(sb.maximum())

    def clear(self):
        self._box.clear()

    def _open_ops(self):
        """打开操作记录（一行一次操作，追加式）。"""
        p = os.path.join(work_root(), 'logs', 'operations.log')
        if not os.path.exists(p):
            self._status.setText('还没有操作记录：' + p)
            return
        try:
            os.startfile(p)      # noqa: S606  Windows 关联程序打开
            self._status.setText('已打开 ' + p)
        except Exception as e:
            self._status.setText('打不开（' + str(e) + '），路径：' + p)

    def _copy_all(self):
        QApplication.clipboard().setText(self._box.toPlainText())
        self._status.setText('已复制到剪贴板')


class VersionsDialog(QDialog):
    """可恢复版本列表。

    每一行 = 一次部署版本（backup\\<目标>\\history\\<id>.json）。
    恢复的语义是「退回那一次写入之前的内容」（指令文件 / 补丁层），不是「跳到那一版」。
    当前文件在那之后被改过时，inject.ps1 会先拦下（exit 3），这里再问一次要不要 -Force。
    """

    def __init__(self, main, target, parent=None):
        super().__init__(parent)
        self.main = main
        self.target = target
        self.setWindowTitle('版本历史 · ' + target['card'])
        self.setMinimumSize(760, 480)
        self.setStyleSheet('QDialog { background: ' + C['BG'] + '; }')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(_mk_label('可恢复版本 · ' + target['card'], 18, 'TEXT_PRIMARY', bold=True))
        lay.addWidget(_mk_label('恢复 = 退回「那一次写入之前」的指令文件 / 补丁层；技能库不动。'
                                '文件在那之后被改过时会先拦下，确认后才覆盖（会另存现场）。',
                                12, 'TEXT_SECONDARY', bold=False))
        self.list = QListWidget()
        self.list.setStyleSheet('QListWidget { background: ' + C['SURFACE'] + '; border: 1px solid '
                                + C['BORDER'] + '; border-radius: 10px; color: ' + C['TEXT_PRIMARY']
                                + '; font-family: Consolas, monospace; font-size: 12px; padding: 6px; }')
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.hint = _mk_label('', 12, 'TEXT_MUTED', bold=False)
        row.addWidget(self.hint)
        row.addStretch(1)
        b_refresh = QPushButton('刷新')
        b_refresh.setStyleSheet(_btn_style('ghost'))
        b_refresh.setFixedHeight(34)
        b_refresh.clicked.connect(self.refresh)
        row.addWidget(b_refresh)
        b_diff = QPushButton('看差异')
        b_diff.setStyleSheet(_btn_style('ghost'))
        b_diff.setFixedHeight(34)
        b_diff.setCursor(Qt.PointingHandCursor)
        b_diff.clicked.connect(self.show_diff)
        row.addWidget(b_diff)
        b_restore = QPushButton('恢复选中版本')
        b_restore.setStyleSheet(_btn_style('primary'))
        b_restore.setFixedHeight(34)
        b_restore.clicked.connect(self._restore_selected)
        row.addWidget(b_restore)
        lay.addLayout(row)
        # 差异面板（默认收起）：恢复前先看清楚会改哪几行
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setMinimumHeight(200)
        self.diff.setStyleSheet('QPlainTextEdit { background: ' + C['LOG_BG'] + '; border: 1px solid '
                                + C['BORDER'] + '; border-radius: 10px; padding: 8px; color: '
                                + C['LOG_FG'] + '; font-family: Consolas, monospace; font-size: 12px; }')
        self.diff.setVisible(False)
        lay.addWidget(self.diff, 2)
        self.reload()

    def _path(self):
        return os.path.join(work_root(), 'history-' + self.target['key'] + '.json')

    def reload(self):
        d = read_json(self._path()) or {}
        self.rows = d.get('versions') or []
        self.list.clear()
        for r in self.rows:
            at = str(r.get('at') or '')
            if len(at) > 19:
                at = at[:19].replace('T', ' ')
            ok = '可恢复' if r.get('restorable') else '不可恢复'
            item = QListWidgetItem('%s  %s  %-12s %-10s 文件%s  %s'
                                   % (at, str(r.get('id') or '')[:12], str(r.get('action') or ''),
                                      ok, r.get('files'), str(r.get('prompt') or '')))
            item.setData(0x0100, r.get('id'))          # Qt.UserRole
            if not r.get('restorable'):
                item.setForeground(QColor(C['TEXT_MUTED']))
            self.list.addItem(item)
        self.hint.setText('共 %d 条 ｜ 目录 %s' % (len(self.rows), os.path.join(work_root(), 'backup',
                                                                          self.target['key'], 'history')))

    def refresh(self):
        def done(code, tail):
            self.reload()
            self.main._set_status('版本列表已刷新（退出码 %d）' % code, 'ok' if code == 0 else 'warn')
        self.main._enqueue(['-Target', self.target['key'], '-ListVersions', '-Json'],
                           '读版本列表 ' + self.target['card'], done,
                           kind='操作', target_card=self.target['card'])

    def show_diff(self):
        """看差异：-Diff 只读输出「当前 → 恢复后」，不写任何目标文件。"""
        it = self.list.currentItem()
        if it is None:
            self.hint.setText('先选一条版本记录')
            return
        vid = str(it.data(0x0100) or '')
        out = os.path.join(work_root(), 'diff-' + self.target['key'] + '.txt')
        try:
            os.remove(out)
        except OSError:
            pass

        def done(code, tail):
            if not os.path.exists(out):
                self.hint.setText('取差异失败（退出码 %d），见日志' % code)
                return
            try:
                with open(out, encoding='utf-8') as fh:
                    text = fh.read()
            except Exception as e:
                self.hint.setText('读不到差异：' + str(e))
                return
            self.diff.setPlainText(text)
            self.diff.setVisible(True)
            summary = [x for x in text.splitlines() if x.startswith('# 合计')]
            self.hint.setText('差异已生成 ｜ ' + (summary[-1] if summary else '') + ' ｜ 仅比对，未写入')

        self.hint.setText('正在比对…')
        self.main._enqueue(['-Target', self.target['key'], '-Diff', vid, '-Out', out],
                           '版本差异 ' + self.target['card'], done,
                           kind='操作', target_card=self.target['card'])

    def _restore_selected(self):
        it = self.list.currentItem()
        if it is None:
            self.hint.setText('先选一条版本记录')
            return
        vid = str(it.data(0x0100) or '')
        if QMessageBox.question(self, APP_NAME,
                                '恢复版本 ' + vid[:12] + '？\n\n'
                                '· 退回那次部署之前的内容（指令文件 / 补丁层）\n'
                                '· 技能库不动\n'
                                '· 那个版本之后被改过的文件会先拦下，确认才覆盖\n\n'
                                '注意：恢复后需重启客户端才生效。',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        def force():
            def done2(code2, tail2):
                self.main._set_status(target_msg(code2, True), 'ok' if code2 == 0 else 'error')
                self.reload()
            self.main._enqueue(['-Target', self.target['key'], '-Restore', vid, '-Force'],
                               '强制恢复 ' + self.target['card'], done2,
                               kind='操作', target_card=self.target['card'])

        def target_msg(code, forced=False):
            if code == 0:
                return self.target['card'] + (' 已强制恢复版本 ' if forced else ' 已恢复版本 ') + vid[:12]
            if code == 3:
                return self.target['card'] + ' 恢复被拦下：文件在那之后被改过'
            return self.target['card'] + ' 恢复失败（退出码 %d），见日志' % code

        def done(code, tail):
            if code == 3:
                drift = [str(x) for x in (tail or []) if '漂移' in str(x) or '校验不通过' in str(x)]
                msg = '恢复被拦下了。\n\n' + ((drift[-1][:400] + '\n\n') if drift else '')
                msg += ('这些文件在那个版本之后被改动过，恢复会覆盖改动。\n'
                        '继续会先把当前内容另存到备份区（backup\\drift\\…）。\n\n要强制恢复吗？')
                self.main._set_status(self.target['card'] + ' 恢复已暂停：文件被外部改过', 'warn')
                if QMessageBox.question(self, APP_NAME, msg,
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                    force()
                    return
            else:
                self.main._set_status(target_msg(code), 'ok' if code == 0 else 'error')
            self.reload()

        self.main._enqueue(['-Target', self.target['key'], '-Restore', vid],
                           '恢复版本 ' + self.target['card'], done,
                           kind='操作', target_card=self.target['card'])


def _field_qss():
    return ('QPlainTextEdit { background: ' + C['SURFACE'] + '; border: 1px solid ' + C['BORDER']
            + '; border-radius: 10px; padding: 8px; color: ' + C['TEXT_PRIMARY'] + '; }')


class TaskComposeDialog(QDialog):
    """任务构建器：把一句话变成任务契约（档位 + 工作链 + 通道 + 交付要求）。

    文案由 inject.ps1 -Compose 生成（CLI 与 GUI 同一份实现的道理），
    这里只负责选项、预览与复制；不写任何配置、不联网。
    """

    PROFILES = [('max', 'MAX / 全开', '完整直接'),
                ('focused', 'FOCUS / 聚焦', '短链路'),
                ('builder', 'BUILDER / 构建', '实现打包'),
                ('research', 'RESEARCH / 研究', '来源证据'),
                ('creative', 'CREATIVE / 创作', '角色语气')]
    CHANNELS = [('auto', '自动判断'), ('reverse', '逆向 / 结构还原'), ('crack', '授权 / 校验链路'),
                ('pentest', '攻击面验证'), ('game', '客户端工程'), ('sample', '样本 / 取证'),
                ('content', '内容创作')]
    FORMATS = [('markdown', 'Markdown'), ('json', 'JSON'), ('code', '代码')]
    PRESETS = [('code', '代码交付'), ('research', '方案研究'), ('struct', '结构输出')]

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setWindowTitle('任务构建 · 一句话 → 任务契约')
        self.setMinimumSize(820, 780)
        self.setStyleSheet('QDialog { background: ' + C['BG'] + '; }')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(8)
        lay.addWidget(_mk_label('任务构建', 18, 'TEXT_PRIMARY', bold=True))
        lay.addWidget(_mk_label('写清目标（可补上下文与约束）→ 选档位 / 通道 / 输出格式 → 生成契约，复制到客户端发过去。'
                                '只生成文本，不写配置、不联网。', 12, 'TEXT_SECONDARY', bold=False))

        # 预设：一键把常见场景的输入铺好，之后随便改
        prow = QHBoxLayout()
        prow.setSpacing(8)
        prow.addWidget(_mk_label('预设', 12, 'TEXT_SECONDARY', bold=True))
        for k, label in self.PRESETS:
            b = QPushButton(label)
            b.setStyleSheet(_btn_style('ghost'))
            b.setFixedHeight(30)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, key=k: self.apply_preset(key))
            prow.addWidget(b)
        prow.addStretch(1)
        lay.addLayout(prow)

        lay.addWidget(_mk_label('档位', 12, 'TEXT_SECONDARY', bold=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        self._pgroup = QButtonGroup(self)
        self._pbtn = {}
        for i, (k, label, sub) in enumerate(self.PROFILES):
            b = QPushButton(label + '\n' + sub)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(46)
            b.setStyleSheet(_btn_style('chip'))
            self._pgroup.addButton(b, i)
            self._pbtn[k] = b
            row.addWidget(b, 1)
            if k == 'max':
                b.setChecked(True)
        lay.addLayout(row)

        lay.addWidget(_mk_label('通道', 12, 'TEXT_SECONDARY', bold=True))
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self._cgroup = QButtonGroup(self)
        self._cbtn = {}
        for i, (k, label) in enumerate(self.CHANNELS):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(32)
            b.setStyleSheet(_btn_style('chip'))
            self._cgroup.addButton(b, i)
            self._cbtn[k] = b
            row2.addWidget(b, 1)
            if k == 'auto':
                b.setChecked(True)
        lay.addLayout(row2)

        lay.addWidget(_mk_label('目标（一句话也行）', 12, 'TEXT_SECONDARY', bold=True))
        self.goal = QPlainTextEdit()
        self.goal.setPlaceholderText('例：把 D:\\samples\\demo.exe 的注册校验链还原出来，并给出可回滚的补丁')
        self.goal.setFixedHeight(64)
        self.goal.setStyleSheet(_field_qss())
        lay.addWidget(self.goal)

        # 上下文 / 约束：并排两栏，省高度
        cols = QHBoxLayout()
        cols.setSpacing(12)
        for attr, title, ph in (('ctx', '上下文（可选）', '样本来源 / 环境 / 已知条件…'),
                               ('con', '约束（可选）', '格式 / 边界 / 不能碰的东西…')):
            box = QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(_mk_label(title, 12, 'TEXT_SECONDARY', bold=True))
            w = QPlainTextEdit()
            w.setPlaceholderText(ph)
            w.setFixedHeight(52)
            w.setStyleSheet(_field_qss())
            box.addWidget(w)
            setattr(self, attr, w)
            cols.addLayout(box, 1)
        lay.addLayout(cols)

        lay.addWidget(_mk_label('输出格式', 12, 'TEXT_SECONDARY', bold=True))
        frow = QHBoxLayout()
        frow.setSpacing(8)
        self._fgroup = QButtonGroup(self)
        self._fbtn = {}
        for i, (k, label) in enumerate(self.FORMATS):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(32)
            b.setStyleSheet(_btn_style('chip'))
            self._fgroup.addButton(b, i)
            self._fbtn[k] = b
            frow.addWidget(b, 1)
            if k == 'markdown':
                b.setChecked(True)
        lay.addLayout(frow)

        row3 = QHBoxLayout()
        self.hint = _mk_label('', 12, 'TEXT_MUTED', bold=False)
        row3.addWidget(self.hint)
        row3.addStretch(1)
        b_gen = QPushButton('生成契约')
        b_gen.setStyleSheet(_btn_style('primary'))
        b_gen.setFixedHeight(34)
        b_gen.clicked.connect(self.generate)
        row3.addWidget(b_gen)
        b_copy = QPushButton('复制')
        b_copy.setStyleSheet(_btn_style('ghost'))
        b_copy.setFixedHeight(34)
        b_copy.clicked.connect(self._copy)
        row3.addWidget(b_copy)
        lay.addLayout(row3)

        lay.addWidget(_mk_label('预览（可直接复制）', 12, 'TEXT_SECONDARY', bold=True))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet('QPlainTextEdit { background: ' + C['LOG_BG'] + '; border: 1px solid '
                                   + C['BORDER'] + '; border-radius: 10px; padding: 10px; color: '
                                   + C['LOG_FG'] + '; font-family: Consolas, monospace; font-size: 12px; }')
        lay.addWidget(self.preview, 1)

    def _selected(self):
        prof = 'max'
        for k, b in self._pbtn.items():
            if b.isChecked():
                prof = k
        chan = 'auto'
        for k, b in self._cbtn.items():
            if b.isChecked():
                chan = k
        return prof, chan

    def _selected_format(self):
        for k, b in self._fbtn.items():
            if b.isChecked():
                return k
        return 'markdown'

    def _set_buttons(self, group, value):
        for k, b in group.items():
            b.setChecked(k == value)

    def apply_preset(self, key):
        """预设由 inject.ps1 提供（单一实现）：拿 -Preset 的 Json 结果回填表单。"""
        out = os.path.join(work_root(), 'compose-preset.json')
        try:
            os.remove(out)
        except OSError:
            pass

        def done(code, tail):
            d = read_json(out) or {}
            if code != 0 or not d:
                self.hint.setText('预设读取失败（退出码 %d），见日志' % code)
                return
            self.goal.setPlainText(d.get('goal') or '')
            self.ctx.setPlainText(d.get('context') or '')
            self.con.setPlainText(d.get('constraints') or '')
            self._set_buttons(self._pbtn, d.get('profile') or 'max')
            self._set_buttons(self._cbtn, d.get('channel') or 'auto')
            self._set_buttons(self._fbtn, d.get('format') or 'markdown')
            self.hint.setText('已套用预设 %s（可继续改）' % key)

        self.hint.setText('读取预设…')
        self.main._enqueue(['-Target', TARGETS[0]['key'], '-Compose', '-Preset', key, '-Json', '-Out', out],
                           '读取任务预设 ' + key, done, kind='操作', target_card=TARGETS[0]['card'])

    def generate(self):
        goal = self.goal.toPlainText().strip()
        if not goal:
            self.hint.setText('先写一句目标')
            return
        prof, chan = self._selected()
        fmt = self._selected_format()
        out = os.path.join(work_root(), 'compose-preview.md')
        try:
            os.remove(out)
        except OSError:
            pass

        def done(code, tail):
            if code != 0 or not os.path.exists(out):
                self.hint.setText('生成失败（退出码 %d），见日志' % code)
                return
            try:
                with open(out, encoding='utf-8') as fh:
                    self.preview.setPlainText(fh.read())
                self.hint.setText('已生成（档位 %s / 通道 %s / 格式 %s）' % (prof, chan, fmt))
            except Exception as e:
                self.hint.setText('读不到生成结果：' + str(e))

        self.hint.setText('生成中…')
        self.main._enqueue(['-Target', TARGETS[0]['key'], '-Compose', '-Profile', prof,
                            '-Channel', chan, '-Format', fmt,
                            '-Goal', goal,
                            '-Context', self.ctx.toPlainText().strip(),
                            '-Constraints', self.con.toPlainText().strip(),
                            '-Out', out],
                           '生成任务契约', done, kind='操作', target_card=TARGETS[0]['card'])

    def _copy(self):
        text = self.preview.toPlainText().strip()
        if not text:
            self.hint.setText('还没有内容可复制')
            return
        QApplication.clipboard().setText(text)
        self.hint.setText('已复制到剪贴板')


# ------------------------------------------------------------------ 历史页

class HistoryPage(Page):
    """操作历史时间线：每次注入/卸载/自检的记录。"""

    @staticmethod
    def _kind_color(kind):
        """按当前主题取色。
        旧写法是类属性 KIND_COLOR = {...C[...]...}，在 import 时就固化了调色板，
        切主题后历史页颜色不跟着变。
        """
        return {'注入': C['SUCCESS'], '卸载': C['DANGER'],
                '自检': C['ACCENT_GLOW'], '操作': C['TEXT_SECONDARY']}.get(kind, C['TEXT_SECONDARY'])

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        hdr = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        ttl = _mk_label('历史', 21, 'TEXT_PRIMARY', bold=True)
        col.addWidget(ttl)
        sub = _mk_label('最近的注入 / 卸载 / 自检记录（保存在工作目录 history.json）', 12, 'TEXT_SECONDARY', bold=False)
        col.addWidget(sub)
        hdr.addLayout(col)
        hdr.addStretch(1)
        b_clear = QPushButton('清空历史')
        b_clear.setStyleSheet(_btn_style('danger'))
        b_clear.setFixedHeight(34)
        b_clear.clicked.connect(self._clear)
        hdr.addWidget(b_clear)
        outer.addLayout(hdr)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.NoSelection)
        self._list.setStyleSheet(
            'QListWidget { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 12px; padding: 8px; font-size: 12px; color: ' + C['TEXT_PRIMARY'] + '; }'
            'QListWidget::item { border-bottom: 1px solid ' + C['BORDER_LIGHT'] + '; padding: 8px 6px; }')
        outer.addWidget(self._list, 1)
        self._reload()

    def _reload(self):
        self._list.clear()
        recs = history_load()
        for r in recs[:100]:
            color = self._kind_color(r.get('kind', ''))
            mark = '✓' if r.get('ok') else '✗'
            mark_color = C['SUCCESS'] if r.get('ok') else C['DANGER']
            item = QListWidgetItem(
                '<b style="color:' + color + ';">' + r.get('kind', '') + '</b>'
                '&nbsp;&nbsp;' + r.get('target', '')
                + ' &nbsp;·&nbsp; <span style="color:' + C['TEXT_MUTED'] + ';">'
                + r.get('time', '') + '</span>'
                + '&nbsp;&nbsp;<span style="color:' + mark_color + ';">' + mark + ' '
                + str(r.get('detail', '')) + '</span>')
            self._list.addItem(item)
        if not recs:
            self._list.addItem('<span style="color:' + C['TEXT_MUTED'] + ';">暂无记录 — 去模板页部署一次试试</span>')

    def on_enter(self):
        self._reload()

    def _clear(self):
        ans = QMessageBox.question(self, APP_NAME, '清空全部历史记录？',
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        try:
            os.remove(history_path())
        except Exception:
            pass
        self._reload()


# ------------------------------------------------------------------ 设置页

class SettingsPage(Page):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        # 外层滚动容器：窗口高度不足时设置页可滚动
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)
        body_w = QWidget()
        body_w.setStyleSheet('background: transparent; border: none;')
        scroll.setWidget(body_w)
        outer = QVBoxLayout(body_w)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)
        outer.setAlignment(Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        t = _mk_label('设置', 21, 'TEXT_PRIMARY', bold=True)
        col.addWidget(t)
        s = _mk_label('运行偏好与外观', 12, 'TEXT_SECONDARY', bold=False)
        col.addWidget(s)
        outer.addLayout(col)

        # 外观（主题切换）
        card0 = self._card()
        c0 = QVBoxLayout(card0)
        c0.setContentsMargins(18, 14, 18, 14)
        c0.setSpacing(10)
        t0 = _mk_label('外观', 13, 'TEXT_PRIMARY', bold=True)
        t0.setMinimumHeight(20)
        c0.addWidget(t0)
        seg = QHBoxLayout()
        seg.setSpacing(8)
        self._theme_btns = {}
        theme_group = QButtonGroup(self)   # 互斥：选中一档自动取消其他
        theme_group.setExclusive(True)
        for tid, label in (('auto', '跟随系统'), ('dark', '🌙 深色'), ('light', '☀ 浅色')):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(36)
            b.setFixedWidth(108)
            _set_px_font(b, 13, bold=True)
            b.setStyleSheet(
                'QPushButton { background: transparent; color: ' + C['TEXT_SECONDARY'] + ';'
                ' border: 1px solid ' + C['BORDER'] + '; font-weight: 600; }'
                'QPushButton:hover { border-color: ' + C['ACCENT'] + '; color: ' + C['TEXT_PRIMARY'] + '; }'
                'QPushButton:checked { background: ' + C['ACCENT'] + '; color: #FFFFFF;'
                ' border-color: ' + C['ACCENT'] + '; }')
            b.clicked.connect(lambda _=False, k=tid: self.main.apply_theme(k))
            theme_group.addButton(b)
            seg.addWidget(b)
            self._theme_btns[tid] = b
        seg.addStretch(1)
        c0.addLayout(seg)

        outer.addWidget(card0)

        # 自动注入（iOS 式开关）
        card1 = self._card()
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(18, 14, 18, 14)
        c1.setSpacing(8)
        g1 = QGridLayout()
        g1.setHorizontalSpacing(14)
        g1.setVerticalSpacing(5)
        self._auto = ToggleSwitch()
        self._auto.setChecked(main._read_auto())
        self._auto.toggled.connect(main._save_auto)
        g1.addWidget(self._auto, 0, 0)
        lbl1 = _mk_label('启动时自动注入', 13, 'TEXT_PRIMARY', bold=True)
        g1.addWidget(lbl1, 0, 1)
        note = _mk_label('打开后每次启动工具会自动对「已注入」的端重跑一次注入，幂等覆盖，保持文件最新。', 11, 'TEXT_MUTED', wrap=True)
        note.setMinimumHeight(18)
        g1.addWidget(note, 1, 1)
        g1.setColumnMinimumWidth(0, 56)
        g1.setColumnStretch(1, 1)
        c1.addLayout(g1)
        outer.addWidget(card1)

        # 开机自启
        cardA = self._card()
        cA = QVBoxLayout(cardA)
        cA.setContentsMargins(18, 14, 18, 14)
        cA.setSpacing(6)
        gA = QGridLayout()
        gA.setHorizontalSpacing(14)
        gA.setVerticalSpacing(5)
        self._autostart = ToggleSwitch()
        self._autostart.setChecked(is_autostart_on())
        self._autostart.toggled.connect(self._toggle_autostart)
        gA.addWidget(self._autostart, 0, 0)
        lblA = _mk_label('开机自动启动', 13, 'TEXT_PRIMARY', bold=True)
        gA.addWidget(lblA, 0, 1)
        noteA = _mk_label('开机后在后台启动本工具；配合「启动时自动注入」实现开机即部署。', 11, 'TEXT_MUTED', wrap=True)
        noteA.setMinimumHeight(18)
        gA.addWidget(noteA, 1, 1)
        gA.setColumnMinimumWidth(0, 56)
        gA.setColumnStretch(1, 1)
        cA.addLayout(gA)
        outer.addWidget(cardA)

        # 退出行为：直接退出 vs 最小化到托盘
        cardE = self._card()
        cE = QVBoxLayout(cardE)
        cE.setContentsMargins(18, 14, 18, 14)
        cE.setSpacing(8)
        gE = QGridLayout()
        gE.setHorizontalSpacing(14)
        gE.setVerticalSpacing(5)
        self._exit_tray = ToggleSwitch()
        # exitToTray 默认 True（原行为）；开关语义=「点关闭直接退出」，与 exitToTray 相反
        self._exit_tray.setChecked(read_app_config().get('exitToTray') is False)
        self._exit_tray.toggled.connect(self._toggle_exit_mode)
        gE.addWidget(self._exit_tray, 0, 0)
        lblE = _mk_label('点 ✕ 直接退出软件', 13, 'TEXT_PRIMARY', bold=True)
        gE.addWidget(lblE, 0, 1)
        noteE = _mk_label('关闭（OFF）：点 ✕ 最小化到托盘，右键托盘图标退出。开启（ON）：点 ✕ 直接退出。', 11, 'TEXT_MUTED', wrap=True)
        noteE.setMinimumHeight(18)
        gE.addWidget(noteE, 1, 1)
        gE.setColumnMinimumWidth(0, 56)
        gE.setColumnStretch(1, 1)
        cE.addLayout(gE)
        outer.addWidget(cardE)

        # 使用说明弹窗
        cardS = self._card()
        cS = QVBoxLayout(cardS)
        cS.setContentsMargins(18, 14, 18, 14)
        cS.setSpacing(8)
        gS = QGridLayout()
        gS.setHorizontalSpacing(14)
        gS.setVerticalSpacing(5)
        self._skip_agree = ToggleSwitch()
        self._skip_agree.setChecked(bool(read_app_config().get('skipAgreement')))
        self._skip_agree.toggled.connect(self._toggle_skip_agreement)
        gS.addWidget(self._skip_agree, 0, 0)
        lblS = _mk_label('启动时不显示使用说明弹窗', 13, 'TEXT_PRIMARY', bold=True)
        gS.addWidget(lblS, 0, 1)
        noteS = _mk_label('开启后下次启动直接进主界面；仍可在本页随时查看使用说明。', 11, 'TEXT_MUTED', wrap=True)
        noteS.setMinimumHeight(18)
        gS.addWidget(noteS, 1, 1)
        gS.setColumnMinimumWidth(0, 56)
        gS.setColumnStretch(1, 1)
        cS.addLayout(gS)
        outer.addWidget(cardS)

        # 工作目录
        card2 = self._card()
        c2 = QHBoxLayout(card2)
        c2.setContentsMargins(18, 14, 18, 14)
        c2.setSpacing(10)
        cc2 = QVBoxLayout()
        cc2.setSpacing(3)
        tt = _mk_label('工作目录', 13, 'TEXT_PRIMARY', bold=True)
        tt.setMinimumHeight(20)
        cc2.addWidget(tt)
        pp = _mk_label(work_root(), 11, 'TEXT_MUTED', bold=False)
        pp.setMinimumHeight(18)
        cc2.addWidget(pp)
        c2.addLayout(cc2)
        c2.addStretch(1)
        b_open = QPushButton('打开')
        b_open.setStyleSheet(_btn_style('ghost'))
        b_open.setFixedHeight(34)
        b_open.clicked.connect(lambda: open_in_explorer(work_root()))
        c2.addWidget(b_open)
        outer.addWidget(card2)

        # 教程
        card3 = self._card()
        c3 = QHBoxLayout(card3)
        c3.setContentsMargins(18, 14, 18, 14)
        c3.setSpacing(10)
        cc3 = QVBoxLayout()
        cc3.setSpacing(3)
        t3 = _mk_label('使用教程', 13, 'TEXT_PRIMARY', bold=True)
        t3.setMinimumHeight(20)
        cc3.addWidget(t3)
        p3 = _mk_label('选模板 → 注入 → 重启客户端；自检与排障说明。', 11, 'TEXT_MUTED', bold=False)
        p3.setMinimumHeight(18)
        cc3.addWidget(p3)
        c3.addLayout(cc3)
        c3.addStretch(1)
        b_tut = QPushButton('查看')
        b_tut.setStyleSheet(_btn_style('ghost'))
        b_tut.setFixedHeight(34)
        b_tut.clicked.connect(lambda: TutorialDialog(self).exec())
        c3.addWidget(b_tut)
        outer.addWidget(card3)

        # 危险区：与普通设置卡同构，仅以红色标题与描边按钮区分，不再用红底大框
        dz = self._card()
        c4 = QVBoxLayout(dz)
        c4.setContentsMargins(18, 14, 18, 14)
        c4.setSpacing(8)
        head4 = QHBoxLayout()
        t4 = _mk_label('卸载与还原', 13, 'DANGER', bold=True)
        head4.addWidget(t4)
        head4.addStretch(1)
        c4.addLayout(head4)
        p4 = _mk_label('卸载会摘除注入段并还原备份；客户端自带技能不受影响。操作前请先关闭对应客户端。', 11, 'TEXT_SECONDARY', bold=False)
        c4.addWidget(p4)
        line4 = QFrame()
        line4.setFixedHeight(1)
        line4.setStyleSheet('background: ' + C['BORDER_LIGHT'] + '; border: none;')
        c4.addWidget(line4)
        row = QHBoxLayout()
        row.setSpacing(10)
        for tg in TARGETS:
            b = QPushButton('卸载 ' + tg['card'])
            b.setStyleSheet(_btn_style('danger'))
            b.setFixedHeight(34)
            b.clicked.connect(lambda _=False, t=tg: self.main._uninstall(t))
            row.addWidget(b)
        row.addStretch(1)
        c4.addLayout(row)
        outer.addWidget(dz)

        # 关于（含 APP_BUILD 版本标识：排查「跑的是不是新包」只看这一行）
        line5 = QFrame()
        line5.setFixedHeight(1)
        line5.setStyleSheet('background: ' + C['BORDER_LIGHT'] + '; border: none;')
        outer.addWidget(line5)
        about = QLabel(APP_NAME + ' ' + APP_VERSION + ' · ' + APP_BUILD
                       + '　|　注入即用 · 卸载即还原 · 不修改客户端本体')
        _set_px_font(about, 11)
        about.setStyleSheet('color: ' + C['TEXT_MUTED'] + ';')
        about.setAlignment(Qt.AlignCenter)
        about.setWordWrap(True)
        outer.addWidget(about)

    def _toggle_exit_mode(self, direct_exit):
        write_app_config('exitToTray', not direct_exit)

    def _toggle_skip_agreement(self, skip):
        write_app_config('skipAgreement', bool(skip))

    def _toggle_autostart(self, on):
        ok = set_autostart(on)
        if not ok:
            QMessageBox.warning(self, APP_NAME, '写入开机自启失败（注册表权限或系统限制）。')
            self._autostart.blockSignals(True)
            self._autostart.setChecked(not on)
            self._autostart.blockSignals(False)

    def _card(self):
        f = QFrame()
        f.setObjectName('SettingCard')
        f.setStyleSheet(
            'QFrame#SettingCard { background: ' + C['CARD_BG'] + '; border: 1px solid ' + C['BORDER']
            + '; border-radius: 12px; }')
        return f


# ------------------------------------------------------------------ 教程弹窗

class TutorialDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME + ' · 使用教程')
        self.setFixedSize(760, 600)
        self.setStyleSheet('QDialog { background: ' + C['SURFACE'] + '; }')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        h = _mk_label('使用教程', 15, 'TEXT_PRIMARY', bold=True)
        lay.addWidget(h)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(TUTORIAL)
        box.setStyleSheet(
            'QPlainTextEdit { background: ' + C['BG'] + '; border: 1px solid ' + C['BORDER'] + ';'
            ' border-radius: 10px; padding: 14px; font-size: 12px; color: ' + C['TEXT_PRIMARY'] + '; }')
        lay.addWidget(box, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton('知道了')
        b.setStyleSheet(_btn_style('primary'))
        b.setFixedSize(120, 38)
        b.clicked.connect(self.accept)
        row.addWidget(b)
        lay.addLayout(row)


TUTORIAL = """使用步骤

1. 选模板并注入
   「模板」页按目标模型分组选一张卡，点上面的端按钮即开始注入。
   DeepSeek 4.1 Flash 组的 V5.2c 是实测最稳的（99.3%~100%, n=300）。
   换模板 = 再点一次别的卡，覆盖是幂等的。

2. 重启客户端
   注入完成后在「首页」点对应卡片的「重启」。APPEND_SYSTEM.md 只在
   客户端启动时读取。

3. 验证（不需要任何激活词）
   注入的规范常驻在系统提示词的 addendum 段，每一轮请求都在。
   直接说你的需求即可；想确认生效，点「自检两端」看 L1 行。

4. 注入内容
   - PiDeck   ~/.pi/agent/APPEND_SYSTEM.md（addendum 段）
   - DSH      $DSH_HOME/AGENTS.md（持久 user 消息）+ cordis.patch.yml 预算层
   - 两端 skills/（63+ 模块技能库）
   DSH 预算不足时 AGENTS.md 会被整份丢弃，工具已自动写 patch 层抬高 maxBytes。

5. 技能管理
   「技能」页可搜索、按状态筛选；「禁用」= 把技能目录移到 skills-disabled
   （不在扫描路径内），「启用」= 移回。本工具部署的技能会标注来源。

6. 卸载
   「设置」页底部危险区，或「首页」卡片上的「卸载」按钮。
   只摘除本工具管理段并还原备份，客户端自带技能不动。
"""


# ------------------------------------------------------------------ 无边框窗口

class FramelessWindow(QWidget):
    """无边框窗口基类。

    缩放/拖拽用原生 WM_NCHITTEST：鼠标在窗口边缘 8px 内（无论悬停在哪个子控件上）
    都返回 HTLEFT/HTRIGHT/... 让 Windows 自己处理缩放——子控件不再吞掉边缘事件，
    这是"拉不动"的根治方案（mousePressEvent 方案会被页面内的 QFrame 挡住）。
    """

    RESIZE_MARGIN = 8

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint)

    # ---- 原生命中测试

    def nativeEvent(self, eventType, message):
        """WM_NCHITTEST：整窗边缘交给 Windows 原生缩放。

        坐标一致性：WM_NCHITTEST 的 lParam 是**物理像素**，QWidget.rect() 是**逻辑
        像素**——150% 缩放下差 1.5 倍。这里全程用物理像素比较：GetClientRect +
        ClientToScreen（与 Qt 内部处理同一个消息的口径一致），只在需要 childAt
        时再换算回逻辑坐标。

        注：`c_short` 属于 ctypes，不在 ctypes.wintypes 里（写错会抛 AttributeError，
        被下面的 except 吞掉后表现为「命中测试静默失效」），所以这里用纯 Python
        做符号扩展，不依赖 ctypes 的标量类型。
        """
        if eventType != b'windows_generic_MSG':
            return False, 0
        import ctypes
        import ctypes.wintypes as wt
        msg = wt.MSG.from_address(int(message))
        if msg.message != 0x0084:   # 只看 WM_NCHITTEST
            return False, 0
        if self.isMaximized() or self.isFullScreen():
            return False, 0
        # 屏幕物理像素坐标（副屏在主屏左侧时为负 → 手动符号扩展）
        x = msg.lParam & 0xFFFF
        if x >= 0x8000:
            x -= 0x10000
        y = (msg.lParam >> 16) & 0xFFFF
        if y >= 0x8000:
            y -= 0x10000
        try:
            hwnd = ctypes.c_void_p(int(self.winId()))   # c_void_p：避免 64 位句柄被截成 int
            u32 = ctypes.windll.user32
            origin = wt.POINT(0, 0)
            csz = wt.RECT()
            if not u32.ClientToScreen(hwnd, ctypes.byref(origin)):
                return False, 0
            if not u32.GetClientRect(hwnd, ctypes.byref(csz)):
                return False, 0
        except Exception:
            return False, 0
        left, top = origin.x, origin.y
        right = left + csz.right
        bottom = top + csz.bottom
        dpr = self.devicePixelRatioF() or 1.0
        m = max(4, int(round(self.RESIZE_MARGIN * dpr)))   # 热区按 DPI 折算成物理像素
        # 窗口外（含热区外扩范围）一律不接管，避免把光标判成“贴着边框”而粘住缩放
        if not (left - m <= x <= right + m and top - m <= y <= bottom + m):
            return False, 0
        L = (x - left) <= m
        R = (right - x) <= m
        T = (y - top) <= m
        B = (bottom - y) <= m
        if T and L: return True, 13   # HTTOPLEFT
        if T and R: return True, 14   # HTTOPRIGHT
        if B and L: return True, 16   # HTBOTTOMLEFT
        if B and R: return True, 17   # HTBOTTOMRIGHT
        if T: return True, 12         # HTTOP
        if B: return True, 15         # HTBOTTOM
        if L: return True, 10         # HTLEFT
        if R: return True, 11         # HTRIGHT
        # 标题栏空白区交给 Windows 拖动（按钮/文字控件所在处仍是普通点击）
        if (y - top) <= int(round(40 * dpr)):
            from PySide6.QtCore import QPoint
            lp = QPoint(int(round((x - left) / dpr)), int(round((y - top) / dpr)))
            if isinstance(self.childAt(lp), TitleBar):
                return True, 2            # HTCAPTION
        return False, 0


class TitleBar(QFrame):
    """自绘标题栏：拖拽区 + logo + 标题 + 窗口按钮。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win = window
        self.setFixedHeight(40)
        self.setStyleSheet('TitleBar { background: transparent; border: none; }')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 6, 0)
        lay.setSpacing(8)

        logo = QLabel()
        logo.setPixmap(_make_icon_pm(22, C['ICON_C1'], C['ICON_C2'], '学', 9))
        lay.addWidget(logo)

        title = _mk_label(APP_NAME, 12, 'TEXT_SECONDARY', bold=True)
        lay.addWidget(title)
        lay.addStretch(1)

        btn_min = self._win_btn('—', self._win.showMinimized)
        btn_max = self._win_btn('☐', self._toggle_max)
        btn_cls = self._win_btn('✕', self._win.close, danger=True)
        for b in (btn_min, btn_max, btn_cls):
            lay.addWidget(b)

    def _win_btn(self, glyph, fn, danger=False):
        b = QPushButton(glyph)
        _set_px_font(b, 12)
        b.setFixedSize(34, 28)
        b.setCursor(Qt.PointingHandCursor)
        color = C['DANGER'] if danger else C['TEXT_SECONDARY']
        hover_bg = C['DANGER_SOLID'] if danger else C['CARD_BG_HOVER']
        hover_fg = '#FFFFFF' if danger else C['TEXT_PRIMARY']
        b.setStyleSheet(
            'QPushButton { background: transparent; color: ' + color + '; border: none;'
            ' border-radius: 6px; }'
            'QPushButton:hover { background: ' + hover_bg + '; color: ' + hover_fg + '; }')
        b.clicked.connect(fn)
        return b

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._win.windowHandle().startSystemMove()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self._toggle_max()


# ------------------------------------------------------------------ 主窗口

class MainWindow(FramelessWindow):
    PAGES = ('home', 'tpl', 'skills', 'log', 'history', 'settings')

    def __init__(self, skip_auto=False):
        super().__init__()
        self.setWindowTitle(APP_NAME + ' ' + APP_VERSION)
        self.resize(1180, 720)
        self.setMinimumSize(1020, 640)
        self.setStyleSheet('MainWindow { background: ' + C['BG'] + '; }')

        self._runner = None
        self._busy = False
        self._queue = []
        self._quit_requested = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = SideBar()
        self.sidebar.switched = self.switch_page
        body.addWidget(self.sidebar)

        self.stack = FadeStack()
        self.page_home = HomePage(self)
        self.page_tpl = TemplatePage(self)
        self.page_skills = SkillsPage(self)
        self.page_log = LogPage(self)
        self.page_history = HistoryPage(self)
        self.page_settings = SettingsPage(self)
        for p in (self.page_home, self.page_tpl, self.page_skills,
                  self.page_log, self.page_history, self.page_settings):
            self.stack.addWidget(p)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(4000)
        self._refresh_status()
        self._sync_theme_buttons()
        self._setup_tray()

        if not skip_auto and self._read_auto() and self._any_installed():
            QTimer.singleShot(400, self._auto_reinject)

    # ---------------------------------------------------------- 导航

    def switch_page(self, idx):
        self.stack.setCurrentIndex(idx)
        self.sidebar.select(idx, emit=False)
        page = self.stack.currentWidget()
        if hasattr(page, 'on_enter'):
            page.on_enter()

    def go_deploy(self, target):
        """首页点某端的「去部署」：切到模板页，并把那里的① 客户端选中该端（保持一致）"""
        page = self.page_tpl
        if getattr(page, '_sel_target', None) != target['key']:
            page._set_target(target['key'])
        self.switch_page(1)

    def _picked_addons(self):
        """取模板页勾选的附加包 key 列表（无勾选返回 None）。
        读 TemplatePage 的状态字典，而不是遍历勾选框控件——卡片切分组后会
        被 deleteLater，旧写法会拿已销毁的 QCheckBox 调 isChecked() 而报错。
        """
        page = getattr(self, 'page_tpl', None)
        if page is None:
            return None
        picked = page._addon_selected()
        return picked or None

    # ---------------------------------------------------------- 配置

    def _read_auto(self):
        cfg = read_json(tool_config_path()) or {}
        return bool(cfg.get('autoInject'))

    def _save_auto(self, val):
        p = tool_config_path()
        cfg = read_json(p) or {}
        cfg['autoInject'] = bool(val)
        try:
            write_json(p, cfg)
        except Exception:
            pass

    def _sync_theme_buttons(self):
        page = getattr(self, 'page_settings', None)
        if not page:
            return
        for tid, b in page._theme_btns.items():
            b.setChecked(tid == _cfg_theme())

    def apply_theme(self, name):
        """切换主题：持久化 → 重建窗口。name: auto/dark/light

        旧实现直接 self.close()，会被 closeEvent 拦成「最小化到托盘」——
        于是每次切主题都弹一次气泡，并留下一个隐藏的旧窗口 + 一个幽灵托盘图标。
        这里改成显式 _teardown（停定时器、收托盘、让 close 走 accept），
        并把页面序号与导航选中态一并搬到新窗口。
        """
        global _ACTIVE_WINDOW
        if name not in ('auto', 'dark', 'light'):
            return
        if self._busy:
            # 重建窗口会连带丢掉正在跑的注入线程，任务执行中不切
            self._set_status('任务执行中，完成后再切换主题', 'warn')
            self._sync_theme_buttons()
            return
        p = tool_config_path()
        cfg = read_json(p) or {}
        cfg['theme'] = name
        try:
            write_json(p, cfg)
        except Exception:
            pass
        actual = _system_theme() if name == 'auto' else name
        idx = self.stack.currentIndex()
        self._teardown()
        set_theme(actual)
        _apply_global_qss(QApplication.instance())
        new_w = MainWindow(skip_auto=True)
        new_w.stack.setCurrentIndex(idx)
        new_w.sidebar.select(idx, animate=False, emit=False)   # 导航高亮/指示条跟着走
        new_w.show()
        _ACTIVE_WINDOW = new_w
        new_w._sync_theme_buttons()

    def _teardown(self):
        """重建前显式清理：定时器 / 托盘图标 / 窗口，避免幽灵图标与残留窗口。"""
        self._timer.stop()
        try:
            self.tray.hide()
        except Exception:
            pass
        self._quit_requested = True     # 让 closeEvent 走 accept 分支，不弹托盘气泡
        self.close()
        self.deleteLater()

    # ---------------------------------------------------------- 状态

    def _refresh_status(self):
        any_installed = False
        for target in TARGETS:
            st = read_json(state_path(target['key']))
            ver = st.get('versionLabel') if st else None
            mode = st.get('skillMode') if st else None
            installed = bool(st)
            any_installed = any_installed or installed
            self.page_home.cards[target['key']].update_state(installed, ver, mode)
        if self._busy:
            self.page_home._chip_all.set_state('任务执行中…', C['WARN'])
        elif any_installed:
            self.page_home._chip_all.set_state('已部署', C['SUCCESS'])
        else:
            self.page_home._chip_all.set_state('未部署', C['TEXT_MUTED'])

    def _any_installed(self):
        return any(read_json(state_path(t['key'])) for t in TARGETS)

    def _set_status(self, text, level=None):
        color = {'ok': C['SUCCESS'], 'error': C['DANGER'], 'warn': C['WARN']}.get(level, C['TEXT_SECONDARY'])
        self.page_log._status.setText('● ' + text)
        self.page_log._status.setStyleSheet('color: ' + color + ';')
        self.page_home._chip_all.set_state(text, color or C['TEXT_SECONDARY'])

    def _set_busy(self, busy):
        self._busy = busy

    # ---------------------------------------------------------- 执行队列

    def _enqueue(self, args, label, on_done=None, kind='操作', target_card=''):
        self._queue.append((args, label, on_done, kind, target_card))
        self._pump()

    def _pump(self):
        if self._busy or not self._queue:
            return
        args, label, on_done, kind, target_card = self._queue.pop(0)
        self._launch(args, label, on_done, kind, target_card)

    def _launch(self, args, label, on_done, kind='操作', target_card=''):
        if not IS_WINDOWS:
            self._set_status('当前仅支持 Windows', 'error')
            self._pump()          # 早退也要继续队列，否则后面的任务全卡住
            return
        self._cur_kind = kind
        self._cur_target = target_card
        self._cur_target_key = ''
        try:
            if '-Target' in args:
                self._cur_target_key = args[args.index('-Target') + 1]
        except Exception:
            self._cur_target_key = ''
        script = _res('inject.ps1')
        if not os.path.exists(script):
            self._set_status('找不到 inject.ps1', 'error')
            self._pump()
            return

        argv = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script] + args
        self._busy = True
        self._set_status(label + '…', 'warn')
        self._log_line('')
        self._log_line('> ' + ' '.join(argv))

        # 看门狗：卡死的任务不能把界面拖住（alice 那套 breaker 的思路，
        # 落到这里就是一个单任务超时 + 杀整棵进程树）。
        self._task_started = time.time()
        if not hasattr(self, '_watchdog'):
            self._watchdog = QTimer(self)
            self._watchdog.setSingleShot(True)
            self._watchdog.timeout.connect(self._on_task_timeout)
        self._watchdog.start(int(TASK_TIMEOUT_SEC * 1000))

        runner = Runner(argv, self)
        runner.line.connect(self._log_line)
        runner.done.connect(lambda code, r=runner: self._on_finished(code, r, label, on_done))
        runner.finished.connect(lambda r=runner: self._recycle_runner(r))
        self._runner = runner
        runner.start()

    def _on_task_timeout(self):
        r = self._runner
        if r is None:
            return
        used = int(time.time() - getattr(self, '_task_started', time.time()))
        self._log_line('[WARN] 任务已跑 ' + str(used) + ' 秒，超过上限 '
                       + str(TASK_TIMEOUT_SEC) + ' 秒，已终止（防界面卡死）')
        r.terminate_tree()

    def _recycle_runner(self, runner):
        """线程真正结束后再回收（finished 信号在 run() 返回后才发，此时删除才安全）。"""
        try:
            if hasattr(self, '_watchdog'):
                self._watchdog.stop()
        except Exception:
            pass
        if self._runner is runner:
            self._runner = None
        runner.deleteLater()

    def _on_finished(self, code, runner, label, on_done):
        tail = runner.tail() if runner is not None else []
        self._log_line('')
        self._log_line('退出码: ' + str(code))
        if code == 3:
            self._log_line('（退出码 3 = 需要你确认：本次没有写入任何文件）')
        ms = read_model_status(getattr(self, '_cur_target_key', ''))
        if ms:
            self._log_line('· ' + ms)
        self._busy = False
        self._refresh_status()
        history_add(self._cur_kind, self._cur_target, label, code == 0)
        if on_done:
            on_done(code, tail)
        QTimer.singleShot(0, self._pump)

    def _log_line(self, text):
        self.page_log.append(text)

    # ---------------------------------------------------------- 动作

    def _run_install(self, target, version_key, version_label, no_skills, label=None, addon_keys=None,
                     skill_mode='full'):
        prompt = ensure_prompt(version_key)
        args = ['-Target', target['key'], '-SourcePrompt', prompt]
        mode_note = ''
        if no_skills:
            args.append('-NoSkills')
        else:
            if addon_keys:
                # 指令集模板 + 附加技能包：主库 + 附加包目录一起部署
                dirs = ['skills-v4']
                dirs += [a['skill_dir'] for a in ADDONS if a['key'] in addon_keys]
                args += ['-SkillsSource', ';'.join(dirs)]
            args += ['-SkillMode', skill_mode]
            args += ['-MenuKeepAdvertised', ';'.join(a['skill_name'] for a in ADDONS)]
            mode_note = ' · ' + ('极简模式' if skill_mode == 'menu' else '完整模式')

        def done(code, tail):
            if code == 0:
                self._set_status(target['card'] + ' 注入完成 · ' + version_label, 'ok')
                self.switch_page(3)
            else:
                self._set_status(target['card'] + ' 注入失败，见日志', 'error')
                self.switch_page(3)

        self._enqueue(args, label or ('注入 ' + target['card'] + ' · ' + version_label + mode_note), done,
                      kind='注入', target_card=target['card'])

    def _remove_addons(self, target, skill_dirs, names):
        """从目标端移除附加技能包（不动指令集与其它技能）。"""
        args = ['-Target', target['key'], '-RemoveAddons', skill_dirs]

        def done(code, tail):
            if code == 0:
                self._set_status(target['card'] + ' 附加包已移除 · ' + names, 'ok')
                self.switch_page(3)
            else:
                self._set_status(target['card'] + ' 附加包移除失败，见日志', 'error')
                self.switch_page(3)

        self._enqueue(args, '移除附加包 ' + names + ' <- ' + target['card'], done,
                      kind='卸载', target_card=target['card'])

    def _run_skills_only(self, target, skills_dirs, names, skill_mode='auto'):
        """只部署附加技能包，不动指令集（需先注入过任一指令集模板）。
        skill_mode=auto 时沿用目标端上次记录的模式。"""
        args = ['-Target', target['key'], '-SkillsOnly', '-SkillsSource', skills_dirs,
                '-SkillMode', skill_mode,
                # 附属包是纪律型技能，极简模式下也要保持进提示词（靠描述自动触发才有意义）
                '-MenuKeepAdvertised', ';'.join(a['skill_name'] for a in ADDONS)]

        def done(code, tail):
            if code == 0:
                self._set_status(target['card'] + ' 附加包部署完成 · ' + names, 'ok')
                self.switch_page(3)
            else:
                self._set_status(target['card'] + ' 附加包部署失败，见日志', 'error')
                self.switch_page(3)

        self._enqueue(args, '部署附加包 ' + names + ' -> ' + target['card'], done,
                      kind='注入', target_card=target['card'])

    def _auto_reinject(self):
        for target in TARGETS:
            st = read_json(state_path(target['key']))
            if not st:
                continue
            key = resolve_version_key(st.get('versionKey'))
            self._run_install(
                target, key,
                (st.get('versionLabel') or 'V5') + '（自动重注入）',
                not st.get('installedSkills'),
                label='自动注入 ' + target['card'],
                skill_mode=(st.get('skillMode') or 'full'))

    def _reinject(self):
        todo = [(t, read_json(state_path(t['key']))) for t in TARGETS]
        todo = [(t, st) for t, st in todo if st]
        if not todo:
            self._set_status('两端都未注入，先到「模板」页选一张卡部署', 'warn')
            return
        for target, st in todo:
            key = resolve_version_key(st.get('versionKey'))
            self._run_install(target, key,
                              (st.get('versionLabel') or 'V5') + ' 重新注入',
                              not st.get('installedSkills'),
                              label='重新注入 ' + target['card'],
                              skill_mode=(st.get('skillMode') or 'full'))

    def _uninstall(self, target):
        ans = QMessageBox.question(
            self, APP_NAME,
            '确认卸载 ' + target['card'] + ' 的注入？\n\n'
            '· 从 ' + prompt_name(target) + ' 摘除本工具管理段，有备份则还原\n'
            '· 只删除状态清单里记录的本工具技能\n'
            '· 客户端自带技能与用户同名技能不受影响\n'
            '· 若目标文件在部署后被外部改过，会先停下让你确认（不静默覆盖）\n\n'
            '注意：卸载后需重启客户端才生效。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return

        def force():
            def done2(code2, tail2):
                if code2 == 0:
                    self._set_status(target['card'] + ' 已强制卸载（被改过的文件已另存到备份区）', 'ok')
                else:
                    self._set_status(target['card'] + ' 强制卸载失败，见日志', 'error')
                self.switch_page(3)
            self._enqueue(['-Target', target['key'], '-Uninstall', '-Force'],
                          '强制卸载 ' + target['card'], done2,
                          kind='卸载', target_card=target['card'])

        def done(code, tail):
            if code == 0:
                self._set_status(target['card'] + ' 卸载完成', 'ok')
            elif code == 3:
                # 退出码 3 = 注入器发现目标文件被外部改过，停下来等人拍板。
                # 直接还原会把用户手写的内容覆盖掉，所以默认由用户决定。
                drift = [str(x) for x in (tail or []) if ('漂移' in str(x) or '外部改动' in str(x))]
                self._set_status(target['card'] + ' 卸载已暂停：目标文件被外部改过', 'warn')
                msg = '卸载被拦下了。\n\n'
                if drift:
                    msg += (drift[-1][:400] + '\n\n')
                msg += ('目标文件在部署之后被外部改动过，直接还原会把这些改动覆盖掉。\n'
                        '继续卸载会先把改动另存到备份区（backup\\drift\\…）。\n\n'
                        '要强制卸载吗？')
                if QMessageBox.question(self, APP_NAME, msg,
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                    force()
                    return
            else:
                self._set_status(target['card'] + ' 卸载失败，见日志', 'error')
            self.switch_page(3)

        self._enqueue(['-Target', target['key'], '-Uninstall'], '卸载 ' + target['card'], done,
                      kind='卸载', target_card=target['card'])

    def _restart(self, target):
        running = list_procs(target['procs'])
        if running:
            killed = kill_procs(target['procs'])
            time.sleep(0.6)
            self._set_status('已结束 ' + target['card'] + ' 进程' if killed
                             else '结束进程失败，请手动关闭 ' + target['card'],
                             'ok' if killed else 'warn')
        else:
            self._set_status(target['card'] + ' 未在运行', 'ok')

        exe = None
        for p in target['exe_hints']:
            if p and os.path.exists(p):
                exe = p
                break
        if exe:
            try:
                subprocess.Popen([exe])
                self._set_status('已重新启动 ' + target['card'], 'ok')
            except Exception:
                self._set_status('已结束进程，自动启动失败，请手动打开 ' + target['card'], 'warn')
        else:
            self._set_status('未找到 ' + target['card'] + ' 的安装路径，请手动打开', 'warn')

    # ---------------------------------------------------------- 托盘

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon(_make_icon_pm(52, C['ICON_C1'], C['ICON_C2'], '学', 22)))
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        act_show = QAction('显示主窗口', menu)
        act_show.triggered.connect(self._show_from_tray)
        act_inject = QAction('重新注入（按上次模板）', menu)
        act_inject.triggered.connect(lambda: [self.show(), self._reinject()])
        act_check = QAction('自检两端', menu)
        act_check.triggered.connect(lambda: [self.show(), self._check_all()])
        act_quit = QAction('退出', menu)
        act_quit.triggered.connect(self._really_quit)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_inject)
        menu.addAction(act_check)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        """点 ✕：默认隐藏到托盘；设置里开了「直接退出」则真退出。"""
        direct = read_app_config().get('exitToTray') is False
        if self._quit_requested or direct:
            if direct and not self._quit_requested and not self._confirm_quit_while_busy():
                e.ignore()
                return
            e.accept()
            return
        e.ignore()
        self.hide()
        self.tray.showMessage(APP_NAME, '已最小化到托盘，右键图标可退出', QSystemTrayIcon.Information, 2000)

    def _confirm_quit_while_busy(self):
        """有任务在跑时退出会中途断掉 PowerShell（可能留下写了一半的文件），先确认。"""
        if not self._busy:
            return True
        ans = QMessageBox.question(
            self, APP_NAME,
            '有任务正在执行（注入 / 自检 / 卸载）。\n\n'
            '现在退出会中断它，可能留下写了一半的文件。建议等它跑完。\n\n仍要退出吗？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return False
        r = self._runner
        if r is not None:
            r.terminate_tree()
        return True

    def _really_quit(self):
        if not self._confirm_quit_while_busy():
            return
        self._quit_requested = True
        try:
            self.tray.hide()
        except Exception:
            pass
        QApplication.quit()

    def _check_all(self):
        def done_factory(tg):
            def done(code, tail):
                if code == 0:
                    self._set_status(tg['card'] + ' 自检通过（L1/L2/L3）', 'ok')
                elif code == 1:
                    self._set_status(tg['card'] + ' 自检存在未通过项，见日志', 'warn')
                else:
                    self._set_status(tg['card'] + ' 自检完成（退出码 ' + str(code) + '）', 'warn')
            return done

        for target in TARGETS:
            self._enqueue(['-Target', target['key'], '-Check'],
                          '自检 ' + target['card'], done_factory(target),
                          kind='自检', target_card=target['card'])
        self._log_line('')
        self._log_line('[提示] L4 会话层需人工验证：在客户端新开会话，直接给一个技术任务，'
                       '看是否第一行就给交付物（不出现「我不能/无法」类开场)')
        self.switch_page(3)

    def _compose(self):
        """打开任务构建器（只生成文本，不动部署，不联网）。"""
        try:
            TaskComposeDialog(self).exec()
        except Exception as e:
            self._set_status('任务构建窗口打不开：' + str(e), 'error')

    def _versions(self, target):
        """打开版本历史：先让 inject.ps1 刷一份列表 JSON，再弹窗选。"""
        def done(code, tail):
            if code != 0:
                self._set_status(target['card'] + ' 读版本列表失败（退出码 %d），见日志' % code, 'error')
                return
            try:
                VersionsDialog(self, target).exec()
            except Exception as e:
                self._set_status('版本窗口打不开：' + str(e), 'error')

        self._enqueue(['-Target', target['key'], '-ListVersions', '-Json'],
                      '读版本列表 ' + target['card'], done,
                      kind='操作', target_card=target['card'])

    def _probe_all(self):
        """两端一起体检（每端一次真实模型调用；预检不过的那端不会花调用）。"""
        ans = QMessageBox.question(
            self, APP_NAME,
            '对两端各做一次通道体检？\n\n'
            '· 加载层（不联网）：技能会不会被客户端发现\n'
            '· 通道层：真跑一次客户端 CLI，问模型「能看见哪些技能」\n'
            '  两端就是**两次真实模型调用**，会花时间与额度\n\n'
            '配置根里没有 provider 配置、或找不到 CLI 的那一端，会在预检处拦下，不白花调用。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return
        cn = {'pass': '体检放行', 'mismatch': '体检异常（隐藏标记未生效）', 'fail': '体检未送达',
              'unclear': '体检存疑', 'unrun': '体检未执行（缺 CLI）',
              'preflight': '预检没过，已跳过调用', 'timeout': '体检超时'}

        def done_factory(tg):
            def done(code, tail):
                st = read_json(state_path(tg['key'])) or {}
                status = ((st.get('evidence') or {}).get('channelProbe') or {}).get('status')
                self._set_status('%s %s' % (tg['card'], cn.get(status, '体检完成（退出码 %s）' % code)),
                                 'ok' if status == 'pass' else 'warn')
            return done

        for target in TARGETS:
            self._enqueue(['-Target', target['key'], '-Probe'], '通道体检 ' + target['card'],
                          done_factory(target), kind='自检', target_card=target['card'])
        self.switch_page(3)

    def _probe(self, target):
        """通道体检：文件写对了不等于客户端真的读了。

        先跑加载层（不联网），再真跑一次客户端 CLI 问模型「能看见哪些技能」。
        真跑会花时间与额度，所以默认弹窗确认，并且只能一个目标一个目标地跑。
        """
        ans = QMessageBox.question(
            self, APP_NAME,
            '对 ' + target['card'] + ' 做通道体检？\n\n'
            '· 加载层（不联网）：技能会不会被客户端发现（缺 description 会被静默跳过）\n'
            '· 通道层：真跑一次客户端 CLI，问模型「能看见哪些技能」\n'
            '  这是一次**真实模型调用**，会花时间与额度\n\n'
            '答不出来通常意味着：prompt 没送达 / 没配 provider / 客户端没登录。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return

        def done(code, tail):
            st = read_json(state_path(target['key'])) or {}
            cp = ((st.get('evidence') or {}).get('channelProbe') or {})
            status = cp.get('status')
            if status == 'pass':
                self._set_status(target['card'] + ' 体检放行：模型能看到已部署技能', 'ok')
            elif status == 'mismatch':
                self._set_status(target['card'] + ' 体检异常：本该隐藏的模块被模型看到了', 'warn')
            elif status == 'fail':
                self._set_status(target['card'] + ' 体检未送达：模型看不到任何已部署技能', 'error')
            elif status == 'timeout':
                self._set_status(target['card'] + ' 体检超时（已终止子进程），见日志', 'warn')
            elif status == 'unrun':
                self._set_status(target['card'] + ' 体检未执行：找不到客户端 CLI，日志里有手动命令', 'warn')
            else:
                self._set_status(target['card'] + ' 体检完成（退出码 ' + str(code) + '），见日志', 'warn')
            self.switch_page(3)

        self._enqueue(['-Target', target['key'], '-Probe'], '通道体检 ' + target['card'], done,
                      kind='自检', target_card=target['card'])


# ------------------------------------------------------------------ 入口与全局样式

def _apply_global_qss(app):
    """全局暗色 QSS：滚动条 / 复选框 / 表格 / 提示 / 消息框。"""
    app.setStyleSheet(
        # 滚动条：细窄圆角
        'QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }'
        'QScrollBar::handle:vertical { background: ' + C['BORDER'] + '; border-radius: 4px; min-height: 30px; }'
        'QScrollBar::handle:vertical:hover { background: ' + C['TEXT_MUTED'] + '; }'
        'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }'
        'QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }'
        'QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }'
        'QScrollBar::handle:horizontal { background: ' + C['BORDER'] + '; border-radius: 4px; min-width: 30px; }'
        'QScrollBar::handle:horizontal:hover { background: ' + C['TEXT_MUTED'] + '; }'
        'QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }'
        'QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }'
        # 复选框
        'QCheckBox { color: ' + C['TEXT_SECONDARY'] + '; spacing: 7px; }'
        'QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;'
        ' border: 1px solid ' + C['TEXT_MUTED'] + '; background: ' + C['CARD_BG'] + '; }'
        'QCheckBox::indicator:hover { border-color: ' + C['ACCENT'] + '; }'
        'QCheckBox::indicator:checked { background: ' + C['ACCENT'] + '; border-color: ' + C['ACCENT'] + '; }'
        # 消息框
        'QMessageBox { background: ' + C['SURFACE'] + '; }'
        'QMessageBox QLabel { color: ' + C['TEXT_PRIMARY'] + '; font-size: 13px; }'
        'QMessageBox QPushButton { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_PRIMARY'] + ';'
        ' border: 1px solid ' + C['BORDER'] + '; border-radius: 6px; padding: 5px 16px;'
        ' min-width: 64px; }'
        'QMessageBox QPushButton:hover { background: ' + C['CARD_BG_HOVER'] + ';'
        ' border-color: ' + C['ACCENT'] + '; }'
        # 菜单（QMessageBox 等内部会用到）
        'QMenu { background: ' + C['CARD_BG'] + '; color: ' + C['TEXT_PRIMARY'] + ';'
        ' border: 1px solid ' + C['BORDER'] + '; border-radius: 8px; padding: 4px; }'
        'QMenu::item { padding: 6px 20px; border-radius: 6px; }'
        'QMenu::item:selected { background: ' + C['ACCENT_LIGHT'] + '; }'
    )


def _apply_ui_font(app):
    """显式指定支持中文的字体链，避免 Qt 默认字体回退成方块。"""
    from PySide6.QtGui import QFontDatabase
    families = set(QFontDatabase.families())
    for name in ('Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑',
                 'PingFang SC', 'Noto Sans CJK SC', 'Source Han Sans SC',
                 'SimHei', 'SimSun'):
        if name in families:
            f = QFont(name, 9)
            f.setStyleStrategy(QFont.PreferAntialias)
            app.setFont(f)
            return name
    return None


def _apply_ui_font_px(app, px=13):
    """把应用默认字体设为像素字号（真机度量与布局一致，避免 QSS font-size 与 sizeHint 脱节）。"""
    from PySide6.QtGui import QFontDatabase
    families = set(QFontDatabase.families())
    for name in ('Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑',
                 'PingFang SC', 'Noto Sans CJK SC', 'Source Han Sans SC'):
        if name in families:
            f = QFont(name)
            f.setPixelSize(px)
            f.setStyleStrategy(QFont.PreferAntialias)
            app.setFont(f)
            return name
    # 找不到偏好字体（非 Windows/精简环境）：仍强制像素字号，保证布局一致
    f = QFont()
    f.setPixelSize(px)
    app.setFont(f)
    return 'default' 


def version_check():
    """叮版本身份自检：窗口化 exe 没有 stdout，所以把结果写文件。

    干嘛用：拿到一个 exe 先确认「跑的是不是这个构建」—— 升级排查里最常见的问题
    就是“我装的新包怎么还是旧行为”。结果落 work_root/version-check.json。
    """
    res = {
        'name': APP_NAME,
        'version': APP_VERSION,
        'build': APP_BUILD,
        'frozen': bool(getattr(sys, '_MEIPASS', None)),
        'python': sys.version.split()[0],
        'executable': sys.executable,
        'workRoot': work_root(),
        'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    out = os.path.join(work_root(), 'version-check.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
    except Exception:
        return 1
    return 0


def bundle_check():
    """打包自检：确认单文件 exe 内部的脚本与资源都可寻址。写结果到文件后退出。

    要查哪些资源不写死在这里 —— 从 deploy-contract.json 读。
    原因：资源清单写两处就会漏（旧写法是这里一份、实际随包一份，
    增模板时只改一处不会有人报错）。
    """
    res = {'frozen': bool(getattr(sys, '_MEIPASS', None)), 'base': _res(), 'items': {}, 'ok': True}
    cpath = _res('deploy-contract.json')
    contract = None
    try:
        with open(cpath, encoding='utf-8') as fh:
            contract = json.load(fh)
    except Exception as e:
        res['contract'] = '缺失或读不了: %s (%s)' % (e, cpath)
        res['ok'] = False
    if contract is None:
        need, skills_min, addons = [], 0, []
    else:
        res['contract'] = contract.get('version')
        need = list(contract.get('resources') or [])
        lib = contract.get('skillLibrary') or {}
        skills_min = int(lib.get('minSkills') or 0)
        addons = list(contract.get('addons') or [])
    for rel in need:
        p = _res(rel.replace('/', os.sep))
        exists = os.path.exists(p)
        size = os.path.getsize(p) if exists else 0
        res['items'][rel] = {'exists': exists, 'size': size}
        if not exists or size == 0:
            res['ok'] = False
    skills_dir = _res('skills-v4')
    n = 0
    if os.path.isdir(skills_dir):
        for name in os.listdir(skills_dir):
            if os.path.exists(os.path.join(skills_dir, name, 'SKILL.md')):
                n += 1
    res['skills'] = n
    res['skillsMin'] = skills_min
    if n < skills_min:
        res['ok'] = False
    # 附加包单列出来查（它们是后加的功能，缺了就一定失败）
    for addon in addons:
        ok_addon = os.path.exists(os.path.join(skills_dir, addon, 'SKILL.md'))
        res['items']['skills-v4/' + addon] = {'exists': ok_addon}
        if not ok_addon:
            res['ok'] = False

    out = os.path.join(work_root(), 'bundle-check.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return 0 if res['ok'] else 1


def main():
    global _ACTIVE_WINDOW
    if os.environ.get('PJ_BUNDLE_CHECK') == '1':
        return bundle_check()
    if os.environ.get('PJ_VERSION_CHECK') == '1':
        return version_check()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle('Fusion')
    _ico = _res('app.ico')
    if os.path.exists(_ico):
        app.setWindowIcon(QIcon(_ico))      # 任务栏 / Alt-Tab 图标（否则是 PyInstaller 默认图标）
    _apply_ui_font(app)
    _apply_ui_font_px(app, 13)
    _apply_global_qss(app)

    # 主题：配置优先（auto = 跟随系统）
    cfg = read_json(tool_config_path()) or {}
    mode = cfg.get('theme') if cfg.get('theme') in ('dark', 'light', 'auto') else 'dark'
    set_theme(_system_theme() if mode == 'auto' else mode)
    _apply_global_qss(app)

    if not cfg.get('skipAgreement') and os.environ.get('PJ_SKIP_AGREEMENT') != '1':
        dlg = AgreementDialog()
        if dlg.exec() != QDialog.Accepted:
            return 0

    _ACTIVE_WINDOW = MainWindow()
    _ACTIVE_WINDOW.show()

    # 系统主题变更监听：auto 模式下跟随切换
    def _on_sys_theme(actual):
        if _cfg_theme() == 'auto' and _ACTIVE_WINDOW is not None:
            _ACTIVE_WINDOW.apply_theme('auto')
    global _THEME_FILTER
    _THEME_FILTER = _install_theme_listener(_on_sys_theme)   # 持引用，防 GC
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
