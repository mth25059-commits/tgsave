# tgsave

A private Telegram saver. Paste a post link, get the file back.

One owner, no database, no join-gate, no ads. It runs as a systemd service on a
small VPS and forgets everything the moment a file is delivered.

```
you  →  https://t.me/c/1234567890/55
bot  →  ⬇️  Downloading  ██████████░░░░  71%   142.0 MB / 200.1 MB   6.1 MB/s
bot  →  [the file, original name and extension intact]
```

## What it does

- **Any link.** Public `t.me/name/12`, private `t.me/c/1234567890/55`, forum
  topics `t.me/c/123/45/678`, and `?single=1` query junk.
- **Any file.** Video, photo, audio, voice, sticker, animation — and documents,
  so a 2 GB `.zip` comes back as a `.zip`, not as `downloads`.
- **Albums whole.** Send one link from a media group, get every item.
- **Ranges.** `/batch <first link> <last link>` walks the ids in between.
- **Live progress.** One message, edited in place, with speed and ETA.
- **Stoppable.** `/cancel` kills the transfer mid-flight and wipes the temp dir.

## Commands

| | |
|---|---|
| *(just paste a link)* | fetch that post |
| `/batch <first> <last>` | fetch a whole range |
| `/cancel` | stop the current job |
| `/status` | account, free disk, uptime |
| `/login` | put your account online |
| `/logout` | take it offline |
| `/help` | this list |

Everyone who is not `ADMIN_ID` gets "🔒 Private bot." and nothing else.

## Install

Ubuntu 22.04 / 24.04, two minutes:

```bash
git clone https://github.com/YOUR_NAME/tgsave.git /opt/tgsave
cd /opt/tgsave
bash install.sh
```

The first run copies `config.env.example` to `config.env` and stops. Fill it in:

```ini
API_ID=1234567          # my.telegram.org -> API development tools
API_HASH=0123456789abcdef0123456789abcdef
BOT_TOKEN=123456:ABC…   # @BotFather -> /newbot
ADMIN_ID=123456789      # your own numeric id, @userinfobot knows it
```

Then run `bash install.sh` again. It builds the venv, writes the systemd unit,
and starts the service.

```bash
journalctl -u tgsave -f       # logs
sudo systemctl restart tgsave # restart
```

## First login

Reading restricted content needs a **user account**, not a bot — the Bot API
simply cannot fetch those messages. So the bot borrows yours, once:

1. Message your bot `/login`
2. Send your phone number, `+919876543210`
3. Send the code **with spaces** — `1 2 3 4 5`. Without spaces Telegram spots
   its own code in the chat and cancels it.
4. If you have two-step verification on, send the password. Both the code and
   the password message are deleted right after they are read.

That writes `data/owner.session`. It never leaves the box, and `.gitignore`
keeps it out of git. `/logout` unlinks the account for good.

## Layout

```
bot.py       handlers, /login conversation, boot
grab.py      download → re-upload engine, progress, cancellation
links.py     link parser
config.py    config.env loader
compat.py    widens Pyrogram's peer id ranges
install.sh   apt + venv + systemd, idempotent
```

Six files, three dependencies, no database. State lives in one dict in
`bot.py`; a restart is a clean slate on purpose.

## Notes worth knowing

- **Pyrogram 2.0.106 cannot see modern channels out of the box.** Its
  `MIN_CHANNEL_ID` caps raw channel ids at 2³¹, and Telegram has long since gone
  past that, so `t.me/c/3980676358/790` dies on `Peer id invalid` before a single
  request leaves the machine — nothing to do with the account or the link.
  `compat.py` widens the bounds to the current TDLib values.
- **Files keep their names.** Pyrogram runs `os.path.split()` on `file_name`, so
  passing a bare directory silently renames every download after the directory
  itself. `grab.py` passes a trailing separator, which is what preserves the
  original name and extension.
- **Private ids are cold at boot.** A `-100…` id only resolves after the dialog
  list has been walked once; `warm_peers()` does that lazily, on the first
  `PeerIdInvalid`, instead of at every start.
- **Uploads stop at 2 GB.** Above that the bot says so rather than transferring
  gigabytes and failing at the end.
- **No stdin, ever.** The service uses `connect()`, which returns an
  authorization flag, instead of `start()`, which would block on a tty prompt
  that systemd cannot answer.
- **Every job gets its own temp dir**, removed in a `finally`. `/cancel` and
  crashes both leave the disk clean, and boot sweeps whatever survived.

## Configuration

| key | default | meaning |
|---|---|---|
| `API_ID`, `API_HASH` | — | from my.telegram.org, required |
| `BOT_TOKEN` | — | from @BotFather, required |
| `ADMIN_ID` | — | the only account allowed in, required |
| `DATA_DIR` | `data` | sessions and in-flight downloads |
| `MAX_BATCH` | `200` | ceiling for one `/batch` range |

Only save content you are allowed to keep.
