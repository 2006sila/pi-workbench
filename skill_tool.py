#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能库维护工具 —— 加技能 / 移除 / 登记类目 / 体检。

设计分工（照抄 alice 的原则）：**脚本只做机械动作与机械校验，不做语义猜测**。
类目判断由人（或 AI）决定，脚本负责：校验 frontmatter 合规 → 落库 → 登记类目 → 报开销与问题。
类目给错了、描述太短、名字不合规，脚本拒绝执行并说明原因，不替你猜。

用法：
  py -X utf8 skill_tool.py check                    # 体检整个技能库（默认动作）
  py -X utf8 skill_tool.py list                     # 类目与登记情况
  py -X utf8 skill_tool.py gen                      # 按技能自己的声明重排类目表（机械动作）
  py -X utf8 skill_tool.py gen --check              # 只读校验三方一致（可进 CI）
  py -X utf8 skill_tool.py add <目录|zip> --category <类目> [--name X] [--desc X] [--dry-run] [--force]
  py -X utf8 skill_tool.py add --batch <目录> --category <类目> [--dry-run]
  py -X utf8 skill_tool.py register <技能名> --category <类目>    # 只改登记，不动文件
  py -X utf8 skill_tool.py remove <技能名> [--yes]               # 移到 skills-v4/_removed/
  py -X utf8 skill_tool.py new-category <类目名> --when "<何时进这类>"

退出码：0 正常；1 体检发现问题；2 参数或校验不通过。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(ROOT, 'skills-v4')
CATS_PATH = os.path.join(ROOT, 'skill-categories.json')
REMOVED_DIR = os.path.join(SKILLS_DIR, '_removed')

# Agent Skills 规范 / Pi 文档里的硬规则
NAME_MAX = 64
DESC_MAX = 1024
NAME_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
# 项目自定阈值（不是规范，是提示词预算）
DESC_WARN_HIGH = 200      # 完整模式下每条描述每轮都进系统提示词
DESC_WARN_LOW = 15        # 太短则路由信息不足

# 技能自己声明类目：写在 frontmatter 的 metadata 下（Agent Skills 规范允许
# metadata 放任意键值），也兼容顶层同名键。这样「这个技能属于哪一类」跟着技能走，
# 加技能时顺手写下，不用回头改 skill-categories.json —— 漏登记就是这么来的。
CLASS_KEY = 'x-pj-class'


# ---------------------------------------------------------------- 基础

def est_tokens(s: str) -> float:
    """CJK 1 字符 ≈ 1 token，ASCII 4 字符 ≈ 1 token"""
    cjk = sum(1 for c in s if '\u3000' <= c <= '\u9fff' or '\uff00' <= c <= '\uffef')
    return cjk + (len(s) - cjk) / 4.0


def read(p: str) -> str:
    with open(p, encoding='utf-8', errors='replace') as fh:
        return fh.read()


def parse_frontmatter(text: str):
    """返回 (frontmatter 文本, 正文)。无 frontmatter 时返回 (None, text)。"""
    m = re.match(r'^\ufeff?---\r?\n(.*?)\r?\n---[ \t]*\r?\n?', text, re.S)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def fm_value(fm: str, key: str) -> str:
    """读 frontmatter 字段。支持四种写法：行内 / 双引号 / 单引号 / YAML 块标量（| 或 >）。"""
    if fm is None:
        return ''
    m = re.search(r'^' + re.escape(key) + r':[ \t]*(.*)$', fm, re.M)
    if not m:
        return ''
    first = m.group(1).strip()
    if first in ('|', '>', '|-', '>-', '|+', '>+'):
        parts = []
        for ln in fm[m.end():].splitlines():
            if not ln.strip():
                continue
            if re.match(r'^[ \t]+', ln):
                parts.append(ln.strip())
            else:
                break
        return ' '.join(parts).strip()
    if len(first) >= 2 and first[0] == first[-1] and first[0] in ('"', "'"):
        return first[1:-1]
    return first


def fm_keys(fm: str):
    return set(re.findall(r'^([A-Za-z][A-Za-z0-9_-]*):', fm or '', re.M))


