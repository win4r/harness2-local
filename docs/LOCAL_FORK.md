# Harness2 本地 fork

版本 `0.1.0+local.1`。基于上游 `1cfe254e8d25b15f37048c3730134686abe60335`，保留原始 Git 历史和 Apache-2.0 许可；本地分支为 `local/main`，`upstream` 指向 Google 原仓库。本版本没有上传为 GitHub 远程 fork，也不覆盖此前的实验仓库或结果。

## 本地启动

当前目录已有独立 `.venv`。直接双击 `start-demo.command`，或在仓库根目录运行：

```bash
./start-demo.command
```

它运行两项本地账本任务，采用真实 Python 执行和确定性编辑器，验证并行与顺序流程，不调用模型、不需要凭据。结果在 `runs/local-demo/demo.json`；再次运行会恢复已完成任务。

在另一台机器或从源码包恢复后，先安装 uv，再运行：

```bash
./scripts/local-install.sh
./start-demo.command
```

安装只作用于这个 fork 的 `.venv`，支持 Python 3.12/3.13。正式模型、JobBench、LAB、WorkBuddy 的依赖和凭据仍按上游 [SETUP.md](SETUP.md) 配置。macOS 离线演示不代表所有 Linux/Docker 正式基准已验证；本轮未重新跑付费模型或官方基准。

## 可选人工反馈

借鉴 [PR #6](https://github.com/google-research/harness2/pull/6)，默认关闭。每项任务对应一个 UTF-8 Markdown 文件，任务 ID 中 `/` 替换为 `__`。目录必须是普通文本文件的平铺目录；单个文件最多 64 KiB，合计最多 1 MiB，不接受符号链接、嵌套目录或会导致文件名冲突的任务 ID。

```bash
mkdir -p local-notes
printf '%s\n' '核对贷项与借项都计入总额，并说明采用方案的依据。' > local-notes/ledger-1.md
.venv/bin/harness2-demo --output runs/feedback-demo --k 2 --feedback-dir local-notes
```

正式入口也接受 `harness2 --feedback-dir /绝对路径/notes`。整批反馈在执行前校验并固定为内存快照，中途改文件不会改变本轮证据。反馈只进入当前任务的最终选择与经验反思；之后可能经经验库影响后续任务。`k=1` 仍只有一个最终候选，不为反馈额外调用选择器。

人工反馈可以包含人的偏好或提示，但不是正式裁判输出的导入接口。启用后的运行会标记为人工参与；不能与无反馈基准直接混算。对同一运行目录再次执行时必须保留 `--feedback-dir`，需要恢复无反馈条件时使用新运行目录。候选字母 ID 只在候选集合不变时有效，反馈应说明产物和行为，不应把一次运行的 `A/B` 当作永久标识。

## 修复范围

- 模型通道、模型名、effort、步数、有效超时和回收路径参与 proposer 缓存身份，避免返回另一配置的旧回复或旧脚本。
- OpenCode 的致命会话错误优先于残留答案处理，不再把错误会话当成功缓存。
- Codex 的文件编辑、MCP、协作和网页工具事件保留在轨迹证据中，含失败状态，并沿用原有截断限制。
- 被拒绝的编辑在重试时保留合法部分；符号链接与越界路径不进入可编辑树，保留的执行辅助脚本不能被替换。
- 同一运行目录的首次配置认领串行化，避免并发混入不同模型。
- 恢复时校验经验库前缀和实际选择/反思提示；已完成的所请求模式可从部分成功的摘要恢复。任务输入、任务说明、基线或编辑提示等关键身份变化会明确拒绝旧目录，要求新目录或 tag，防止旧执行器缓存再次混入。
- 每次实际选择保存 `selected_at`，开始重跑前即使旧选择失效。重跑或重新评分失败时，旧分数不会冒充当前结果；缺失时间戳的历史评分也不能据此保留。只重跑一个模式时，保留另一个模式的有效记录。
- 直接 API 重用经验库时，当前任务自己的旧经验不参与输入身份或再次学习；验证成功的恢复会还原该经验，避免重复执行，也保留其他任务的经验。
- 评分不完整、异常或格式非法时保持未评分；JobBench 裁判异常、LAB 冲突结论、不同任务集合的报告比较和报告子进程失败分别处理。
- 补齐 `.env.example`、本地忽略规则和源码发行包内的 runner/许可/校验清单。

这些是对未合并 PR 的审阅后移植与本地修正，不代表上游已接纳。全部 PR 编号、作者和审查时的 head SHA 见 [LOCAL_UPSTREAM_REVIEW.json](LOCAL_UPSTREAM_REVIEW.json)。PR #42 与 #28 重复；#21 的经验前缀修复合并进 #36 的实现。

## 使用边界

本版保持上游 finals-only 选择策略：`status=ok` 仍只表示流程完成，不等于答案正确，也没有增加自动语义验收或失败时从基线兜底。需要正确性、速度或业务指标保证时，必须另设独立检查。

旧版没有输入身份记录的任务缓存会被拒绝，必须使用新目录。经验前缀或选择/反思提示发生变化时，本版可能重新运行；有模型的运行会产生新调用。不要把旧实测产物复制进新 fork 的 `runs/`。当前未加入 issue #5 的自定义任务文件夹执行器，也未加入 issue #4 的 `--max-usd`：执行器费用仍不完整，不能将记录中的零成本视为免费或可靠预算控制。可用已有 `--max-tasks` 限制任务规模。

本轮回归、离线双模式演示、反馈恢复和打包检查结果写入 `docs/LOCAL_VALIDATION.json`。最终 Git bundle、归档和提交的一致性记录，以及原版失败证据和 PR 原始数据，保存在本 fork 外的 `outputs/fork-review/`。

## 更新和保留本地版本

```bash
git status
git fetch upstream
git log --oneline local/main..upstream/main
```

先查看变化再决定如何合并；不要将 `local/main` 重置到上游。交付的 Git bundle 可在新目录用 `git clone -b local/main /路径/harness2-local.bundle 新目录` 恢复完整本地版本；从 bundle 恢复后可执行 `git remote add upstream https://github.com/google-research/harness2.git` 重新添加上游远端。普通源码包需要重新创建 `.venv`；wheel 不含所有第三方基准资源，正式基准应从源码目录运行。
