# Deploying rare-earth (cloud server + client exe)

The **server** runs on a cloud box with a public IP; players run the **client**
from source (Python), choose **Multiplayer**, and type the server's address.

The world autosaves and reloads, so the server is a persistent shared world.
(No player accounts yet — each connection is a fresh player; the *world*
persists.)

---

## A. Deploy the server

Pick one. A plain VPS / droplet is simplest for raw TCP; fly.io works but needs
a dedicated IPv4.

### Option 1 — DigitalOcean droplet (or any Ubuntu VPS) — simplest

```bash
# on the droplet (Ubuntu 22.04/24.04), as root:
apt update && apt install -y python3 python3-pip git \
    libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0 \
    libfreetype6 libportmidi0

git clone <your-repo-url> rare-earth && cd rare-earth
pip3 install -r requirements.txt          # or: pip3 install pygame==2.6.1

ufw allow 5555/tcp                         # open the port (+ any DO cloud firewall)

# quick test run (Ctrl-C to stop):
SDL_VIDEODRIVER=dummy python3 src/server.py
```

Players connect to `DROPLET_PUBLIC_IP:5555`.

**Always-on** via systemd — create `/etc/systemd/system/rare-earth.service`:

```ini
[Unit]
Description=rare-earth game server
After=network.target

[Service]
WorkingDirectory=/root/rare-earth
Environment=SDL_VIDEODRIVER=dummy
Environment=SDL_AUDIODRIVER=dummy
Environment=RARE_EARTH_SAVE=/root/rare-earth/saves/server.json
ExecStart=/usr/bin/python3 src/server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now rare-earth
journalctl -u rare-earth -f     # watch logs / connections
```

The world persists on the droplet disk at `saves/server.json`.

### Option 2 — fly.io (Docker)

```bash
fly launch --no-deploy            # reuse the included Dockerfile + fly.toml; set your app name
fly volumes create rare_earth_data --size 1 -r <region>
fly ips allocate-v4               # raw TCP needs a DEDICATED IPv4 (small monthly cost)
fly deploy
fly ips list                      # the dedicated IPv4 is the server address
```

Players connect to `THAT_IPv4:5555`. Logs: `fly logs`.

### Option 3 — any Docker host

```bash
docker build -t rare-earth .
docker run -d --name rare-earth -p 5555:5555 -v rare_earth_data:/data --restart unless-stopped rare-earth
```

Players connect to `HOST_IP:5555`.

---

## B. Run the client (from source)

Players need Python 3.11+ and the repo:

```bash
git clone <your-repo-url> rare-earth && cd rare-earth
pip install -r requirements.txt
python src/main.py
```

Then: title screen -> set **Server address** to `IP:5555` -> **Multiplayer**.
(Or skip the menu: `python src/client.py SERVER_IP 5555`.)

---

## C. Players connect

1. `python src/main.py`
2. On the title screen, set **Server address** to your server's `IP:5555`.
3. Click **Multiplayer**.

---

## D. Securing the server

A fresh droplet has no firewall, so `5555` is already reachable — the job is to
**close everything else** and gate the game.

**1. Firewall — allow only SSH + the game port:**

```bash
ufw allow OpenSSH          # SSH (22) — don't lock yourself out
ufw allow 5555/tcp         # the game port
ufw --force enable
ufw status
```

(If you attached a **DO Cloud Firewall** in the control panel, add an inbound
TCP 5555 rule there too.)

**2. Lock down SSH** (the real attack surface — not the game port):

```bash
apt install -y fail2ban                 # throttles SSH brute-force
# use an SSH key, then in /etc/ssh/sshd_config set:  PasswordAuthentication no
# then: systemctl restart ssh           (confirm your key works first!)
```

**3. Run the game as a non-root user** so a server bug can't own the box:

```bash
adduser --system --group rare
# place the repo where 'rare' can read it, and set User=rare in the systemd unit.
```

**4. Player cap** (optional env var on the server, e.g. in the systemd unit's
`[Service]` section):

```ini
Environment=MAX_PLAYERS=16    # cap concurrent connections (default 16)
```

The server validates every packet and drops slow/over-cap clients, so a stranger
who finds the IP **can't crash or flood it**. There's no join password, though —
anyone who knows `IP:5555` can connect as a player, so keep the address to your
friends.

---

## Notes

- **Keep client and server in sync.** The wire protocol must match — have
  players `git pull` to the same commit whenever you change the netcode, or
  clients will fail to connect / desync.
- **Versions:** both server and client need Python 3.11+.
- **Region:** put the server near you and your friends to keep latency low
  (there's interpolation + local prediction, but lower ping always feels better).
- **fly.io auto-stop:** `fly.toml` sets `min_machines_running = 1` so the world
  stays live; otherwise an idle machine would stop and reset session state.