def _unquote(v: str) -> str:
    v = (v or '').strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def fm_meta(fm: str, key: str) -> str:
    """读 metadata: 块下的子键（只支持块形式，不支持内联 {a: b}）。

    注意行尾：技能库的 SKILL.md 是 CRLF，用 `^metadata:$` 配 re.M 会永远匹配不上
    —— `$` 停在 `\n` 前，行尾那个 `\r` 就成了多余字符（踩过一次：声明全读成空，
    gen 把类目表清空）。
    """
    if not fm:
        return ''
    m = re.search(r'^metadata:[ \t]*(?:\r?\n|$)', fm, re.M)
    if not m:
        return ''
    for ln in fm[m.end():].splitlines():
        if not re.match(r'^[ \t]+', ln):
            break
        mm = re.match(r'^[ \t]+' + re.escape(key) + r':[ \t]*(.*)$', ln)
        if mm:
            return _unquote(mm.group(1))
    return ''


def skill_class(fm: str) -> str:
    """技能自己声明的类目：metadata.x-pj-class 优先，兼容顶层 x-pj-class。"""
    return fm_meta(fm, CLASS_KEY) or fm_value(fm, CLASS_KEY)


def set_skill_class(path: str, value: str = '', remove: bool = False) -> bool:
    """在 SKILL.md frontmatter 里写入/更新/删除 metadata.x-pj-class。

    纯追加式改写：只动 frontmatter 里与这个键相关的字节，其余字节（含 BOM、
    正文、既有行尾）原样保留 —— 技能库是 git 内容，diff 越小越好审。
    返回是否改动了文件。
    """
    with open(path, 'rb') as fh:
        raw = fh.read()
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = raw.decode('utf-8-sig')
    m = re.match(r'^---[ \t]*(?:\r?\n)', text)
    if not m:
        return False
    close = re.search(r'^---[ \t]*(?:\r?\n|$)', text[m.end():], re.M)
    if not close:
        return False
    fm_start = m.end()
    fm_end = m.end() + close.start()
    fm = text[fm_start:fm_end]

    child = re.compile(r'^([ \t]+)' + re.escape(CLASS_KEY) + r':[^\r\n]*(\r?\n|$)', re.M)
    top = re.compile(r'^' + re.escape(CLASS_KEY) + r':[^\r\n]*(\r?\n|$)', re.M)
    meta = re.search(r'^metadata:[ \t]*(?:\r?\n|$)', fm, re.M)

    # 有两种写法：顶层 x-pj-class（旧）与 metadata 下（规范）。
    # 哪个已存在就改哪个 —— 不把旧写法升级成新写法，避免留下两份。
    if top.search(fm):
        if remove:
            new_fm = top.sub('', fm, count=1)
        else:
            new_fm = top.sub(lambda mm: CLASS_KEY + ': ' + value + (mm.group(1) or ''), fm, count=1)
    elif child.search(fm):
        if remove:
            new_fm = child.sub('', fm, count=1)
            # metadata 块被清空 → 连块一起收掉，别留空壳
            mm = re.search(r'^metadata:[ \t]*(?:\r?\n|$)', new_fm, re.M)
            if mm:
                rest = new_fm[mm.end():]
                kept = []
                for ln in rest.splitlines(keepends=True):
                    if re.match(r'^[ \t]+', ln):
                        kept.append(ln)
                    else:
                        break
                if not any(x.strip() for x in kept):
                    new_fm = new_fm[:mm.start()] + rest[len(''.join(kept)):]
        else:
            new_fm = child.sub(lambda mm: mm.group(1) + CLASS_KEY + ': ' + value + (mm.group(2) or ''), fm, count=1)
    elif remove:
        return False
    elif meta:
        nl = '\r\n' if meta.group(0).endswith('\r\n') else '\n'
        new_fm = fm[:meta.end()] + '  ' + CLASS_KEY + ': ' + value + nl + fm[meta.end():]
    else:
        nl = '\r\n' if '\r\n' in fm else '\n'
        # 插在最后一行内容之后：先去掉末尾空行（frontmatter 结尾的空白无信息量），
        # 再补一个换行把 metadata 块接上。
        core = fm.rstrip()
        new_fm = core + nl + 'metadata:' + nl + '  ' + CLASS_KEY + ': ' + value + nl

    if new_fm == fm:
        return False
    out = text[:fm_start] + new_fm + text[fm_end:]
    data = (b'\xef\xbb\xbf' if bom else b'') + out.encode('utf-8')
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, path)
    return True


def load_cats():
    with open(CATS_PATH, encoding='utf-8') as fh:
        return json.load(fh)


def save_cats(d):
    tmp = CATS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    os.replace(tmp, CATS_PATH)


