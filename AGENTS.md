## Critical Rules 

1. NEVER start coding, exploring the codebase, or web-fetching until the user has finished their message and made an explicit request. If a message is cut off or ambiguous, ask for clarification — do not guess.
1.   Always run `pwd` (or PowerShell 的 `Get-Location`) to verify you're in the correct project directory before creating or modifying any files. 
1.  After any Python code change, run `pytest -x --tb=short` before offering to commit — catch bugs immediately.

## Version Control

- After completing any file-modifying task, always offer to commit and push with a descriptive Chinese commit message summarizing all changes. 
-  Before committing, run `git status` and `git diff --stat` to verify only intended files are staged — never accidentally commit unrelated changes. 
-  When the user says 'commit' or 'push', treat it as a full workflow: stage → commit → push to origin/main.



## Wiki & Bulk Operations 

- Wiki data lives in markdown files with YAML frontmatter. Always validate frontmatter structure before any bulk edit. 
- For any operation touching 10+ files, run it on 1-2 sample files first, verify correctness, then scale up. 
-  When scraping or fetching external data, implement exponential backoff for HTTP 429/567 rate limiting.

## Terminal / Shell

- 所有终端命令必须在 PowerShell 7（pwsh）中执行；调用终端工具时显式指定 pwsh，不得省略或依赖默认 shell。系统自带的 powershell.exe 是 5.1，写文件默认不是 UTF-8 No BOM，禁止使用。
- 默认禁止使用 Bash、WSL、Git Bash 或 cmd。确需使用 cmd 时，必须说明原因，不得跨 shell 拼接命令。
- 并行执行多条命令时，每条命令分别显式指定 pwsh。
- 复杂多行代码使用 PowerShell here-string，并显式调用项目解释器。
- 禁止使用依赖 Bash/Zsh 的 heredoc、`cat <<EOF` 等 Unix 专属写法。PowerShell here-string 管道给 `python -` 可以使用。
- 需要传递字符串值、路径或正则时使用单引号；PowerShell 单引号内不需要反斜杠转义，连续两个单引号表示一个单引号。
- 读取文件使用 UTF-8，生成文件使用 UTF-8 No BOM（pwsh 7 写文件默认即 UTF-8 No BOM，符合本规则）。
- pwsh 管道/重定向输出默认跟随系统 GBK 代码页（本机 chcp 936），命令需要输出中文时先执行 `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)` 切换为 UTF-8。
- Python 侧会话环境已注入 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`，读取/输出均为 UTF-8，无需额外设置；项目代码中所有 `open()` 均已显式指定 encoding，保持该写法。

## gstack

- Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.
- Available gstack skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/setup-gbrain`, `/retro`, `/investigate`, `/document-release`, `/document-generate`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore