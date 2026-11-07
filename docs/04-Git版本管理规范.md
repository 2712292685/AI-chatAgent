# Git 版本管理规范

| 项目 | 内容 |
| --- | --- |
| 文档版本 | 1.0 |
| 强制对象 | 所有参与本项目的开发者，**尤其是 AI 开发 Agent** |
| 效力 | 本规范为强制约束。Agent 与规范冲突的"最佳实践"一律以本规范为准 |

---

## 0. 三条根本原则

1. **历史必须可读。** 半年后回看 `git log`，要能看懂每次改动为什么发生，而不是一堆 `update`、`fix`、`123`。
2. **每个提交必须可回滚。** 单个提交应是一个完整的逻辑单元，撤销它不会让程序半死不活。
3. **每一步都留痕。** Agent 在动 Git 之前必须先说话——说清"要做什么"和"现在到哪了"。人不需要盯着它，但要能随时接管。

---

## 1. 仓库与分支

### 1.1 当前模型：单 `main` 到底

```
main ──●──●──●──●──●──►   （所有提交直接落在 main）
        │  │  │  │  │
      v0.1 v0.1.1 v0.2 v0.2.1 v0.3 ...
```

**规则**

- `main` 是唯一长期分支，**永远处于可运行状态**。
- 所有开发直接提交到 `main`。不建 `develop`、不建 `feat/*`、不建 `release/*`。
- 不使用 `git merge` 产生合并提交（没有分支就没有合并）。

**为什么不做分支模型**

单人开发 + AI 辅助，分支带来的唯一收益（并行隔离）用不上，成本（合并冲突、分支漂移、忘记切换）却是实打实的。规范里保留这一节，是为了将来真有多人协作时有明确迁移路径。

### 1.2 将来切换条件

满足以下**任一**条件时，才切换为分支模型，并同步修订本节：

- 出现第二个经常性提交代码的人
- 需要同时维护两个可发布版本
- 需要 PR 评审流程

届时采用：`main`（稳定）+ `feat/<kebab-case>` / `fix/<kebab-case>` 短分支，`--no-ff` 合并，合并后删除分支。

### 1.3 远程仓库

- 当前阶段**允许没有远程仓库**。所有规范只约束本地历史。
- 一旦添加远程（`git remote add origin ...`），以下立即生效：
  - 推送前必须已完成 §6 的提交前检查
  - 禁止对 `main` 执行 `git push --force` / `--force-with-lease`
  - 禁止 rebase 或 amend 已经推送的提交

---

## 2. 提交信息规范

采用 **Conventional Commits**，**一律使用英文**。

### 2.1 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 2.2 type（必填，从下表取一个）

| type | 用于 | 版本号影响 |
| --- | --- | --- |
| `feat` | 新增功能 | MINOR +1 |
| `fix` | 修复缺陷 | PATCH +1 |
| `docs` | 仅文档变更 | 不升版（随当版发布） |
| `refactor` | 重构，不改变外部行为 | PATCH（若单独发布） |
| `perf` | 性能优化 | PATCH |
| `test` | 增删改测试 | 不升版 |
| `build` | 构建、依赖、打包配置 | PATCH（若单独发布） |
| `ci` | 持续集成配置 | 不升版 |
| `style` | 代码格式（空格、引号），无逻辑变化 | 不升版 |
| `chore` | 杂项：版本号、gitignore、脚手架 | PATCH（若随发布） |
| `revert` | 回滚某次提交 | 视被回滚内容而定 |

### 2.3 scope（选填但强烈建议）

取**模块名或领域名**，小写，与目录结构对应：

`cli` `config` `persona` `llm` `session` `context` `render` `errors` `project` `docs` `deps`

跨多个模块时，取最主要的一个，或在 body 中说明。

### 2.4 subject（必填）

- 祈使句，动词开头：`add` / `fix` / `remove` / `rename` / `switch` / `trim` / `cover`
- 全小写（专有名词除外，如 `DeepSeek`、`Windows`）
- **不超过 50 个字符**
- 结尾**不加句号**
- 不要写成"修改了xxx"，要写成"让代码做了什么"

| 不好 | 好 |
| --- | --- |
| `update code` | `add token budget trimming to context payload` |
| `fix bug` | `fix orphaned assistant message after interrupt` |
| `修改了流式输出` | —（必须英文） |
| `feat(cli): add backslash continuation.` | `feat(cli): add backslash continuation` |

### 2.5 body（建议写）

- 与 subject 之间空一行
- 每行不超过 72 个字符
- **说明"为什么"，不要复述"改了什么"**——改了什么看 diff 就知道
- 用 `-` 列点

```text
feat(context): trim history by token budget

Without trimming, long sessions eventually exceed the model context
window and the request fails with 400.

Trimming happens in pairs so the payload never starts with an assistant
message, which the API rejects.

- estimate tokens conservatively (CJK=1.0, others=0.4)
- drop oldest round first when over budget
```

### 2.6 footer（按需）

- 破坏性变更：`BREAKING CHANGE: <说明>`（**必须**写，且版本号按 §3.2 处理）
- 关联需求：`Refs: FR-05`
- 回滚：`Reverts: <commit-hash>`

### 2.7 一次提交一个逻辑单元

判断标准：**这个提交的标题能用一句话说完，且这句话里没有"和""以及""顺便"**。

反例：

```text
feat(cli): add multiline input and ansi colors and retry logic
```

应拆成三个提交。

---

## 3. 版本号规范

采用 SemVer 三段式 `MAJOR.MINOR.PATCH`，但因为是 0.x 阶段（未承诺稳定 API），规则做如下约定：

### 3.1 编号含义

| 段 | 含义 | 本项目当前取值规则 |
| --- | --- | --- |
| MAJOR | 不兼容的重大变更 | MVP 阶段**保持 0**，不进 1 |
| MINOR | 新增功能 | 每完成一个开发阶段 +1：`v0.1.0` → `v0.2.0` |
| PATCH | 缺陷修复、小幅调整、文案修改 | 每次独立发布 +1：`v0.2.0` → `v0.2.1` |

### 3.2 升降规则

| 变更内容 | 版本动作 | 示例 |
| --- | --- | --- |
| 完成一个计划版本的全部范围 | MINOR +1，PATCH 归零 | `v0.2.3` → `v0.3.0` |
| 修 Bug | PATCH +1 | `v0.2.0` → `v0.2.1` |
| 改文案、改默认参数、改 `persona.toml` 示例内容 | PATCH +1 | `v0.2.1` → `v0.2.2` |
| 补充测试、重构（不改行为） | 可随下次发布；若要单独发布则 PATCH +1 | `v0.2.2` → `v0.2.3` |
| 破坏性变更（0.x 阶段） | MINOR +1，并在 footer 写 `BREAKING CHANGE` | `v0.2.3` → `v0.3.0` |
| 仅改文档、仅改 CI | **不升版**，但照常提交 | — |

> 需求方明确要求：**小功能更新也必须有独立的小版本号**，不允许"改了一堆但版本号不动"。

### 3.3 版本号出现的位置（必须一致）

| 位置 | 内容 |
| --- | --- |
| `pyproject.toml` 的 `[project] version` | `0.2.0`（**无 v 前缀**） |
| `src/chatagent/__init__.py` 的 `__version__` | `"0.2.0"` |
| Git tag | `v0.2.0`（**有 v 前缀**） |
| `CHANGELOG.md` 的标题 | `## [0.2.0] - 2026-11-08` |

四处必须同时更新，且在同一次提交里完成：

```text
chore: bump version to 0.2.0
```

---

## 4. 标签规范

### 4.1 格式

- 一律使用 **annotated tag**（带信息的标签），不用 lightweight tag（唯一例外见 4.3）
- 标签名：`v` + 完整版本号，如 `v0.2.0`、`v0.2.1`

### 4.2 标签信息格式

```text
v0.2.0

Conversation support: multi-turn history, streaming replies,
and token-budget trimming for long sessions.

- feat(llm): switch chat completion to streaming
- feat(session): keep multi-turn history across requests
- feat(cli): stream assistant replies to terminal

Refs: docs/03-开发计划.md §4
```

第一行是版本号，空一行写一句话主题，再空一行列本版主要提交，最后一行标注依据。

### 4.3 里程碑标签

MVP 全部完成时，在 `v0.4.0` 同一提交上额外打一个 lightweight tag `mvp`：

```powershell
git tag -a v0.4.0 -F tag_v0.4.0.txt
git tag mvp v0.4.0
```

### 4.4 打标签的时机

**只有在以下全部成立后，才允许打 tag：**

1. 四项质量检查全绿（§6）
2. 该版本验收清单逐条通过
3. 版本号四处已同步更新
4. `CHANGELOG.md` 已写入该版本条目
5. **`docs/05-开发进度.md` 已把该版本标记为「✅ 已完成」并填好日期、tag、验收结果**
6. 工作区干净（`git status` 无未提交改动）