def registered(d):
    """{模块名: 类目名}"""
    out = {}
    for c in d['categories']:
        for m in c.get('modules', []):
            out[m] = c['name']
    return out


def disk_skills():
    """磁盘上有 SKILL.md 的技能名（跳过 _removed 等下划线目录）"""
    if not os.path.isdir(SKILLS_DIR):
        return []
    out = []
    for x in sorted(os.listdir(SKILLS_DIR)):
        if x.startswith('_'):
            continue
        if os.path.isfile(os.path.join(SKILLS_DIR, x, 'SKILL.md')):
            out.append(x)
    return out


def skill_info(name: str):
    """读一个技能的 name / description / 问题列表"""
    p = os.path.join(SKILLS_DIR, name, 'SKILL.md')
    text = read(p)
    fm, body = parse_frontmatter(text)
    return {
        'dir': name,
        'path': p,
        'fm': fm,
        'body': body,
        'size': os.path.getsize(p),
        'name': fm_value(fm, 'name'),
        'desc': fm_value(fm, 'description'),
        'class': skill_class(fm),
        'keys': fm_keys(fm),
        'text': text,
    }


def menu_line(desc: str, limit: int = 72) -> str:
    """菜单技能里那一行长什么样（与 inject.ps1 的 Shorten-Desc 同口径）"""
    d = re.sub(r'\s+', ' ', desc or '').strip()
    d = re.split(r'\s*(?:触发词|触发器)[:：]', d)[0].strip()
    parts = re.split(r'(?<=[。；;])', d)
    if parts and len(parts[0]) <= limit:
        d = parts[0]
    if len(d) > limit:
        cut = d[:limit]
        for sep in (' ', '，', '、'):
            i = cut.rfind(sep)
            if i > limit * 0.5:
                cut = cut[:i]
                break
        d = cut.rstrip() + '…'
    return d


# ---------------------------------------------------------------- 校验

def validate_name(name: str, dirname: str, others: set):
    """返回 (errors, warnings)。dirname 传最终落库的目录名（与 name 一致时不再报不一致）。"""
    err, warn = [], []
    if not name:
        err.append('frontmatter 缺 name')
        return err, warn
    if len(name) > NAME_MAX:
        err.append('name 超长（%d > %d）' % (len(name), NAME_MAX))
    if not NAME_RE.match(name):
        err.append('name 不合规（只允许小写字母/数字/单连字符，且不能首尾或连续连字符）：%s' % name)
    if dirname and name != dirname:
        warn.append('name(%s) 与目录名(%s) 不一致 —— Pi 不报错，但其他 Agent Skills 实现可能强制要求一致' % (name, dirname))
    if name in others:
        err.append('与已有技能重名：%s' % name)
    return err, warn


def validate_desc(desc: str):
    err, warn = [], []
    if not desc:
        err.append('frontmatter 缺 description（Pi 会直接不加载该技能）')
        return err, warn
    if len(desc) > DESC_MAX:
        err.append('description 超长（%d > %d，Pi 上限）' % (len(desc), DESC_MAX))
    elif len(desc) > DESC_WARN_HIGH:
        warn.append('description 偏长（%d 字符，完整模式下每轮都进提示词，约 %d tokens）'
                    % (len(desc), round(est_tokens(desc))))
    if len(desc) < DESC_WARN_LOW:
        warn.append('description 过短（%d 字符），模型难以据此路由' % len(desc))
    return err, warn


def check_links(skill_dir: str, max_report: int = 5):
    """相对 md 链接可达性。
    两道过滤，避免把代码片段当成链接（上一版正则扫代码导致过大量误报）：
      ① 先剔除围栏代码块与行内代码；
      ② 目标必须「像路径」——含 / 或 .，且不含括号引号逗号空格。
    """
    broken = []
    total = 0
    for r, _d, files in os.walk(skill_dir):
        for f in files:
            if not f.endswith('.md'):
                continue
            p = os.path.join(r, f)
            text = read(p)
            text = re.sub(r'```.*?```', '', text, flags=re.S)   # 围栏代码块
            text = re.sub(r'~~~.*?~~~', '', text, flags=re.S)
            text = re.sub(r'`[^`\n]*`', '', text)              # 行内代码
            for m in re.finditer(r'\]\(([^)\s]+)\)', text):
                t = m.group(1)
                if t.startswith(('http://', 'https://', 'mailto:', '#', 'data:')):
                    continue
                if not re.search(r'[/.]', t):        # 不像路径
                    continue
                if re.search(r'[\[\]"\'`,]', t):      # 像代码索引 / 带引号的标识符
                    continue
                path = t.split('#')[0]
                if not path or os.path.isabs(path) or path.startswith(('~', '$')):
                    continue
                total += 1
                dst = os.path.normpath(os.path.join(r, path.replace('/', os.sep)))
                if not os.path.exists(dst):
                    broken.append((os.path.relpath(p, ROOT).replace(os.sep, '/'), t))
    return total, broken[:max_report], len(broken)


