# 📺 TV Grabber

Paste a magnet link, and your show lands in Plex — correctly named and filed, no manual downloading, renaming, or moving.

TV Grabber is a small self-hosted Flask app that automates fetching TV downloads via qBittorrent, watches them until they finish, and drops the finished episodes straight into your Plex library with proper `S01E02` naming.

## Features

- Simple one-page web UI — paste a magnet link (or bare info hash), show name, and season
- Sends the torrent to qBittorrent and polls its progress
- Auto-detects episode numbers from filenames (`S01E02`, `1x02`, `E02`, `Episode 02`)
- Renames and moves finished video files into `TVShows/<Show Name>/Season NN/`
- Cleans up the completed torrent automatically once files are filed
- **Remove** button to cancel a stuck/stalled download and clear its partial files
- Runs as a systemd service; persists its job queue across restarts

## How it works

1. You paste a magnet link, show name, and season into the UI
2. The app sends the magnet to qBittorrent's Web API and starts polling it
3. Once qBittorrent reports the torrent finished (or effectively finished — seeding/paused-up), TV Grabber scans the download folder for `.mkv` / `.mp4` files
4. It renames them to `Show.Name.S01E02.ext` (falling back to the original filename if no episode number is detected) and moves them into your Plex TV folder
5. The torrent is removed from qBittorrent once the files are safely moved

## Requirements

- Python 3
- Flask, requests (`pip install flask requests`)
- qBittorrent with the Web UI enabled
- A media server (Plex, Jellyfin, etc.) watching the destination TV folder

## Setup

1. Clone the repo and set the following environment variables (or edit the fallback defaults at the top of `app.py` directly for local testing):

   | Variable | Description | Default |
   |---|---|---|
   | `QB_URL` | qBittorrent Web UI URL | `http://localhost:8080` |
   | `QB_USER` | qBittorrent Web UI username | — |
   | `QB_PASS` | qBittorrent Web UI password | — |
   | `DOWNLOADS_DIR` | Where qBittorrent saves torrents | — |
   | `TV_DIR` | Your Plex/media server TV library folder | — |
   | `POLL_INTERVAL` | Seconds between progress checks | `20` |

2. Run it directly:

   ```bash
   export QB_USER=your-username
   export QB_PASS=your-password
   export DOWNLOADS_DIR=/path/to/Torrents
   export TV_DIR=/path/to/TVShows
   python3 app.py
   ```

   Or install it as a systemd service using the included `tvgrabber.service`, then layer your real values on top as an override so they never touch the repo:

   ```bash
   sudo cp tvgrabber.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now tvgrabber
   sudo systemctl edit tvgrabber
   ```

   The last command opens an override file — paste in:

   ```ini
   [Service]
   Environment=QB_USER=your-username
   Environment=QB_PASS=your-password
   Environment=DOWNLOADS_DIR=/path/to/Torrents
   Environment=TV_DIR=/path/to/TVShows
   ```

   Save it, then apply with:

   ```bash
   sudo systemctl restart tvgrabber
   ```

3. Open `http://<host>:5555` in a browser.

## Removing a stuck download

If a torrent stalls (no peers, stuck progress), hit **Remove** on its card. This deletes the torrent from qBittorrent along with any partial files and clears the job from the queue.

## Security note

`app.py` reads its config from environment variables, so no real credentials or paths are stored in the repo. When deploying, set the variables above either in your shell, a systemd `Environment=` line, or an `.env`-style file that's excluded via `.gitignore` — never commit real values.

## License

MIT — see [LICENSE](LICENSE) for details.
