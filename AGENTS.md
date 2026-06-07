## Critical Rules 

1. NEVER start coding, exploring the codebase, or web-fetching until the user has finished their message and made an explicit request. If a message is cut off or ambiguous, ask for clarification — do not guess.
1.   Always run `pwd` (or `echo %CD%` on Windows) to verify you're in the correct project directory before creating or modifying any files. 
1.  After any Python code change, run `pytest -x --tb=short` before offering to commit — catch bugs immediately.

## Version Control

- After completing any file-modifying task, always offer to commit and push with a descriptive Chinese commit message summarizing all changes. 
-  Before committing, run `git status` and `git diff --stat` to verify only intended files are staged — never accidentally commit unrelated changes. 
-  When the user says 'commit' or 'push', treat it as a full workflow: stage → commit → push to origin/main.



## Wiki & Bulk Operations 

- Wiki data lives in markdown files with YAML frontmatter. Always validate frontmatter structure before any bulk edit. 
- For any operation touching 10+ files, run it on 1-2 sample files first, verify correctness, then scale up. 
-  When scraping or fetching external data, implement exponential backoff for HTTP 429/567 rate limiting.

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