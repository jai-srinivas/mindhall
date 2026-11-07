# Connect ChatGPT, Claude and other AI apps

This guide sets up the Mindhall server on your own machine so ChatGPT, Claude, Claude Code and other MCP apps share the same mind as your Hermes agents. It also gives you the tables page for reading and editing your mind in a browser.

It is written for an Ubuntu server (such as AWS EC2) where Hermes already runs. Allow about 20 minutes.

**What you end up with**

- The Mindhall server running all the time, on `127.0.0.1:8765`
- A private HTTPS address that ChatGPT and Claude connect to
- Your tables page at that same address

---

## 1. Install the server

```bash
cd ~
git clone https://github.com/jai-srinivas/mindhall.git mindhall-server
cd mindhall-server
python3 -m venv .venv
.venv/bin/pip install "mcp>=2,<3"
```

If the `venv` line fails with "ensurepip is not available", install the missing package, then repeat the last two lines:

```bash
sudo apt install -y python3.12-venv   # match your Python version: python3 --version
rm -rf .venv
```

## 2. Create your secret

The secret is the key to your mind. This writes a new one straight into a private file, so you never copy or paste it:

```bash
python3 -c 'import secrets; print("MINDHALL_MCP_SECRET=" + secrets.token_urlsafe(32))' > ~/.mindhall-mcp.env
chmod 600 ~/.mindhall-mcp.env
```

To see it later: `cat ~/.mindhall-mcp.env`. Your secret is everything after the `=`.

Never post it, share it in a chat or show it on screen. Anyone with your address and secret can read and change your mind.

## 3. Run it all the time

This makes the server start on boot and restart if it ever stops. If your user isn't `ubuntu`, change the three places it appears.

```bash
sudo tee /etc/systemd/system/mindhall-mcp.service > /dev/null << 'EOF'
[Unit]
Description=Mindhall server
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/mindhall-server
EnvironmentFile=/home/ubuntu/.mindhall-mcp.env
ExecStart=/home/ubuntu/mindhall-server/.venv/bin/python mcp_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now mindhall-mcp
```

**Check it:**

```bash
sleep 6
systemctl is-active mindhall-mcp
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/wrong/mcp
```

You want `active` and then `404`. The 404 is good: the server is answering, and refusing an address without your secret.

## 4. Give it a secure address

ChatGPT and Claude connect over the internet, so the server needs an HTTPS address. Pick one option.

### Option A: quick tunnel (free, for trying it out)

No domain or account needed, but **the address changes every time the tunnel restarts**, and you then have to update it in every connected app.

```bash
# Install cloudflared (on Intel/AMD servers use the amd64 file instead of arm64; check with: uname -m)
curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb

# Start it in the background so it survives closing the terminal
nohup cloudflared tunnel --url http://127.0.0.1:8765 > ~/tunnel.log 2>&1 &
sleep 8
grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' ~/tunnel.log | head -1
```

The last line prints your address. Run it again any time you need to see it. To check whether the tunnel is still running: `pgrep -af "cloudflared tunnel --url"`.

### Option B: your own domain (permanent, recommended)

You need a domain whose DNS is managed by Cloudflare (a free account is enough). This example uses `mcp.example.com`.

```bash
cloudflared tunnel login                                   # opens a link: pick your domain
cloudflared tunnel create mindhall                         # prints a tunnel ID
cloudflared tunnel route dns mindhall mcp.example.com
```

Create the tunnel's config, replacing `TUNNEL_ID` (twice) with the ID it printed:

```bash
sudo mkdir -p /etc/cloudflared
sudo tee /etc/cloudflared/config.yml > /dev/null << 'EOF'
tunnel: TUNNEL_ID
credentials-file: /home/ubuntu/.cloudflared/TUNNEL_ID.json
ingress:
  - hostname: mcp.example.com
    service: http://127.0.0.1:8765
  - service: http_status:404
EOF
sudo cloudflared service install
sudo systemctl restart cloudflared
```

Then tell the Mindhall server its permanent name, so it checks every request's Host and Origin headers:

```bash
echo 'MINDHALL_MCP_ALLOWED_HOSTS=mcp.example.com' >> ~/.mindhall-mcp.env
sudo systemctl restart mindhall-mcp
```

If you were using a quick tunnel, stop it: `pkill -f "cloudflared tunnel --url"`.

**Check the address from outside:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://mcp.example.com/wrong/mcp
```

`404` means the address reaches your server.

## 5. Your two addresses

Put your address and secret together like this:

| What | Address |
| --- | --- |
| For AI apps | `https://<your address>/<your secret>/mcp` |
| Your tables page | `https://<your address>/<your secret>/` (note the slash at the end) |

## 6. Connect your AI apps

### ChatGPT (web)

Custom connectors depend on your ChatGPT plan and, on workspace plans, on your admin's settings.

1. Open **Settings → Apps** (it may say **Plugins**). If you don't see a way to add your own server, look for **Developer mode** under the advanced settings and switch it on.
2. Choose to add a **custom MCP server**.
3. **Name:** Mindhall. **Server URL:** your address for AI apps. **Authentication:** No authentication.
4. Tick the confirmation box and create it.
5. In a new chat, turn on Mindhall from the tools menu and ask: "What's in my Mindhall?"

### Claude (claude.ai and the desktop app)

Custom connectors need a paid Claude plan.

1. Open **Settings → Connectors** and choose **Add custom connector**.
2. **Name:** Mindhall. **URL:** your address for AI apps.
3. In a chat, turn on Mindhall and ask: "What's in my Mindhall?"

### Claude Code

```bash
claude mcp add --transport http mindhall "https://<your address>/<your secret>/mcp"
```

Each app signs its own changes in the Change Log ("ChatGPT", "Claude", "Claude Code") automatically.

## 7. Change your secret

Do this if the secret was ever shown or shared, or simply now and then:

```bash
python3 -c 'import secrets; print("MINDHALL_MCP_SECRET=" + secrets.token_urlsafe(32))' > ~/.mindhall-mcp.env.new
grep -v '^MINDHALL_MCP_SECRET=' ~/.mindhall-mcp.env >> ~/.mindhall-mcp.env.new   # keeps your other settings
mv ~/.mindhall-mcp.env.new ~/.mindhall-mcp.env && chmod 600 ~/.mindhall-mcp.env
sudo systemctl restart mindhall-mcp
```

The old address stops working immediately. Update it in every connected app and in your bookmark.

## 8. Updating

```bash
cd ~/mindhall-server && git pull && sudo systemctl restart mindhall-mcp
```

## Troubleshooting

| What you see | What it means | Fix |
| --- | --- | --- |
| `systemctl is-active` says `failed` | The server stopped while starting | `journalctl -u mindhall-mcp -n 20 --no-pager` shows why. "Set MINDHALL_MCP_SECRET" means the secret file is missing or empty: repeat step 2 |
| `curl` prints `000` | Nothing is answering on that port | The server is still starting (wait a few seconds) or has stopped: see the line above |
| `curl` prints `404` | It works | Use your secret in the address |
| An app says the connection failed | Usually the address changed | Quick tunnels get a new address on every restart: run the `grep` line in step 4 and update the app |
| The tables page says "Offline, retrying" | The page can't reach the server | Check the server and tunnel as above |

## Privacy

Your mind is stored on your machine, in `~/.mindhall/mindhall.db`. When an AI reads it, what it reads becomes part of that conversation and is processed by that AI's provider, like anything else you tell it. The tables page loads nothing from other sites and never sends its address on.
