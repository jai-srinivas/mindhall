# Mindhall

**One shared mind for every AI you use.**

Every AI you use remembers a different version of you. Tell Claude your launch moved and ChatGPT still has the old date. Your Hermes agent never heard about it at all.

Mindhall is one source of truth they all share: your work, your dates and the rules you want followed. Any AI can read it and write to it, and every change is signed, so you can see exactly which AI changed what.

```
Latest changes
  10:26  Hermes Time: Removed note: test
  10:15  ChatGPT: Moved Launch from 2026-11-20 to 2026-11-22
  09:28  Hermes: Added Launch on 2026-11-20
```

## How it works

Your mind has four rooms:

| Room | Holds |
| --- | --- |
| About you | Who you are, how you work, the rules every AI follows |
| Work | Your projects, clients or courses, and where each one stands |
| Dates | Every deadline and launch |
| Change Log | Every change, which AI made it, and when |

- **Every Hermes turn starts with a short Mindhall block**: about you, active work, the next two weeks of dates and the latest changes. If Mindhall and the agent's own memory disagree, Mindhall wins.
- **Agents write back** with five tools: `mindhall_read`, `mindhall_update_work`, `mindhall_set_date`, `mindhall_add_note` and `mindhall_remove`.
- **Moving a date updates it.** "Launch moved to Nov 22" changes the existing Launch date instead of adding a second, conflicting one.
- **Other AI apps share the same mind** through the included MCP server: ChatGPT, Claude, Cursor and anything else that speaks MCP.
- **It never breaks your agent.** If the mind can't be read, the turn goes ahead without it.

Mindhall is a regular plugin, not a memory provider, so it works alongside Honcho, Mem0 or whatever memory you already use.

## Install in Hermes

```bash
hermes plugins install jai-srinivas/mindhall --enable
hermes gateway restart
```

Start a new session and tell your agent about a project or a date. Then type `/mindhall` to see what it saved and who signed it.

## Connect ChatGPT, Claude and other AI apps

`mcp_server.py` serves the same mind over MCP, so ChatGPT, Claude, Claude Code and other MCP apps share it with your Hermes agents. In short:

1. Install it next to Hermes and create a secret.
2. Run it as an always-on service on `127.0.0.1:8765`.
3. Give it an HTTPS address: a free quick tunnel to try it, or your own domain to keep it.
4. Add `https://<your address>/<your secret>/mcp` as a custom connector in ChatGPT or Claude, with no authentication.

**[Step-by-step guide: Connect AI apps](https://github.com/jai-srinivas/mindhall/wiki/Connect-AI-apps)**, with every command, how to keep the address permanent, changing your secret, and troubleshooting.

Keep your address private: the secret in it is what protects your mind. Requests without it get a 404. Each app signs its own changes ("ChatGPT", "Claude") automatically.

## Read and edit your mind

The same server gives you a clean web page with your mind as simple tables. Open it in any browser:

```
https://<your-address>/<your-secret>/
```

- **Work, Dates and About you** as tables: click any cell to edit, add rows at the bottom, delete with ×.
- **The Change Log** shows every change and who made it, updating live as your AIs work.
- **Undo** any change, whether an AI or you made it. Undos are signed changes too, so they can be undone.
- Your edits are signed **You** (change it with `MINDHALL_WEB_NAME`).

The page loads nothing from other sites and never sends its address on, so the secret in it stays private. Don't share screenshots of the address bar.

## Settings

Set these in a profile's `.env` (`~/.hermes/.env` for the default profile, `~/.hermes/profiles/<name>/.env` for others). Mindhall reads them through Hermes's profile-aware settings, never by opening the file itself.

| Variable | What it does | Default |
| --- | --- | --- |
| `MINDHALL_AGENT_NAME` | The name this profile signs changes with | `Hermes <Profile>`, or `Hermes` for the default profile |
| `MINDHALL_DB` | Where this profile's mind is stored | `~/.mindhall/mindhall.db`, shared by every profile |

Every profile shares one mind by default, which is the point. Give a profile its own `MINDHALL_DB` to keep it separate, for example a demo or a personal agent.

## What Mindhall touches

- **One local SQLite file** at `~/.mindhall/mindhall.db` (or your `MINDHALL_DB`), plus `~/.mindhall/sessions.json`, which remembers which profile each session belongs to.
- **No network calls** from the Hermes plugin, no telemetry, no credentials.
- **The MCP server is optional** and listens only on localhost. It also serves the tables page described above. It is reachable from outside only if you expose it yourself, for example with a tunnel.
- Text is capped at 500 characters per note, name, step or date label.
- **What AIs see:** your mind is stored on your machine, but when an AI reads it, what it reads becomes part of that conversation and is processed by that AI's provider, like anything else you tell it.

## Updating

```bash
hermes plugins update mindhall
hermes gateway restart
```

If you installed Mindhall separately into a profile (for example from the desktop app's Install from Git), that profile keeps its own copy. Update each one too:

```bash
HERMES_HOME=~/.hermes/profiles/<name> hermes plugins update mindhall
```

`/mindhall` ends with the profile and name in use, so you can check any agent at a glance.

## Known issues

- **Signatures follow the profile Hermes assigns to each session.** Agents on their own profile, such as a profile's Telegram bot, sign with that profile's name. In some setups, desktop-app sessions run under the default profile whichever profile is selected in the app, so they sign with the default profile's name.
- The MCP server protects your mind with a secret address, not a login. Sign-in (OAuth) is planned.

## Develop

```bash
pip install pytest
pytest
```

The tests cover the engine and settings, and need no Hermes install.

## Coming next

- Sign-in for the MCP server, so the address needs no secret
- A hosted version with nothing to set up
- Memory provider mode, for people who want Mindhall as their main memory
- One-click undo from the Change Log

## License

MIT
