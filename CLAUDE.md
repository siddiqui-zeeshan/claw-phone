# Project Instructions

## Auto-update README

After implementing any new feature or significant change, automatically update README.md in the background to reflect the change. Use a background agent for this so it doesn't block the main workflow.

## Deployment

The bot runs on a Linux machine accessible via `ssh deploy-host`. It is installed as a pip package in `~/.spare-paw/venv/` (no git checkout on the host). After code changes:
1. Push to GitHub
2. `ssh deploy-host "~/.spare-paw/venv/bin/pip install --upgrade git+https://github.com/siddiqui-zeeshan/spare-paw.git"`
3. `ssh deploy-host "systemctl --user restart spare-paw"`
4. Check logs: `ssh deploy-host "journalctl --user -u spare-paw -n 50 --no-pager | grep -v 'getUpdates'"`

## Prompt files

The bot loads personality/context from `~/.spare-paw/` on every turn:
- `IDENTITY.md` — bot personality
- `USER.md` — user preferences
- `SYSTEM.md` — device capabilities and behavior rules
