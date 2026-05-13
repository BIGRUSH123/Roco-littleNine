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