---

## 5. CHANGELOG 规范

采用 [Keep a Changelog](https://keepachangelog.com/) 的简化结构。

### 5.1 文件位置

仓库根目录 `CHANGELOG.md`。

### 5.2 结构

```markdown
# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.2.1] - 2026-11-08

### Fixed
- Drop orphaned assistant message when generation is interrupted

## [0.2.0] - 2026-11-08

### Added
- Multi-turn conversation history
- Streaming assistant replies
- Token-budget based context trimming

### Changed
- `llm.chat_once` replaced by streaming `chat_stream`

## [0.1.0] - 2026-11-07

### Added
- Initial project skeleton
- Persona loading and system prompt rendering
- Single-turn chat against `deepseek-flash`
```

### 5.3 分节用词

| 分节 | 用于 |
| --- | --- |
| `Added` | 新功能 |
| `Changed` | 已有行为的变化 |
| `Deprecated` | 即将移除 |
| `Removed` | 已移除 |
| `Fixed` | 缺陷修复 |
| `Security` | 安全相关 |

### 5.4 写法

- 面向**使用者**描述，不写实现细节（"支持多行输入"而不是"在 cli.py 加了 while 循环"）
- 每条一行，动词开头
- 日期用 `YYYY-MM-DD`

---

## 6. 提交前检查清单

**每一次 `git commit` 之前都必须完成**，缺一项不得提交。

```powershell
# 1. 四项质量检查（全绿才继续）
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q

# 2. 确认改动范围符合预期（没有意外文件）
git status
git diff --stat

# 3. 逐行审阅暂存内容
git diff --staged

# 4. 密钥扫描（必须无输出）
git diff --staged | Select-String -Pattern "sk-[a-zA-Z0-9]{20,}"

# 5. 确认没有把敏感/临时文件加进来
git status --porcelain | Select-String -Pattern "\.env|\.venv|__pycache__|\.pytest_cache|\.mypy_cache"
```

macOS / Linux 下第 4、5 条改用：

```bash
git diff --staged | grep -nE "sk-[a-zA-Z0-9]{20,}"
git status --porcelain | grep -E "\.env|\.venv|__pycache__|\.pytest_cache|\.mypy_cache"
```

**任一项不通过 → 停止，向人报告，不得提交。**

---

## 7. Agent 操作协议

本章是**专门为 AI Agent 写的**，也是需求方最关心的一条：Agent 可以自己操作 Git，但**每一步都要说清楚**。

### 7.1 总则

1. Agent **有权**自行执行 `add` / `commit` / `tag`，无需每次征求同意。
2. 但**每一次** Git 命令执行**之前**，必须先输出说明：
   - **只读命令**（`status` / `log` / `diff` / `show`）：一行说明即可。
   - **写操作命令**（`add` / `commit` / `tag` / `push` / `reset` / `restore` / `revert` / `checkout`）：必须输出完整**操作预告块**。
3. 预告块必须包含**当前版本状态卡**——这是"当前版本是什么内容"的落地方式。
4. 执行后必须输出**结果回执**。
5. **不得静默执行任何写操作。** 没有预告块就执行，视为违规。

### 7.2 版本状态卡

每次写操作前，先输出这张卡。所有字段都必须真实填写，不允许"略"。

```text
═══════════════ 版本状态 ═══════════════
当前版本：v0.2.0（开发中）
版本主题：对话成型 —— 多轮历史 + 流式输出 + 上下文裁剪
已完成：
  ✓ feat(context): 预算裁剪与 token 估算
  ✓ feat(llm): 改为流式调用
  ✓ feat(session): 多轮历史维护
  ✓ test(context): 裁剪不变量测试
进行中：feat(cli) 接入流式输出
待办：chore 版本号提升、打 tag v0.2.0
═════════════════════════════════════════
```

**卡片数据来源**（Agent 必须实际查证，不得凭记忆编造）：

| 字段 | 来源 |
| --- | --- |
| 当前版本 | `git describe --tags --abbrev=0`（无 tag 时取 `0.0.0-dev`） |
| 版本主题 | `docs/03-开发计划.md` 的版本总览表 |
| 已完成 / 待办 | 本版开发计划 §x.2 范围内清单，逐项对照 `git log <上次tag>..HEAD` |
| 进行中 | 本次操作要处理的那一项 |

### 7.3 操作预告块

紧跟在状态卡之后：

```text
───── Git 操作预告 ─────
序号：本版第 5 次提交
操作：git add src/chatagent/cli.py src/chatagent/render.py
     git commit -m "..."
目的：让 cli 以流式方式打印模型回复
影响文件：
  M src/chatagent/cli.py
  M src/chatagent/render.py
提交信息：
  feat(cli): stream assistant replies to terminal
风险：无（不改动已有函数签名）
────────────────────────
```

**字段说明**

| 字段 | 要求 |
| --- | --- |
| 序号 | 本版第几次提交，便于人回溯 |
| 操作 | 逐条列出要执行的命令（可脱敏省略长消息体） |
| 目的 | 一句话说明为什么要做这次提交 |
| 影响文件 | 从 `git status` 实际读取，标 `A`/`M`/`D` |
| 提交信息 | 完整的 subject（有 body 时写出 body 首行） |
| 风险 | 是否有破坏性、是否改公共接口、是否可能需回滚 |

### 7.4 执行后回执

```text
───── 结果 ─────
✅ 成功
提交：a3f9c21
信息：feat(cli): stream assistant replies to terminal
剩余待办：2 项（版本号提升、打 tag）
────────────────
```

失败时：

```text
───── 结果 ─────
❌ 失败
命令：git commit ...
原因：pre-commit 检查未通过（mypy 报错 2 处）
已执行：git add 已完成，工作区仍干净
下一步建议：修复 test_context.py:42 的类型错误后重试
────────────────
```

失败时**必须**说明"已执行的部分"与"下一步建议"，让人不用去猜现在的仓库处于什么状态。

### 7.5 各类操作的具体要求

| 操作 | 额外要求 |
| --- | --- |
| `git add` | 必须逐个列出路径，**禁止 `git add .` / `git add -A`**（除非本次改动确实只有新增的独立文件，且已在预告中说明） |
| `git commit` | 提交前已跑完 §6 检查；使用多行消息（`-m` 多次或 `-F` 文件）；不得用 `--no-verify` 跳过钩子 |
| `git tag` | 只在 §4.4 条件全满足时执行；必须是 annotated；打 tag 前该版本必须已在 `docs/05-开发进度.md` 中标记为「已完成」；执行后 `git show <tag>` 验证 |
| `git push` | 首次推送前确认远程已配置；推送前再跑一次 §6；禁止 `--force` |
| `git reset` | **只能 `--soft` 或 `--mixed`**，且必须单独预告并说明原因；**禁止 `--hard`**（会丢工作区） |
| `git restore` / `checkout --` | 丢弃改动前必须列出被丢弃的文件，并说明"这些改动将永久丢失" |
| `git revert` | 用于已推送提交的回滚；需先 `git log` 定位并展示被回滚提交 |
| `git rebase` | 未推送提交间可做；已推送**禁止** |
| `git commit --amend` | 仅当未推送且仅修改消息或补充遗漏文件时允许；必须预告说明"将改写上一条提交" |

### 7.6 一次典型提交的完整交互

```text
═══════════════ 版本状态 ═══════════════
当前版本：v0.2.0（开发中）
版本主题：对话成型 —— 多轮历史 + 流式输出 + 上下文裁剪
已完成：
  ✓ v0.1.0 骨架
  ✓ feat(context): 预算裁剪与 token 估算
  ✓ feat(llm): 改为流式调用
进行中：feat(session) 多轮历史维护
待办：feat(cli) 流式接入、chore 版本号、tag
═════════════════════════════════════════

───── Git 操作预告 ─────
序号：本版第 3 次提交
操作：git add src/chatagent/session.py
     git commit -F .git/COMMIT_EDITMSG_TMP
目的：把多轮历史收进 ChatSession，使模型能记住前文
影响文件：
  A src/chatagent/session.py
提交信息：
  feat(session): keep multi-turn history across requests
风险：新增模块，未改动现有调用方，无破坏性
────────────────────────

[执行]

───── 结果 ─────
✅ 成功
提交：7c1e08b
信息：feat(session): keep multi-turn history across requests
剩余待办：2 项
────────────────
```

### 7.7 会话开始时的首次报告

Agent 在**接手一个已有仓库**时，第一步不是写代码，而是报告现状：

```text
───── 仓库现状 ─────
分支：main
最新 tag：v0.2.0
HEAD：7c1e08b feat(session): keep multi-turn history across requests
工作区：干净
未推送提交：无远程
下一步建议：v0.2.0 范围内还剩 [feat(cli) 流式接入]、[版本号提升]、[打 tag]
────────────────────
```

---

## 8. 禁止事项

以下操作**一律禁止**，没有例外，没有"这次特殊"。

| 编号 | 禁止 | 原因 |
| --- | --- | --- |
| F-01 | 对 `main` 执行 `git push --force` 或 `--force-with-lease` | 覆盖他人历史 |
| F-02 | rebase 或 amend 已推送到远程的提交 | 破坏他人本地历史 |
| F-03 | `git reset --hard` | 静默丢失未提交工作 |
| F-04 | 提交任何真实密钥、token、密码 | 泄露 |
| F-05 | 提交 `.env`、`.venv/`、`__pycache__/`、`.pytest_cache/`、`.mypy_cache/`、`dist/`、`build/` | 污染仓库 |
| F-06 | 一次性提交多个不相关改动 | 不可回滚 |
| F-07 | 使用 `git commit --no-verify` 跳过检查 | 绕过质量门 |
| F-08 | 使用 `wip`、`tmp`、`update`、`fix`、`123`、`。` 等无意义提交信息 | 历史不可读 |
| F-09 | 未输出预告块就执行写操作 | 违反 §7 |
| F-10 | 修改已有的 tag（删除重打） | 版本号语义被破坏 |
| F-11 | 在检查未通过时提交 | 破坏 `main` 可运行性 |
| F-12 | 提交 `docs/` 之外的文档时夹带代码改动 | 混淆变更类型 |

---

## 9. `.gitignore` 基线

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.eggs/

# 虚拟环境
.venv/
venv/

# 工具缓存
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# 构建产物
dist/
build/

# 本地环境与密钥
.env
.env.*
!.env.example

# 编辑器
.vscode/
.idea/
*.swp

# 操作系统
.DS_Store
Thumbs.db
```

---

## 10. 异常情况处理

### 10.1 提交后发现提交信息写错（未推送）

允许 `git commit --amend`，但必须：

1. 输出预告块，明确写"将改写上一条提交"
2. 说明改写原因（typo / 漏加文件）

### 10.2 提交后发现漏了文件（未推送）

```powershell
git add <漏掉的文件>
git commit --amend --no-edit
```

同样需要预告块。

### 10.3 已推送的提交需要回滚

**禁止** reset + force push。改用：

```powershell
git revert <commit-hash>
```

并提交一条 `revert: <原提交信息>` 的提交，body 中写 `Reverts: <hash>`。

### 10.4 质量检查在提交后才发现失败

立即修复，作为**新提交**提交：

```text
fix(context): correct token estimate for mixed cjk text
```

**不要**回头修改历史。历史里出现一个"当时的错误"是可接受的，历史被篡改是不可接受的。

### 10.5 需要临时保存未完成的工作

不要在 `main` 上提交半成品。用 stash：

```powershell
git stash push -m "wip: streaming refactor"
```

并在报告里说明 stash 的存在与内容。

---

## 11. 快速参考卡

贴在 Agent 的提示词里也不嫌长的那一页。

```text
提交格式：<type>(<scope>): <subject>      英文 / 祈使句 / ≤50 字符 / 无句号

常用 type：feat fix docs refactor test chore build perf
常用 scope：cli config persona llm session context render errors project docs deps

提交前四连（必须全绿）：
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy src
  uv run pytest -q

版本号：MINOR=新功能  PATCH=修复/调整  四处同步：pyproject / __init__ / tag / CHANGELOG

打 tag 前六问：
  1. 四项检查绿了吗
  2. 验收清单过了吗
  3. 版本号四处同步了吗
  4. CHANGELOG 写了吗
  5. 进度文档标「已完成」了吗
  6. git status 干净吗

写操作前必说：版本状态卡 → 操作预告块 → （执行）→ 结果回执
绝对不做：force push / rebase 已推送 / reset --hard / 提交密钥 / 跳过检查
```

---

## 附录 A：本规范的落地检查

在新会话开始、或接手他人工作前，Agent 应确认：

- [ ] 已读本文件全文
- [ ] 已执行 `git log --oneline -20` 了解近期历史
- [ ] 已确认当前 tag 与版本号
- [ ] 已确认工作区状态
- [ ] 已理解"写操作前必须输出状态卡与预告块"

未完成以上确认就开始写代码或提交，视为未遵守规范。