# ---------------------------------------------------------------- 命令

def cmd_list(args):
    d = load_cats()
    reg = registered(d)
    disk = set(disk_skills())
    print('类目表：%s' % os.path.relpath(CATS_PATH, ROOT))
    print('磁盘技能 %d 个 ｜ 已登记 %d 个 ｜ 未登记 %d 个'
          % (len(disk), len(disk & set(reg)), len(disk - set(reg))))
    print()
    for c in d['categories']:
        mods = [m for m in c.get('modules', []) if m in disk]
        missing = [m for m in c.get('modules', []) if m not in disk]
        print('· %-14s %2d 个' % (c['name'], len(mods)))
        if c.get('when'):
            print('    when: %s' % c['when'])
        if missing:
            print('    ⚠ 登记了但磁盘没有：%s' % '、'.join(missing))
    unreg = sorted(disk - set(reg))
    if unreg:
        print()
        print('未登记（极简模式下会落到「其他」）：%d 个' % len(unreg))
        for m in unreg:
            print('   %s' % m)
        print('   → 收进类目：py -X utf8 skill_tool.py register <名字> --category <类目>')
    return 0


def cmd_check(args):
    d = load_cats()
    reg = registered(d)
    disk = disk_skills()
    errors, warnings = [], []
    total_desc_tokens = 0.0
    total_links = total_broken = 0

    print('体检 %d 个技能（%s）' % (len(disk), os.path.relpath(SKILLS_DIR, ROOT)))
    print()

    for name in disk:
        info = skill_info(name)
        e, w = validate_name(info['name'], name, set())
        errors += ['%s：%s' % (name, x) for x in e]
        warnings += ['%s：%s' % (name, x) for x in w]
        e, w = validate_desc(info['desc'])
        errors += ['%s：%s' % (name, x) for x in e]
        warnings += ['%s：%s' % (name, x) for x in w]
        if info['fm'] is None:
            errors.append('%s：没有 frontmatter 块' % name)
        total_desc_tokens += est_tokens(info['desc'])

    # 重名（磁盘 vs 磁盘 frontmatter name）
    by_declared = {}
    for name in disk:
        i = skill_info(name)
        by_declared.setdefault(i['name'], []).append(name)
    for k, v in by_declared.items():
        if k and len(v) > 1:
            errors.append('声明的 name 重复：%s → %s' % (k, '、'.join(v)))

    # 防串稿：description / 正文都不应该与别的技能完全一致。
    # 两份说明一样时模型无法判断该用哪个，正文一样说明是复制来没改写。
    # 用 skill_info 的 desc（已经走 fm_value，支持 | / > 块标量）——
    # 裸正则会把块标量技能的 description 读成「|」，于是全被误判成重复。
    # 这两项只算提示：同域多技能边界相近难免，拦下来会逼人把描述改差。
    by_desc, by_body = {}, {}
    for name in disk:
        i = skill_info(name)
        desc = (i['desc'] or '').strip()
        if desc:
            by_desc.setdefault(desc, []).append(name)
        body = (i['body'] or '').strip()
        if body:
            norm = re.sub(r'\s+', ' ', body)
            by_body.setdefault(hashlib.sha256(norm.encode('utf-8')).hexdigest(), []).append(name)
    for desc, names in by_desc.items():
        if len(names) > 1:
            warnings.append('description 与其它技能重复：%s → 「%s」；模型无法区分，建议写明各自边界'
                            % ('、'.join(names), desc[:48]))
    for names in by_body.values():
        if len(names) > 1:
            warnings.append('正文与其它技能完全一致：%s → 疑似复制未改写' % '、'.join(names))

    # 登记情况
    unreg = sorted(set(disk) - set(reg))
    if unreg:
        warnings.append('未登记类目（极简模式会落到「其他」）：%s' % '、'.join(unreg))
    dangling = sorted(set(reg) - set(disk))
    if dangling:
        errors.append('登记了但磁盘没有：%s' % '、'.join(dangling))
    # 技能自报类目（真源）↔ 类目表（生成物）
    no_decl, off_decl = [], []
    for name in disk:
        dc = skill_info(name)['class']
        if not dc:
            no_decl.append(name)
        elif name in reg and reg[name] != dc:
            off_decl.append('%s（声明「%s」≠ 登记「%s」）' % (name, dc, reg[name]))
    if no_decl:
        warnings.append('未在 frontmatter 声明类目（metadata.x-pj-class，共 %d 个）：%s'
                        % (len(no_decl), '、'.join(no_decl[:8]) + ('…' if len(no_decl) > 8 else '')))
    if off_decl:
        warnings.append('frontmatter 与类目表不一致，跑 gen 对齐：%s' % '；'.join(off_decl))

    # 链接可达性
    broken_detail = []
    if not args.no_links:
        for name in disk:
            t, show, n = check_links(os.path.join(SKILLS_DIR, name), max_report=args.limit)
            total_links += t
            total_broken += n
            broken_detail += show

    # 输出
    print('── 硬规则（Pi / Agent Skills 规范）──')
    print('  name 合法且无重名          %s' % ('✓' if not any('name' in e for e in errors) else '✗'))
    print('  description 存在且 ≤%d     %s' % (DESC_MAX, '✓' if not any('description' in e for e in errors) else '✗'))
    print('  frontmatter 完整           %s' % ('✓' if not any('frontmatter' in e for e in errors) else '✗'))
    print('  登记与磁盘一致             %s' % ('✓' if not dangling else '✗'))
    dup_n = sum(1 for x in warnings
                if x.startswith('description 与其它技能重复') or x.startswith('正文与其它技能完全一致'))
    print('  description / 正文不串稿   %s' % ('✓' if not dup_n else '⚠ %d 组' % dup_n))
    print()
    print('── 提示词预算 ──')
    print('  描述合计 %.0f tokens／每轮（完整模式：65 条全进系统提示词）' % total_desc_tokens)
    print('  极简模式：只 1 条菜单技能 ≈90 tokens／每轮')
    if not args.no_links:
        print()
        print('── 相对链接 ──')
        print('  检查 %d 条，失效 %d 条' % (total_links, total_broken))
        for f, link in broken_detail:
            print('    ✗ %s -> %s' % (f, link))
        if total_broken > len(broken_detail):
            print('    … 另有 %d 条' % (total_broken - len(broken_detail)))

    if warnings:
        print()
        print('── 提示（%d）──' % len(warnings))
        for x in warnings[: args.limit]:
            print('  ⚠ %s' % x)
        if len(warnings) > args.limit:
            print('  … 另有 %d 条，用 --limit 放大' % (len(warnings) - args.limit))
    if errors:
        print()
        print('── 错误（%d）──' % len(errors))
        for x in errors:
            print('  ✗ %s' % x)
    print()
    print('结论：%s' % ('通过' if not errors else '有问题（%d 个错误）' % len(errors)))
    return 1 if errors else 0


