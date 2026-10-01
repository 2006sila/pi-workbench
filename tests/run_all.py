# -*- coding: utf-8 -*-
"""一次跑完全部验收：注入核心 + 工程件 + 仓库门禁。

跑法： py -X utf8 tests\run_all.py
退出码：0 全过 / 1 有失败（可直接接 CI 或发版前的最后一道）
"""
import os
import subprocess
import sys

# 硬闸：禁止一切杀进程动作（子进程继承）。
# 事故背景：一次自检真的杀掉了 PiDeck 的 3 个进程 —— 自动化测试不该有能力碰真实客户端。
os.environ['PJ_TEST_NO_KILL'] = '1'

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable or 'python'


def run(cmd, label):
    print('\n' + '=' * 72)
    print('>> ' + label)
    print('=' * 72, flush=True)
    r = subprocess.run(cmd, cwd=ROOT, check=False)
    return r.returncode == 0


def main():
    results = []
    for f, label in (('verify_inject.py', '注入核心（标记兼容 / 备份保留 / 占位符断言 / 漂移）'),
                     ('verify_addons.py', '附加包生命周期（menuKeepAdvertised 同步 / 菜单重建 / 坏状态可检）'),
                     ('verify_patch.py', 'DSH patch 层（幂等不累积 / 用户配置保留 / 历史块自愈）'),
                     ('verify_borrowed.py', '借鉴机制（输出分级 / 进度清单先行 / 探针口径 / 重名来源选择）'),
                     ('verify_import.py', '技能来源识别（集合 / 包装 / zip / 同名冲突 / 上限）'),
                     ('verify_readonly.py', '只读保护 + 客户端路径探测'),
                     ('verify_ui.py', '界面回归（页面 / 模式 / 只读 / 版本窗 / 任务构建）'),
                     ('verify_job_and_deploy.py', '工程件（Job Object 进程树 / 自我部署脚本）')):
        ok = run([PY, '-X', 'utf8', os.path.join(HERE, f)], label)
        results.append((label, ok))

    # 仓库门禁（不需要沙箱，直接跑仓库自身的检查）
    for args, label in ((['skill_tool.py', 'contract'], '部署契约（资源 / 标记块 / 退出码 / 合规 / 溯源）'),
                        (['skill_tool.py', 'gen', '--check'], '类目三方一致（frontmatter × 类目表 × 磁盘）'),
                        (['skill_tool.py', 'notice'], '第三方清单与技能库同步'),
                        (['skill_tool.py', 'check'], '技能库体检（硬规则 / 预算 / 串稿 / 链接）')):
        ok = run([PY, '-X', 'utf8'] + args, label)
        results.append((label, ok))

    print('\n' + '=' * 72)
    print('汇总')
    print('=' * 72)
    bad = 0
    for label, ok in results:
        print('  %s  %s' % ('PASS' if ok else 'FAIL', label))
        bad += 0 if ok else 1
    print()
    print('结果：%s（%d/%d 通过）' % ('全过' if bad == 0 else '有失败', len(results) - bad, len(results)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())