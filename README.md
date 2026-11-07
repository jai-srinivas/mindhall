# Mindhall

One shared mind for every AI you use.

Your AIs each remember a different version of you. Mindhall gives them one source of truth: your work, your dates and the rules you want followed, plus a Change Log that records which AI changed what, and when.

This repo is the Hermes plugin. An MCP server for Claude, ChatGPT and Cursor is coming next, on the same database.

## Install

```bash
hermes plugins install <your-github-user>/mindhall --enable
```

Restart Hermes. Then tell your agent about a project or a date, and type `/mindhall` to see what it saved.

## What it does

- **Every turn**, Hermes sees a short Mindhall block: about you, active work, the next two weeks of dates and the latest changes. If Mindhall and the agent's own memory disagree, Mindhall wins.
- **Agents write back** with five tools: `mindhall_read`, `mindhall_update_work`, `mindhall_set_date`, `mindhall_add_note` and `mindhall_remove`.
- **Every change is signed** in the Change Log with the agent's name.
- **Moving a date updates it.** "Launch moved to Nov 20" changes the existing Launch date instead of adding a second one.
- **It never breaks your agent.** If the database can't be read, the turn goes ahead without it.

Mindhall is a regular plugin, not a memory provider, so it works alongside Honcho, Mem0 or whatever memory you already use.

## Connect Claude, ChatGPT and other AI apps

`mcp_server.py` lets any app that supports MCP use the same mind as your Hermes agents. Every change is signed with the app's name, so the Change Log shows "Claude: Moved Launch to Nov 20".

```bash
pip install "mcp>=2"
export MINDHALL_MCP_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
python3 mcp_server.py
```

It listens on `127.0.0.1:8765`. Put it behind HTTPS (for example a Cloudflare tunnel), then add `https://<your-address>/<secret>/mcp` as a custom connector in Claude or ChatGPT. Keep that address private: the secret in it is what protects your mind.

## Settings

Each Hermes profile reads its own `.env` file (`~/.hermes/.env` for the default profile, `~/.hermes/profiles/<name>/.env` for others):

| Variable | What it does | Default |
| --- | --- | --- |
| `MINDHALL_AGENT_NAME` | The name this profile signs changes with | `Hermes <Profile>`, or `Hermes` for the default profile |
| `MINDHALL_DB` | Where this profile's mind is stored | `~/.mindhall/mindhall.db`, shared by all profiles |

By default every profile shares one mind, which is the point. Give a profile its own `MINDHALL_DB` to keep it separate, for example a demo or a personal agent. Run `/mindhall` in any profile to see the name it signs with.

## Develop

```bash
pip install pytest
pytest
```

The tests cover the engine and need no Hermes install.

## Coming next

- MCP server, so Claude, ChatGPT and Cursor share the same mind
- Memory provider mode, for people who want Mindhall as their main memory
- One-click undo from the Change Log

## License

MIT