def cmd_gen(args):
    """按技能自己声明的类目重排 skill-categories.json。

    真源是 `skills-v4/<id>/SKILL.md` 的 metadata.x-pj-class；类目表是**生成物**。
    已有类目的顺序与块位置保留，新技能追加到所属类目末尾 —— 不做字母重排，
    免得每次生成都把人工挑过的顺序冲掉。
    脚本不做语义猜测：声明缺失、类目不存在、登记了但磁盘没有，一律停下报，
    不替你归类。
    """
    d = load_cats()
    cat_names = [c['name'] for c in d['categories']]
    disk = disk_skills()
    json_map = registered(d)

    declared = {n: skill_class(skill_info(n)['fm']) for n in disk}

    # 可自动修复：frontmatter 没写，但类目表里已登记且类目有效 → 把登记回填进 frontmatter
    backfill = sorted(n for n in disk if not declared[n] and json_map.get(n) in cat_names)
    # 需人工：从没登记过
    no_field = sorted(n for n in disk if not declared[n] and n not in json_map)
    # 需人工：声明了不存在的类目
    unknown = sorted((n, declared[n]) for n in disk if declared[n] and declared[n] not in cat_names)
    # 需人工：类目表登记了，磁盘上没这个技能
    dangling = sorted(set(json_map) - set(disk))
    # frontmatter 与类目表声明不一致（以 frontmatter 为准）
    mismatch = sorted((n, declared[n], json_map[n]) for n in disk
                      if declared[n] and n in json_map and json_map[n] != declared[n])

    block = bool(no_field or unknown or dangling)
    drift = bool(block or mismatch or backfill)

    print('扫描 %s ｜ 磁盘 %d 个技能 ｜ 类目表 %d 个类目／已登记 %d 个'
          % (os.path.relpath(SKILLS_DIR, ROOT), len(disk), len(cat_names), len(json_map)))
    print()
    print('  已声明类目          %d' % sum(1 for n in disk if declared[n]))
    print('  可回填（表里有登记）  %d' % len(backfill))
    print('  从未登记            %d' % len(no_field))
    print('  声明了不存在的类目    %d' % len(unknown))
    print('  表里有但磁盘没有      %d' % len(dangling))
    print('  两边声明不一致        %d' % len(mismatch))
    if backfill:
        print()
        print('── 可从类目表回填 ──')
        for n in backfill:
            print('  %s → %s' % (n, json_map[n]))
    for label, items in (('从未登记（先决定它属于哪类）', no_field),
                         ('声明了类目表里没有的类目', unknown),
                         ('类目表里登记了但磁盘没有', dangling),
                         ('frontmatter 与类目表不一致（以 frontmatter 为准）', mismatch)):
        if items:
            print()
            print('── %s ──' % label)
            for it in items:
                print('  %s' % ('、'.join(str(x) for x in it) if isinstance(it, tuple) else it))

    if args.check:
        print()
        if block:
            print('结论：有问题，需人工处置（脚本不猜类目）')
            return 1
        if drift:
            print('结论：能自动对齐，跑 `py -X utf8 skill_tool.py gen` 即可')
            return 1
        print('结论：三方一致（frontmatter × 类目表 × 磁盘）')
        return 0

    if block:
        print()
        print('✗ 不动文件。先处置上面的条目：')
        if no_field:
            print('   · 给技能写入声明（或直接登记）：')
            print('     py -X utf8 skill_tool.py register %s --category "<类目>"' % no_field[0])
        if unknown:
            print('   · 类目不存在：改 frontmatter，或先 new-category 建类目')
        if dangling:
            print('   · 表里有磁盘没有：技能改名/删除后忘了同步，用 remove 或手改类目表')
        return 2

    # 回填 + 重排
    filled = 0
    for n in backfill:
        if set_skill_class(os.path.join(SKILLS_DIR, n, 'SKILL.md'), json_map[n]):
            declared[n] = json_map[n]
            filled += 1
    want = {}
    for n in disk:
        if declared[n]:
            want.setdefault(declared[n], []).append(n)
    # 防守：一个声明都读不到却要重写类目表 → 一定会把表清空。宁可停下。
    if disk and not want:
        print()
        print('✗ 读不到任何技能类目声明，拒绝重写类目表（会把它清空）。')
        print('  先查 skills-v4/*/SKILL.md 的 frontmatter 是否被改坏。')
        return 2

    changed = []
    for c in d['categories']:
        cur = [m for m in c.get('modules', []) if m in want.get(c['name'], [])]
        new = [m for m in want.get(c['name'], []) if m not in cur]
        mods = cur + new
        if c.get('modules') != mods:
            c['modules'] = mods
            changed.append((c['name'], len(mods)))

    if filled or changed:
        save_cats(d)
    print()
    if filled:
        print('已回填 frontmatter 声明 %d 个技能' % filled)
    for name, n in changed:
        print('类目表已同步：「%s」%d 个' % (name, n))
    if not filled and not changed:
        print('无需改动。')
    else:
        print('提醒：技能库是 git 内容，记得提交。')
    return 0


