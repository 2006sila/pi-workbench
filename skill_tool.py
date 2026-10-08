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
  py -X utf8 skill_tool.py contract                # 校验部署契约 ↔ 仓库实际状态（随包资源/spec/标记块/退出码/溯源）
  py -X utf8 skill_tool.py pack [--out X.zip] [--verify X.zip]   # 技能库打包（含 SHA-256 清单）/ 校验包
  py -X utf8 skill_tool.py notice [--write]        # 生成/校验 THIRD-PARTY-NOTICES.md（第三方许可清单）
  py -X utf8 skill_tool.py add <目录|zip> --category <类目> [--name X] [--desc X] [--dry-run] [--force]
  py -X utf8 skill_tool.py add --batch <目录> --category <类目> [--dry-run]
  py -X utf8 skill_tool.py register <技能名> --category <类目>    # 只改登记，不动文件
  py -X utf8 skill_tool.py remove <技能名> [--yes]               # 移到 skills-v4/_removed/
  py -X utf8 skill_tool.py new-category <类目名> --when "<何时进这类>"
  py -X utf8 skill_tool.py rate --seed               # 按可测量信号生成评分表（菜单排序列）
  py -X utf8 skill_tool.py rate --list               # 看评分与分片阈值
  py -X utf8 skill_tool.py rate --set <技能> <1-10>  # 人工评分（--seed 不覆盖它）
  py -X utf8 skill_tool.py rate --check              # 校验评分覆盖（可进 CI）

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
CONTRACT_PATH = os.path.join(ROOT, 'deploy-contract.json')
SPEC_PATH = os.path.join(ROOT, 'bj_tool.spec')
INJECT_PATH = os.path.join(ROOT, 'inject.ps1')
BJTOOL_PATH = os.path.join(ROOT, 'bj_tool.py')
README_PATH = os.path.join(ROOT, 'README.md')
REMOVED_DIR = os.path.join(SKILLS_DIR, '_removed')
RATINGS_PATH = os.path.join(ROOT, 'skill-ratings.json')
DEFAULT_SHARD_THRESHOLD = 12      # 类目内超过这么多个模块，菜单就把它拆到 sections/<类目>.md

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


# ---------------------------------------------------------------- 契约（deploy-contract.json）