def _find_skill_root(base: str):
    """在解包/给定目录里找含 SKILL.md 的那个技能目录（支持纵深一层）"""
    if os.path.isfile(os.path.join(base, 'SKILL.md')):
        return base
    for r, dirs, files in os.walk(base):
        dirs[:] = [x for x in dirs if not x.startswith('.')]
        if 'SKILL.md' in files:
            return r
    return None


def _install_one(src: str, category: str, name_override: str, desc_override: str,
                 dry: bool, force: bool, cats: dict):
    """校验并落库一个技能目录；返回 (ok, message, registered_name)"""
    root = _find_skill_root(src)
    if not root:
        return False, '没找到 SKILL.md', None
    text = read(os.path.join(root, 'SKILL.md'))
    fm, _body = parse_frontmatter(text)
    if fm is None:
        return False, 'SKILL.md 没有 frontmatter', None
    declared = fm_value(fm, 'name')
    src_dir = os.path.basename(root.rstrip('/\\'))
    # 落库目录名始终 = 最终技能名（优先 --name，否则 frontmatter 声明的，再不行用源目录名）。
    # 这样目录名天然一致，不用担心其他 Agent Skills 实现强制要求一致。
    name = name_override or declared or src_dir
    desc = desc_override or fm_value(fm, 'description')

    notes = []
    if name_override and declared and name_override != declared:
        notes.append('已按 --name 落库为 %s，但 frontmatter 里仍是 name: %s —— 两者不一致会让 /skill: 用不了' % (name, declared))
    elif declared and declared != src_dir:
        notes.append('源目录名 %s ≠ 声明名 %s，已按声明名落库' % (src_dir, declared))

    err, warn = validate_name(name, name, set(disk_skills()))
    e, w = validate_desc(desc)
    err += e
    warn += w
    if err:
        return False, '校验不通过：\n      ' + '\n      '.join(err), None

    dest = os.path.join(SKILLS_DIR, name)
    if os.path.exists(dest) and not force:
        return False, '目标已存在：skills-v4/%s（要覆盖加 --force）' % name, None

    info = []
    src_txt = os.path.relpath(root, ROOT) if root.startswith(ROOT) else root
    info.append('来源    %s' % src_txt)
    info.append('落库    skills-v4/%s' % name)
    info.append('类目    %s' % category)
    info.append('描述    %d 字符 ≈ %d tokens/轮（完整模式）' % (len(desc), round(est_tokens(desc))))
    info.append('菜单行  %s' % menu_line(desc))
    for x in notes + warn:
        info.append('提示    %s' % x)
    if dry:
        print('  [dry-run] ' + '\n            '.join(info))
        return True, '', name

    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(root, dest)
    # 声明写回技能自己身上（类目表是生成物，真源在 SKILL.md）
    set_skill_class(os.path.join(dest, 'SKILL.md'), category)
    print('  ✓ %s' % name)
    for x in info[1:]:
        print('    %s' % x)
    return True, '', name


def cmd_add(args):
    cats = load_cats()
    names = [c['name'] for c in cats['categories']]
    if args.category not in names:
        print('类目不存在：%s' % args.category)
        print('现有类目：%s' % '、'.join(names))
        print('要新建：py -X utf8 skill_tool.py new-category "<名字>" --when "<何时进这类>"')
        return 2

    src = args.source
    tmp = None
    if not os.path.exists(src):
        print('来源不存在：%s' % src)
        return 2
    if os.path.isfile(src) and src.lower().endswith('.zip'):
        tmp = tempfile.mkdtemp(prefix='skilladd-')
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        src = tmp

    try:
        added = []
        if args.batch:
            cands = [os.path.join(src, x) for x in sorted(os.listdir(src))]
            cands = [c for c in cands if os.path.isdir(c)]
            if not cands:
                print('目录下没有子目录：%s' % src)
                return 2
            print('批量：发现 %d 个子目录' % len(cands))
            for c in cands:
                ok, msg, nm = _install_one(c, args.category, None, None, args.dry_run, args.force, cats)
                if ok:
                    added.append(nm)
                else:
                    print('  ✗ %s：%s' % (os.path.basename(c), msg))
        else:
            ok, msg, nm = _install_one(src, args.category, args.name, args.desc,
                                       args.dry_run, args.force, cats)
            if not ok:
                print('✗ %s' % msg)
                return 2
            added.append(nm)

        # 登记类目
        if added and not args.dry_run:
            target = next(c for c in cats['categories'] if c['name'] == args.category)
            for nm in added:
                for c in cats['categories']:
                    if nm in c.get('modules', []) and c is not target:
                        c['modules'].remove(nm)
                if nm not in target['modules']:
                    target['modules'].append(nm)
            save_cats(cats)
            print()
            print('已登记到「%s」：%s' % (args.category, '、'.join(added)))
            print('提醒：技能库是 git 内容，记得提交；类目表已同步（frontmatter 也写了声明）。')
        elif added:
            print()
            print('[dry-run] 未落库、未登记。去掉 --dry-run 执行。')
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def cmd_register(args):
    cats = load_cats()
    names = [c['name'] for c in cats['categories']]
    if args.category not in names:
        print('类目不存在：%s（现有：%s）' % (args.category, '、'.join(names)))
        return 2
    disk = disk_skills()
    miss = [n for n in args.skill if n not in disk]
    if miss:
        print('磁盘上没有这些技能：%s' % '、'.join(miss))
        return 2
    target = next(c for c in cats['categories'] if c['name'] == args.category)
    for nm in args.skill:
        for c in cats['categories']:
            if nm in c.get('modules', []):
                c['modules'].remove(nm)
        if nm not in target['modules']:
            target['modules'].append(nm)
        # 声明写回技能自己身上
        set_skill_class(os.path.join(SKILLS_DIR, nm, 'SKILL.md'), args.category)
    save_cats(cats)
    print('已登记 %d 个到「%s」（frontmatter 声明已同步）' % (len(args.skill), args.category))
    return 0