def _spec_datas():
    """从 bj_tool.spec 里把 DATAS 读出来（用 AST，不执行 PyInstaller 代码）。"""
    import ast
    if not os.path.isfile(SPEC_PATH):
        return []
    tree = ast.parse(read(SPEC_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, 'id', '') == 'DATAS':
                    try:
                        return [tuple(ast.literal_eval(e)) for e in node.value.elts]
                    except Exception:
                        return []
    return []


def _covered_by_datas(rel: str, datas) -> bool:
    rel = rel.replace(os.sep, '/')
    for ent in datas:
        src = str(ent[0]).replace(os.sep, '/')
        if rel == src or rel.startswith(src.rstrip('/') + '/'):
            return True
    return False


def _bj_composed():
    """从 bj_tool.py 读 COMPOSED 表（AST，不 import：那会拖进 PySide6）。"""
    import ast
    if not os.path.isfile(BJTOOL_PATH):
        return {}
    try:
        tree = ast.parse(read(BJTOOL_PATH))
    except Exception:
        return {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == 'COMPOSED' for t in node.targets):
            try:
                return {k: (v[0], list(v[1])) for k, v in ast.literal_eval(node.value).items()}
            except Exception:
                return {}
    return {}


def cmd_contract(args):
    """校验部署契约与仓库实际状态一致。

    契约不是文档：里面每条都要能被核到，否则就是摆设。这里查四类东西 ——
      ① 随包资源清单 ↔ 磁盘 ↔ bj_tool.spec 的 DATAS（新增模板忘了进包 = 跑到客户机才炸）
      ② 技能库规模 / 类目数 ↔ 
      ③ 标记块字符串 / 退出码 ↔ inject.ps1 里实际用的是不是同一套
      ④ 溯源记录（clean-room）↔ README 「致谢与参考」写的是不是同一个 commit/许可证
    """
    errors, warnings = [], []
    if not os.path.isfile(CONTRACT_PATH):
        print('✗ 缺 deploy-contract.json')
        return 2
    c = json.loads(read(CONTRACT_PATH))
    for k in ('schema', 'version', 'title'):
        if not c.get(k):
            errors.append('契约缺字段 %s' % k)

    inject_txt = read(INJECT_PATH) if os.path.isfile(INJECT_PATH) else ''
    datas = _spec_datas()

    # ① 资源清单
    resources = list(c.get('resources') or [])
    if 'deploy-contract.json' not in resources:
        errors.append('resources 里得包含 deploy-contract.json 自己（否则打包后 check 读不到契约）')
    if len(set(resources)) != len(resources):
        errors.append('resources 有重复项')
    missing, uncovered = [], []
    for rel in resources:
        if not os.path.exists(os.path.join(ROOT, rel.replace('/', os.sep))):
            missing.append(rel)
        if not _covered_by_datas(rel, datas):
            uncovered.append(rel)
    if missing:
        errors.append('resources 里这些文件磁盘上没有：%s' % '、'.join(missing))
    if uncovered:
        errors.append('这些资源没进 bj_tool.spec 的 DATAS（打包后会缺）：%s' % '、'.join(uncovered))
    if not datas:
        errors.append('读不到 bj_tool.spec 的 DATAS')

    # ② 技能库规模与类目
    lib = c.get('skillLibrary') or {}
    skroot = os.path.join(ROOT, lib.get('root') or 'skills-v4')
    disk = [x for x in sorted(os.listdir(skroot)) if not x.startswith('_')
            and os.path.isfile(os.path.join(skroot, x, 'SKILL.md'))] if os.path.isdir(skroot) else []
    if len(disk) < int(lib.get('minSkills') or 0):
        errors.append('技能库只 %d 个，低于契约下限 %s' % (len(disk), lib.get('minSkills')))
    cats_file = os.path.join(ROOT, lib.get('categoriesFile') or 'skill-categories.json')
    ncat = 0
    if os.path.isfile(cats_file):
        ncat = len(json.loads(read(cats_file)).get('categories') or [])
        if ncat != int(lib.get('categories') or 0):
            errors.append('类目数不一致：契约 %s / 实际 %d（跑一次 gen 或更新契约）'
                          % (lib.get('categories'), ncat))
    else:
        errors.append('类目表不存在：%s' % lib.get('categoriesFile'))
    if (lib.get('classField') or '') != 'metadata.' + CLASS_KEY:
        errors.append('契约声明的类目字段（%s）与 skill_tool.py 的 CLASS_KEY（metadata.%s）不一致'
                      % (lib.get('classField'), CLASS_KEY))
    for a in c.get('addons') or []:
        if not os.path.isfile(os.path.join(skroot, a, 'SKILL.md')):
            errors.append('附加包缺失：%s' % a)

    # ②b 模板：引用的零件齐全且在清单里、身份锚定串齐全且不拄串、与 bj_tool.py 的版本表一致
    tpl = c.get('templates') or {}
    tplroot = os.path.join(ROOT, 'prompts')
    anchors = tpl.get('anchors') or {}
    composed = tpl.get('composed') or {}
    standalone = tpl.get('standalone') or {}
    names = set(anchors) | set(standalone.values())
    shared = tpl.get('shared_anchors') or []
    for item in shared:
        names |= set(item.get('files') or [])
    for spec in composed.values():
        names |= set(spec.get('parts') or [])
    texts = {}
    for n in names:
        p = os.path.join(tplroot, n)
        texts[n] = read(p) if os.path.isfile(p) else None
    referenced = set(standalone.values())
    for spec in composed.values():
        referenced |= set(spec.get('parts') or [])
    for n in sorted(referenced):
        if texts.get(n) is None:
            errors.append('模板缺失（被版本引用）：prompts/%s' % n)
        elif not texts[n].strip():
            errors.append('模板是空文件：prompts/%s' % n)
        if ('prompts/' + n) not in resources:
            errors.append('prompts/%s 被版本引用但不在 resources 里（打包清单会漏掉它）' % n)
    for n, words in anchors.items():
        t = texts.get(n)
        if t is None:
            errors.append('锚定串声明的模板不存在：prompts/%s' % n)
            continue
        for w in words:
            if w not in t:
                errors.append('prompts/%s 缺锚定串「%s」（模板被改坏或换错了？）' % (n, w))
            others = sorted(g for g, gt in texts.items() if g != n and gt and w in gt)
            if others:
                errors.append('锚定串拄串：prompts/%s 的「%s」也出现在 %s' % (n, w, '、'.join(others)))
    # 共享锚定串（如 V5.1b 与 V5.2c 共用开头）：必须在声明的那几个文件里，且不得流到其它模板
    for item in shared:
        w = item.get('text') or ''
        group = list(item.get('files') or [])
        if not w or not group:
            errors.append('shared_anchors 项缺 text 或 files')
            continue
        for n in group:
            t = texts.get(n)
            if t is None:
                errors.append('共享锚定串声明的模板不存在：prompts/%s' % n)
            elif w not in t:
                errors.append('prompts/%s 缺共享锚定串「%s」' % (n, w))
        extra = sorted(g for g, gt in texts.items() if g not in group and gt and w in gt)
        if extra:
            errors.append('共享锚定串「%s」流到了不该有的模板：%s' % (w, '、'.join(extra)))
    # 与 bj_tool.py 的版本表对齐（防“契约改了、代码没改”）
    bt = _bj_composed()
    for key, spec in composed.items():
        got = bt.get(key)
        want_out, want_parts = spec.get('out'), list(spec.get('parts') or [])
        if got is None:
            errors.append('契约声明了合成版本 %s，但 bj_tool.py 的 COMPOSED 里没有' % key)
        elif got[0] != want_out or got[1] != want_parts:
            errors.append('合成版本 %s 与 bj_tool.py 不一致：契约 %s%s / 代码 %s%s'
                          % (key, want_out, want_parts, got[0], got[1]))
    bjtxt = read(BJTOOL_PATH) if os.path.isfile(BJTOOL_PATH) else ''
    for key, f in standalone.items():
        pat = re.search(r"'" + re.escape(key) + r"':\s*'([^']+)'", bjtxt)
        if not pat:
            errors.append('契约声明了独立模板 %s，但 bj_tool.py 的 _prompt_file 里没有' % key)
        elif pat.group(1) != f:
            errors.append('独立模板 %s 指向不一致：契约 %s / bj_tool.py %s' % (key, f, pat.group(1)))
    # 孤儿模板：既没被版本引用、也不在随包清单（也没声明 orphan_ok）
    orphan_ok = set(tpl.get('orphan_ok') or [])
    if os.path.isdir(tplroot):
        for f in sorted(os.listdir(tplroot)):
            if not f.endswith('.md') or f.upper().startswith('README'):
                continue
            if f in referenced or ('prompts/' + f) in resources or f in orphan_ok:
                continue
            warnings.append('模板 prompts/%s 没被任何版本引用、也不在随包清单里 —— 是要删，还是漏登记？' % f)

    # ③ 标记块 / 目标端 / 退出码 ↔ inject.ps1
    # 标记块在源码里是「关键串 + 版本载荷」拼出来的：
    #   $MARK_KEY_BEG = '<!-- BEGIN ' + $TOOL_TAG
    #   $MARK_BEG     = $MARK_KEY_BEG + ' ' + $MARK_VER + ' -->'
    # 所以不能直接搜字面量：先把 $TOOL_TAG / $MARK_VER 取出来，按同一拼法还原期望值再比。
    tag_m = re.search(r"^\$TOOL_TAG\s*=\s*'([^']+)'", inject_txt, re.M)
    tag = tag_m.group(1) if tag_m else ''
    ver_m = re.search(r"^\$MARK_VER\s*=\s*'([^']+)'", inject_txt, re.M)
    ver = ver_m.group(1) if ver_m else ''
    if not tag:
        errors.append('读不到 inject.ps1 的 $TOOL_TAG')
    if not ver:
        errors.append('读不到 inject.ps1 的 $MARK_VER')
    for var, word in (('$MARK_KEY_BEG', 'BEGIN'), ('$MARK_KEY_END', 'END')):
        # 关键串必须用 $TOOL_TAG 拼（不许写死）；源码形如：
        #   $MARK_KEY_BEG  = '<!-- BEGIN ' + $TOOL_TAG
        pat_key = re.compile(re.escape(var) + r"\s*=\s*'\x3c!-- " + word + r" '\s*\+\s*\$TOOL_TAG")
        if tag and not pat_key.search(inject_txt):
            errors.append('inject.ps1 里 %s 没用 $TOOL_TAG 拼成关键串' % var)
    # 定位正则必须基于关键串（而非写死的整串）—— 否则标记升级就认不出旧块
    for var in ('$MARK_RE_BEG', '$MARK_RE_END'):
        m2 = re.search(re.escape(var) + r"\s*=\s*[^\r\n]*", inject_txt)
        if not m2 or ('MARK_KEY_BEG' not in m2.group(0) and 'MARK_KEY_END' not in m2.group(0)):
            errors.append('inject.ps1 的 %s 不是基于关键串构造（旧版本标记会认不出）' % var)
    for t in c.get('targets') or []:
        key = t.get('key')
        if not key:
            errors.append('targets 里有一项没 key')
            continue
        if ("'" + key + "' = @{") not in inject_txt:
            errors.append('inject.ps1 的 目标表里没有 %s' % key)
        if tag:
            want_begin = '<!-- BEGIN ' + tag + ' ' + (ver or 'v?') + ' -->'
            want_end = '<!-- END ' + tag + ' ' + (ver or 'v?') + ' -->'
            for f, want in (('markerBegin', want_begin), ('markerEnd', want_end)):
                got = t.get(f)
                if got != want:
                    errors.append('targets.%s.%s 与 inject.ps1 的 $TOOL_TAG(%s) 拼出来的不一致：%s ≠ %s'
                                  % (key, f, tag, got, want))
        if not t.get('promptName'):
            errors.append('targets.%s 缺 promptName' % key)
        elif t['promptName'] not in inject_txt:
            errors.append('targets.%s.promptName 在 inject.ps1 里找不到：%s' % (key, t['promptName']))
        if t.get('patchName') and t['patchName'] not in inject_txt:
            errors.append('targets.%s.patchName 在 inject.ps1 里找不到：%s' % (key, t['patchName']))
        for cli in t.get('probeCli') or []:
            if cli not in inject_txt:
                warnings.append('targets.%s.probeCli 里的 %s 没写进 inject.ps1（体检会退到自动探测）' % (key, cli))
    for code in (c.get('exitCodes') or {}):
        pat = 'exit ' + str(code)
        if pat not in inject_txt:
            errors.append('契约声明了退出码 %s，但 inject.ps1 里没有 `%s`' % (code, pat))

    # ④ 溯源记录 ↔ README
    readme = read(README_PATH) if os.path.isfile(README_PATH) else ''
    # ④b 合规清单：NOTICE 三段式 + THIRD-PARTY-NOTICES 与磁盘一致。
    # 为什么放在契约里：发布前最容易漏的就是这两份说明，而它们直接决定「能不能再分发」。
    notice_path = os.path.join(ROOT, 'NOTICE.md')
    notice = read(notice_path) if os.path.isfile(notice_path) else ''
    for sec in ('## 一、许可范围', '## 二、第三方组件', '## 三、再分发限制'):
        if sec not in notice:
            errors.append('NOTICE.md 缺小节「%s」（对外发布时的合规说明）' % sec)
    tpn_path = os.path.join(ROOT, 'THIRD-PARTY-NOTICES.md')
    n_bare = len(bare_skills())
    if not os.path.isfile(tpn_path):
        errors.append('缺 THIRD-PARTY-NOTICES.md（跑 skill_tool.py notice --write 生成）')
    else:
        tt = read(tpn_path)
        if ('未声明来源或许可的技能（%d）' % n_bare) not in tt:
            errors.append('THIRD-PARTY-NOTICES.md 的「未声明许可」数量与磁盘不一致（当前 %d），跑 notice --write 重新生成' % n_bare)
        for _n in disk_skills():
            lic_p, _l2, _a2 = skill_provenance(_n)
            if lic_p and ('`%s`' % _n) not in tt:
                errors.append('THIRD-PARTY-NOTICES.md 没登记带 LICENSE 的技能：%s' % _n)
    for name, src in (c.get('cleanroom') or {}).items():
        repo, commit, lic = src.get('repo'), src.get('commit'), src.get('license')
        mode = src.get('mode') or ''
        if not (repo and commit and lic and src.get('method')):
            errors.append('cleanroom.%s 缺 repo/commit/license/method（溯源不能只写一句话）' % name)
            continue
        if mode not in ('clean-room', 'reuse'):
            errors.append('cleanroom.%s.mode 必须是 clean-room 或 reuse（决定能不能复用正文）' % name)
        if not re.fullmatch(r'[0-9a-f]{7,40}', str(commit).lower()):
            errors.append('cleanroom.%s.commit 不像 commit（短哈希至少 7 位）：%s' % (name, commit))
        for label, needle in (('repo', repo), ('license', lic), ('commit', commit)):
            if needle not in readme:
                errors.append('README 没写 cleanroom.%s 的 %s（%s）—— 口头致谢不算溯源' % (name, label, needle))
        # 非宽松许可只能借机制；宽松许可（MIT 这类）才能复用正文
        if mode == 'clean-room' and not any(x in readme for x in ('未使用其代码', '未复制', 'clean-room', 'clean room')):
            errors.append('cleanroom.%s 是 clean-room，但 README 没写明「只借设计、未取文本」' % name)
        if mode == 'reuse' and '允许复用' not in readme:
            errors.append('cleanroom.%s 是 reuse，README 里应说明「该许可允许复用正文」' % name)
    if 'GPL-3.0' in json.dumps(c, ensure_ascii=False) and not (c.get('cleanroom')):
        errors.append('契约里提到了第三方许可证，但没写 cleanroom 溯源段')

    # ③ 评分表：随包（菜单排序列 + 分片阈值），缺覆盖只提示不拦 —— 缺了菜单就少一列
    ratings, shard_threshold, _manual = load_ratings()
    rated = [n for n in disk if n in ratings]
    if not os.path.isfile(RATINGS_PATH):
        warnings.append('缺 skill-ratings.json：菜单少一列评分（跑 skill_tool.py rate --seed 生成）')
    elif len(rated) < len(disk):
        warnings.append('评分表未覆盖 %d 个技能：菜单里它们不带【x/10】' % (len(disk) - len(rated)))

    print('契约：%s（schema %s / 版本 %s）' % (os.path.relpath(CONTRACT_PATH, ROOT), c.get('schema'), c.get('version')))
    print('  随包资源      %d 项（磁盘齐全 %d / 已进 DATAS %d）'
          % (len(resources), len(resources) - len(missing), len(resources) - len(uncovered)))
    print('  技能库        %d 个技能（下限 %s）｜ 类目 %d 个'
          % (len(disk), lib.get('minSkills'), ncat))
    print('  模板          %d 个引用件 ｜ 合成版本 %d ｜ 独立版本 %d ｜ 锚定串 %d 个模板'
          % (len(referenced), len(composed), len(standalone), len(anchors)))
    print('  评分          已评 %d/%d ｜ 类目内超过 %d 个模块就分片'
          % (len(rated), len(disk), shard_threshold))
    print('  目标端        %s' % '、'.join(t.get('label') or t.get('key') or '?' for t in c.get('targets') or []))
    print('  退出码        %s' % '、'.join(sorted((c.get('exitCodes') or {}).keys())))
    print('  溯源          %s' % '、'.join('%s %s (%s/%s)' % (k, str(v.get('commit', ''))[:7], v.get('license'), v.get('mode'))
                                          for k, v in (c.get('cleanroom') or {}).items()))
    print('  合规          NOTICE 三段式 ✓ ｜ 第三方清单已同步 ｜ 未声明来源的技能 %d 个' % n_bare)
    for w in warnings:
        print('  ⚠ %s' % w)
    for e in errors:
        print('  ✗ %s' % e)
    print()
    print('结论：%s' % ('契约与仓库一致' if not errors else '不一致（%d 个错误）' % len(errors)))
    return 1 if errors else 0


# ---------------------------------------------------------------- 评分（菜单排序用）

def rating_signals(d):
    """按**可测量信号**给一个技能打分（自足度，不是质量论断）。

    公式（同步写进 skill-ratings.json 的 formula 字段，可复算）：
      base 5
      +1 有 references/ 目录      —— 自带分片资料，不必全靠模型知识
      +1 带可执行脚本             —— 能直接跑，不只是说明文字
      +1 description >= 80 字符   —— 路由信息足够长
      +1 正文 >= 2500 字符        —— 有实质内容
      +1 正文含围栏代码块         —— 有可复用命令
    结果夹在 1..10。它只解决「同一类目里多个模块都命中时先看谁」，
    不代替人（或 AI）对模块强弱的判断 —— 后者用 rate --set 覆盖。
    """
    sk = os.path.join(d, 'SKILL.md')
    text = read(sk) if os.path.isfile(sk) else ''
    fm, body = parse_frontmatter(text)
    desc = fm_value(fm, 'description')
    score, sig = 5, []
    if os.path.isdir(os.path.join(d, 'references')):
        score += 1
        sig.append('references')
    if any(fn.lower().endswith(('.py', '.ps1', '.sh', '.js', '.mjs', '.cmd', '.bat'))
           for _dp, _dn, fns in os.walk(d) for fn in fns):
        score += 1
        sig.append('scripts')
    if len(desc) >= 80:
        score += 1
        sig.append('desc>=80')
    if len(body) >= 2500:
        score += 1
        sig.append('body>=2500')
    if (chr(96) * 3) in body:      # 围栏代码块：避免在源码里写三个反引号
        score += 1
        sig.append('code')
    return max(1, min(10, score)), sig


def load_ratings():
    """返回 (ratings, threshold, manual)。文件缺失/损坏时给空表与默认阈值（菜单照样生成）。"""
    if not os.path.isfile(RATINGS_PATH):
        return {}, DEFAULT_SHARD_THRESHOLD, []
    try:
        j = json.loads(read(RATINGS_PATH))
    except Exception:
        return {}, DEFAULT_SHARD_THRESHOLD, []
    return (dict(j.get('ratings') or {}),
            int(j.get('shardThreshold') or DEFAULT_SHARD_THRESHOLD),
            list(j.get('manual') or []))


def write_ratings(ratings, threshold, manual):
    data = {
        'schema': 1,
        'note': '模块评分与菜单分片阈值。评分只用于「同一类目里多个模块都命中」时的排序：'
                '匹配度 → 评分 → 索引序。它是**可测量的自足度**（见 formula），不是质量论断；'
                '人工/AI 改过的条目记在 manual 里，重跑 rate --seed 不会覆盖它们。',
        'formula': 'base 5 + references/+1 + scripts/+1 + desc>=80字符/+1 + 正文>=2500字符/+1 + 含代码块/+1（夹在 1..10）',
        'shardThreshold': threshold,
        'manual': sorted(manual),
        'ratings': {k: ratings[k] for k in sorted(ratings)},
    }
    tmp = RATINGS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.replace(tmp, RATINGS_PATH)


def _library_modules():
    return [x for x in sorted(os.listdir(SKILLS_DIR)) if not x.startswith('_')
            and os.path.isfile(os.path.join(SKILLS_DIR, x, 'SKILL.md'))] if os.path.isdir(SKILLS_DIR) else []


def cmd_rate(args):
    """评分表维护：--seed 生成 / --set 人工覆盖 / --list 查看 / --check 校验覆盖。"""
    ratings, threshold, manual = load_ratings()
    disk = _library_modules()

    if args.threshold is not None:
        if args.threshold < 1:
            print('✗ 分片阈值要 >= 1')
            return 2
        threshold = args.threshold

    if args.set:
        name, value = args.set
        if name not in disk:
            print('✗ 技能库里没有 %s' % name)
            return 2
        try:
            v = int(value)
        except ValueError:
            v = 0
        if not 1 <= v <= 10:
            print('✗ 评分要 1..10 的整数')
            return 2
        ratings[name] = v
        if name not in manual:
            manual.append(name)
        write_ratings(ratings, threshold, manual)
        print('已记 %s = %d/10（manual，--seed 不会覆盖）' % (name, v))
        return 0

    if args.seed:
        if args.force:
            ratings, manual = {}, []
        computed = []
        for n in disk:
            if n in manual and n in ratings:
                continue
            v, sig = rating_signals(os.path.join(SKILLS_DIR, n))
            ratings[n] = v
            computed.append((v, n, sig))
        for n in list(ratings):
            if n not in disk:
                del ratings[n]                  # 技能已移除 → 评分表跟着瘦身
        write_ratings(ratings, threshold, manual)
        computed.sort(key=lambda r: (-r[0], r[1]))
        for v, n, sig in computed[:args.limit]:
            print('  %2d/10  %-34s %s' % (v, n, ','.join(sig) or '-'))
        if len(computed) > args.limit:
            print('  … 其余 %d 条略（--limit 调大即可看全）' % (len(computed) - args.limit))
        hist = {}
        for v in ratings.values():
            hist[v] = hist.get(v, 0) + 1
        print('评分表：%s（%d 条：%s）' % (os.path.relpath(RATINGS_PATH, ROOT), len(ratings),
                                      ' '.join('%d分%d个' % (k, hist[k]) for k in sorted(hist))))
        if manual:
            print('保留人工评分 %d 条（--force 可一并重算）' % len(manual))
        print('分片阈值：类目内超过 %d 个模块 → 拆到 sections/<类目>.md，菜单只留一行指针' % threshold)
        return 0

    if args.check:
        missing = [n for n in disk if n not in ratings]
        extra = [n for n in ratings if n not in disk]
        print('评分覆盖 %d/%d ｜ 分片阈值 >%d' % (len(disk) - len(missing), len(disk), threshold))
        if missing:
            print('  缺评分：%s%s' % ('、'.join(missing[:10]), ' …' if len(missing) > 10 else ''))
        if extra:
            print('  表里有多余条目（技能已不在库）：%s' % '、'.join(extra[:10]))
        return 1 if (missing or extra) else 0

    rows = sorted(((ratings.get(n, 0), n) for n in disk), key=lambda r: (-r[0], r[1]))
    for v, n in rows[:args.limit]:
        print('  %s  %s' % (('%2d/10' % v) if v else '  --  ', n))
    if len(rows) > args.limit:
        print('  … 其余 %d 条略' % (len(rows) - args.limit))
    print('共 %d 个技能 ｜ 分片阈值 >%d' % (len(disk), threshold))
    return 0


def cmd_pack(args):
    """把技能库打成可分发压缩包（含 SHA-256 清单），或校验一个已打好的包。

    为什么不做「安装器/加密封印」：本仓库是 MIT 且暂不发布，拆包本来就该能读。
    能带走的只有两件事：完整的技能库，跟一份能自证的清单。
    """
    import datetime
    import zipfile

    def collect():
        skip_bare = bool(getattr(args, 'exclude_bare', False))
        bare = set(bare_skills()) if skip_bare else set()
        files = []
        for cat in ('skill-categories.json', 'deploy-contract.json'):
            p = os.path.join(ROOT, cat)
            if os.path.isfile(p):
                files.append((cat, p))
        for r, dirs, fs in os.walk(SKILLS_DIR):
            dirs[:] = [d for d in sorted(dirs) if d != '_removed']
            # --exclude-bare：把未声明来源的技能目录整个跳过（保留仓库里的文件，只是不进包）
            rel_dir = os.path.relpath(r, SKILLS_DIR).replace(os.sep, '/')
            top = rel_dir.split('/')[0]
            if skip_bare and top in bare:
                dirs[:] = []
                continue
            for f in sorted(fs):
                p = os.path.join(r, f)
                files.append((os.path.relpath(p, ROOT).replace(os.sep, '/'), p))
        return sorted(set(files))

    if args.verify:
        if not os.path.isfile(args.verify):
            print('文件不存在：%s' % args.verify)
            return 2
        bad, missing, nost = [], [], []
        with zipfile.ZipFile(args.verify) as z:
            names = set(z.namelist())
            if 'MANIFEST.sha256' not in names:
                print('✗ 包里没有 MANIFEST.sha256，无法校验')
                return 2
            man = z.read('MANIFEST.sha256').decode('utf-8').splitlines()
            want = {}
            for ln in man:
                if not ln.strip():
                    continue
                h, rel = ln.split('  ', 1)
                want[rel] = h
            for rel, h in sorted(want.items()):
                if rel not in names:
                    missing.append(rel)
                    continue
                got = hashlib.sha256(z.read(rel)).hexdigest()
                if got != h:
                    bad.append(rel)
            extra = sorted(names - set(want) - {'MANIFEST.sha256'})
            if extra:
                nost = extra
        print('校验 %s' % os.path.relpath(args.verify, ROOT) if args.verify.startswith(ROOT) else args.verify)
        print('  清单项 %d ｜ 摘要不符 %d ｜ 清单有包内无 %d ｜ 包内有清单无 %d'
              % (len(want), len(bad), len(missing), len(nost)))
        for x in bad[:5]:
            print('  ✗ 摘要不符：%s' % x)
        for x in missing[:5]:
            print('  ✗ 缺失：%s' % x)
        for x in nost[:5]:
            print('  ⚠ 未入清单：%s' % x)
        ok = not (bad or missing)
        print()
        print('结论：%s' % ('包完整，与清单一致' if ok else '包与清单不一致'))
        return 0 if ok else 1

    files = collect()
    if not files:
        print('没有可打包的文件（skills-v4 是空的？）')
        return 2
    out = args.out or os.path.join(ROOT, 'build',
                                   'skill-library-%s.zip' % datetime.datetime.now().strftime('%Y%m%d-%H%M'))
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    lines = []
    for rel, p in files:
        with open(p, 'rb') as fh:
            lines.append('%s  %s' % (hashlib.sha256(fh.read()).hexdigest(), rel))
    manifest = '\n'.join(lines) + '\n'
    tmp = out + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        # 时间戳写死：同样的输入每次打出同样的字节，方便对哈希
        fixed = (1980, 1, 1, 0, 0, 0)
        for rel, p in files:
            zi = zipfile.ZipInfo(rel, date_time=fixed)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with open(p, 'rb') as fh:
                z.writestr(zi, fh.read())
        zi = zipfile.ZipInfo('MANIFEST.sha256', date_time=fixed)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        z.writestr(zi, manifest)
    os.replace(tmp, out)
    size = os.path.getsize(out)
    with open(out, 'rb') as fh:
        zh = hashlib.sha256(fh.read()).hexdigest()
    print('已打包 %d 个文件 → %s（%s 字节）' % (len(files), os.path.relpath(out, ROOT), size))
    print('  包 sha256  %s' % zh)
    print('  清单       MANIFEST.sha256（%d 行）' % len(lines))
    packed_skills = len({rel.split('/')[1] for rel, _ in files if rel.startswith('skills-v4/')})
    hidden = len(bare_skills()) if getattr(args, 'exclude_bare', False) else 0
    if hidden:
        print('  技能数     %d（已按 --exclude-bare 排除未声明来源的 %d 个）' % (packed_skills, hidden))
    else:
        print('  技能数     %d' % packed_skills)
    print()
    print('校验：py -X utf8 skill_tool.py pack --verify "%s"' % out)
    return 0


def skill_provenance(name: str):
    """一个技能的来源线索：@(许可文件绝对路径或 None, frontmatter license, frontmatter author)

    notice 与 pack --exclude-bare 都用它，保证「清单里说未声明」与「打包时排除」是同一套判定。
    """
    d = os.path.join(SKILLS_DIR, name)
    lic = None
    for root2, dirs2, files2 in os.walk(d):
        if os.path.relpath(root2, d).count(os.sep) > 1:
            dirs2[:] = []
            continue
        hit = [x for x in files2
               if os.path.splitext(x)[0].upper() in ('LICENSE', 'COPYING', 'LICENCE')
               and os.path.splitext(x)[1].upper() in ('', '.MD', '.TXT', '.MARKDOWN')]
        if hit:
            lic = os.path.join(root2, hit[0])
            break
    fm = skill_info(name)['fm']
    return (lic, fm_value(fm, 'license'), fm_value(fm, 'author'))


def bare_skills():
    """既无 LICENSE、也无 license / author 声明的技能名（再分发风险项）"""
    return [n for n in disk_skills() if not any(skill_provenance(n))]


def cmd_notice(args):
    """生成 / 校验 THIRD-PARTY-NOTICES.md（第三方清单）

    为什么自动生成：65 个技能里哪些带独立 LICENSE、哪些标了作者、哪些什么都没声明——
    手写的清单一定会腐（改了技能库忘了改清单），而这份东西是**再分发**时的依据。
    脚本只抽磁盘上真实存在的字段，不替你猜许可。
    """
    rows_lic, rows_author, rows_declared, rows_bare = [], [], [], []
    for n in disk_skills():
        lic_file, fm_lic, fm_author = skill_provenance(n)
        if lic_file:
            first = ''
            try:
                for ln in read(lic_file).splitlines():
                    if ln.strip():
                        first = ln.strip()[:80]
                        break
            except Exception:
                pass
            rows_lic.append((n, os.path.relpath(lic_file, ROOT).replace(os.sep, '/'),
                             hashlib.sha256(open(lic_file, 'rb').read()).hexdigest()[:12], first))
        if fm_author:
            rows_author.append((n, fm_author))
        if fm_lic:
            rows_declared.append((n, fm_lic))
        if not (lic_file or fm_lic or fm_author):
            rows_bare.append(n)

    contract = json.loads(read(CONTRACT_PATH)) if os.path.isfile(CONTRACT_PATH) else {}
    clean = contract.get('cleanroom') or {}

    L = []
    L.append('# 第三方组件与许可清单')
    L.append('')
    L.append('> 本文件由 `py -X utf8 skill_tool.py notice --write` 生成，**不要手改**（改了下次生成会被覆盖）。')
    L.append('> 它只记录磁盘上真实存在的字段：技能目录里的 `LICENSE` 文件、`SKILL.md` frontmatter 的')
    L.append('> `license:` / `author:`。没有声明的就不写，不替上游猜许可。')
    L.append('')
    L.append('## 1. 随附原始许可文件的技能包（%d）' % len(rows_lic))
    L.append('')
    if rows_lic:
        L.append('| 技能 | 许可文件 | sha256 | 首行 |')
        L.append('|---|---|---|---|')
        for n, rel, h, first in rows_lic:
            L.append('| `%s` | `%s` | `%s` | %s |' % (n, rel, h, first))
        L.append('')
        L.append('这些目录内的 `LICENSE` 保持原样，请一并遵守。')
    else:
        L.append('（无）')
    L.append('')
    L.append('## 2. frontmatter 标注了作者的技能（%d）' % len(rows_author))
    L.append('')
    if rows_author:
        L.append('| 技能 | author |')
        L.append('|---|---|')
        for n, a in rows_author:
            L.append('| `%s` | %s |' % (n, a))
    else:
        L.append('（无）')
    L.append('')
    L.append('## 3. frontmatter 声明了许可的技能（%d）' % len(rows_declared))
    L.append('')
    if rows_declared:
        L.append('| 技能 | license |')
        L.append('|---|---|')
        for n, lic in rows_declared:
            L.append('| `%s` | %s |' % (n, lic))
    else:
        L.append('（无）')
    L.append('')
    L.append('## 4. 未声明来源或许可的技能（%d）' % len(rows_bare))
    L.append('')
    L.append('这些目录里既没有 `LICENSE`，frontmatter 也没有 `license:` / `author:`。')
    L.append('本仓库把它们作为**整理收集的资料**随附；**再分发或商用前请自行确认权利人意愿**')
    L.append('（本仓库不代为授权）。数量：%d / %d。' % (len(rows_bare), len(disk_skills())))
    L.append('')
    if rows_bare:
        for i2 in range(0, len(rows_bare), 6):
            L.append('- ' + '、'.join('`%s`' % x for x in rows_bare[i2:i2 + 6]))
        L.append('')
    L.append('## 5. 提示词模板与机制溯源')
    L.append('')
    L.append('逐项记录（仓库 / commit / 许可证 / 引用方式）在 `deploy-contract.json` 的 `cleanroom` 段，')
    L.append('`py -X utf8 skill_tool.py contract` 会校对它们与 README 致谢一致。当前条目：')
    L.append('')
    L.append('| 来源 | commit | 许可证 | 方式 |')
    L.append('|---|---|---|---|')
    for k, v in clean.items():
        L.append('| %s | `%s` | %s | %s |' % (v.get('repo'), str(v.get('commit') or '')[:12],
                                                v.get('license'), v.get('mode')))
    L.append('')
    L.append('另：`prompts/_glm53f-kovak.md` 收编自上游仓库（其未声明许可）；`NOTICE.md` 的')
    L.append('「再分发限制」节列出了不得随包分发的组件。')
    L.append('')
    text = '\n'.join(L) + '\n'
    out = os.path.join(ROOT, 'THIRD-PARTY-NOTICES.md')

    if args.write:
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
        os.replace(tmp, out)
        print('已写入 %s' % os.path.relpath(out, ROOT))
        print('  带 LICENSE 的技能包 %d ｜ 标了作者 %d ｜ 声明许可 %d ｜ 未声明 %d'
              % (len(rows_lic), len(rows_author), len(rows_declared), len(rows_bare)))
        return 0

    cur = read(out) if os.path.isfile(out) else ''
    if cur.strip() == text.strip():
        print('THIRD-PARTY-NOTICES.md 与技能库一致（%d 个技能）' % len(disk_skills()))
        return 0
    print('THIRD-PARTY-NOTICES.md 已过期或缺失 —— 跑 `py -X utf8 skill_tool.py notice --write` 重新生成')
    return 1


# 导入识别：深度上限与数量上限
# 来自 alice-assistant 的 import_skill.rs：不依赖用户说明，按 SKILL.md 的位置自己认；
# 但必须封顶 —— 用户误选 C:\ 这种巨型目录时不能无限下钻、不能把命令卡死。
IMPORT_MAX_DEPTH = 3        # 相对来源根的层数
IMPORT_MAX_SKILLS = 64      # 单次收集上限


def collect_skill_roots(base: str, max_depth: int = IMPORT_MAX_DEPTH,
                        max_count: int = IMPORT_MAX_SKILLS):
    """把一个来源（目录 / 解压后的 zip）里**所有**技能目录收出来。

    识别规则（不依赖用户说明）：
      · 目录里直接有 SKILL.md          → 这个目录就是一个技能
      · 目录里 父级/子级 有 SKILL.md   → 该目录是「技能集合」，逐个收进去
      · zip 常见的一层包装             → 自动下钻（如 my-skill-main/my-skill/SKILL.md）
      · 找到技能后不再往下钻（技能目录里的 references/ scripts/ 不是技能）

    返回 (roots, notes)：notes 记录被截断 / 跳过的原因，调用方要如实打印，
    而不是默默少装几个。
    """
    notes = []
    if not os.path.isdir(base):
        return [], ['不是目录：%s' % base]
    if os.path.isfile(os.path.join(base, 'SKILL.md')):
        return [base], notes
    roots = []
    seen = set()
    hit_cap = False
    for r, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if not x.startswith('.'))
        rel = os.path.relpath(r, base)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        if depth >= max_depth and dirs:
            notes.append('到达深度上限 %d，未继续下钻：%s' % (max_depth, rel))
            dirs[:] = []
        if 'SKILL.md' in files:
            key = os.path.abspath(r)
            if key not in seen:
                seen.add(key)
                roots.append(r)
                if len(roots) >= max_count:
                    hit_cap = True
                    break
            dirs[:] = []          # 技能目录内部不再下钻
    if hit_cap:
        notes.append('已收满 %d 个技能后停止（上限）—— 来源目录太大时会这样，建议按包分别导入' % max_count)
    return roots, notes


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
            if len(cands) > IMPORT_MAX_SKILLS:
                print('⚠ 子目录超过上限 %d，只处理前 %d 个' % (IMPORT_MAX_SKILLS, IMPORT_MAX_SKILLS))
                cands = cands[:IMPORT_MAX_SKILLS]
            for c in cands:
                ok, msg, nm = _install_one(c, args.category, None, None, args.dry_run, args.force, cats)
                if ok:
                    added.append(nm)
                else:
                    print('  ✗ %s：%s' % (os.path.basename(c), msg))
        else:
            # 自动识别：单技能 / 技能集合（一包多技能）/ 带一层包装的 zip
            roots, notes = collect_skill_roots(src)
            for n in notes:
                print('  ⚠ %s' % n)
            if not roots:
                print('✗ 没找到 SKILL.md：%s' % src)
                print('  来源目录里既没有技能（本级 SKILL.md），也没有子技能。')
                print('  如果是 zip：确认它里面真的含 <技能名>/SKILL.md。')
                return 2
            if len(roots) > 1:
                print('识别为技能集合：%d 个技能（来源 %s）' % (len(roots), src))
            for c in roots:
                ok, msg, nm = _install_one(c, args.category, args.name, args.desc,
                                           args.dry_run, args.force, cats)
                if ok:
                    added.append(nm)
                else:
                    print('  ✗ %s：%s' % (os.path.basename(c), msg))
            if not added:
                return 2

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
    # 契约里的类目数是硬声明（contract --check 会核对），机械同步，别指望人记得改
    if os.path.isfile(CONTRACT_PATH):
        try:
            c = json.loads(read(CONTRACT_PATH))
            c.setdefault('skillLibrary', {})['categories'] = len(cats['categories'])
            tmp = CONTRACT_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(c, fh, ensure_ascii=False, indent=2)
                fh.write('\n')
            os.replace(tmp, CONTRACT_PATH)
            print('契约里的类目数已同步为 %d' % len(cats['categories']))
        except Exception as e:
            print('⚠ 契约同步失败（记得手改 deploy-contract.json）: %s' % e)
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

    p = sub.add_parser('contract', help='校验部署契约（deploy-contract.json）与仓库实际状态一致')
    p.set_defaults(func=cmd_contract)

    p = sub.add_parser('pack', help='把技能库打成可分发压缩包（含 SHA-256 清单）')
    p.add_argument('--out', help='输出 zip 路径（默认 build/skill-library-<时间>.zip）')
    p.add_argument('--verify', help='校验一个已打好的包（不给则打新包）')
    p.add_argument('--exclude-bare', action='store_true',
                   help='排除未声明来源/许可的技能（再分发安全包）')
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser('notice', help='生成/校验 THIRD-PARTY-NOTICES.md（第三方许可清单）')
    p.add_argument('--write', action='store_true', help='写入文件（不给则只校验是否过期）')
    p.set_defaults(func=cmd_notice)

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

    p = sub.add_parser('rate', help='模块评分表（菜单排序列 + 分片阈值）：--seed / --set / --list / --check')
    p.add_argument('--seed', action='store_true', help='按可测量信号生成（保留人工评分）')
    p.add_argument('--force', action='store_true', help='--seed 时连人工评分一起重算')
    p.add_argument('--set', nargs=2, metavar=('技能名', '分'), help='人工指定 1..10（记入 manual，--seed 不覆盖）')
    p.add_argument('--list', action='store_true', help='列出评分')
    p.add_argument('--check', action='store_true', help='校验覆盖（缺失/多余退出码 1）')
    p.add_argument('--threshold', type=int, help='菜单分片阈值（类目内超过就拆文件）')
    p.add_argument('--limit', type=int, default=25, help='最多显示多少行（默认 25）')
    p.set_defaults(func=cmd_rate)

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