def cmd_remove(args):
    cats = load_cats()
    disk = disk_skills()
    miss = [n for n in args.skill if n not in disk]
    if miss:
        print('磁盘上没有：%s' % '、'.join(miss))
        return 2
    if not args.yes:
        print('将把以下技能移到 skills-v4/_removed/（可手工移回，不会删文件）：')
        for n in args.skill:
            print('   %s' % n)
        print('确认加 --yes')
        return 2
    os.makedirs(REMOVED_DIR, exist_ok=True)
    for nm in args.skill:
        shutil.move(os.path.join(SKILLS_DIR, nm), os.path.join(REMOVED_DIR, nm))
        for c in cats['categories']:
            if nm in c.get('modules', []):
                c['modules'].remove(nm)
        set_skill_class(os.path.join(REMOVED_DIR, nm, 'SKILL.md'), remove=True)
        print('已移出：%s → skills-v4/_removed/%s' % (nm, nm))
    save_cats(cats)
    print('类目表已同步。记得提交 git。')
    return 0


def cmd_new_category(args):
    cats = load_cats()
    if any(c['name'] == args.name for c in cats['categories']):
        print('类目已存在：%s' % args.name)
        return 2
    cats['categories'].append({'name': args.name, 'when': args.when or '', 'modules': []})
    save_cats(cats)
    print('已新增类目「%s」' % args.name)
    if not args.when:
        print('提醒：没写 --when，菜单里该类目会缺「何时进这类」说明。')
    return 0


def main():
    ap = argparse.ArgumentParser(description='技能库维护工具', formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('check', help='体检整个技能库')
    p.add_argument('--limit', type=int, default=25, help='最多显示多少条提示（默认 25）')
    p.add_argument('--no-links', action='store_true', help='跳过相对链接检查')
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('list', help='类目与登记情况')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('gen', help='按技能声明的类目重排/校验类目表')
    p.add_argument('--check', action='store_true', help='只读校验三方一致（不一致退出码 1）')
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser('add', help='加技能（目录或 zip）')
    p.add_argument('source', nargs='?', help='技能目录或 zip；--batch 时是父目录')
    p.add_argument('--batch', action='store_true', help='把 source 下每个子目录都当成一个技能')
    p.add_argument('--category', required=True, help='类目（必须是 skill-categories.json 里已有的）')
    p.add_argument('--name', help='覆盖技能名（默认用 frontmatter 的 name）')
    p.add_argument('--desc', help='覆盖描述（默认用 frontmatter 的 description）')
    p.add_argument('--dry-run', action='store_true', help='只校验不落库')
    p.add_argument('--force', action='store_true', help='目标已存在时覆盖')
    p.set_defaults(func=cmd_add)

    p = sub.add_parser('register', help='只登记类目，不动文件')
    p.add_argument('skill', nargs='+')
    p.add_argument('--category', required=True)
    p.set_defaults(func=cmd_register)

    p = sub.add_parser('remove', help='把技能移到 skills-v4/_removed/')
    p.add_argument('skill', nargs='+')
    p.add_argument('--yes', action='store_true')
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser('new-category', help='新增一个类目')
    p.add_argument('name')
    p.add_argument('--when', help='何时进这类（写进菜单）')
    p.set_defaults(func=cmd_new_category)

    args = ap.parse_args()
    if not getattr(args, 'func', None):
        args = ap.parse_args(['check'])
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
