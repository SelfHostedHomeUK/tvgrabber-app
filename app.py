#!/usr/bin/env python3
"""
TV Grabber - Paste a magnet link, files land in Plex automatically.
Sends to qBittorrent, monitors completion, renames and moves to TVShows.
"""

from flask import Flask, render_template_string, request, jsonify
import requests
import os
import re
import shutil
import threading
import time
import json

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
QB_URL        = os.environ.get("QB_URL", "http://localhost:8080")
QB_USER       = os.environ.get("QB_USER", "your-qbittorrent-username")
QB_PASS       = os.environ.get("QB_PASS", "your-qbittorrent-password")
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "/path/to/Torrents")
TV_DIR        = os.environ.get("TV_DIR", "/path/to/TVShows")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "20"))   # seconds between progress checks
# ─────────────────────────────────────────────────────────────────────────────

JOBS_FILE = "/home/ubuntu/tvgrabber/active_jobs.json"
jobs = {}   # in-memory job store  { job_id: {...} }


def load_jobs():
    if os.path.exists(JOBS_FILE):
        try:
            with open(JOBS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_jobs():
    try:
        with open(JOBS_FILE, 'w') as f:
            json.dump(jobs, f)
    except Exception:
        pass


# ── qBittorrent helpers ───────────────────────────────────────────────────────

def qb_session():
    s = requests.Session()
    r = s.post(f"{QB_URL}/api/v2/auth/login",
               data={"username": QB_USER, "password": QB_PASS}, timeout=10)
    return s if r.text.strip() == "Ok." else None


def add_magnet(magnet):
    s = qb_session()
    if not s:
        return None, "Could not log in to qBittorrent"
    s.post(f"{QB_URL}/api/v2/torrents/add",
           data={"urls": magnet, "savepath": DOWNLOADS_DIR}, timeout=10)
    time.sleep(2)
    torrents = s.get(f"{QB_URL}/api/v2/torrents/info", timeout=10).json()
    if not torrents:
        return None, "Torrent not found after adding"
    torrents.sort(key=lambda x: x.get("added_on", 0), reverse=True)
    return torrents[0]["hash"], None


def get_torrent(hash_str):
    s = qb_session()
    if not s:
        return None
    data = s.get(f"{QB_URL}/api/v2/torrents/info",
                 params={"hashes": hash_str}, timeout=10).json()
    return data[0] if data else None


# ── File renaming ─────────────────────────────────────────────────────────────

def extract_episode(filename):
    """Return episode number (int) from a filename, or None."""
    # S01E02 / s01e02
    m = re.search(r'[Ss]\d{1,2}[Ee](\d{2,3})', filename)
    if m:
        return int(m.group(1))
    # 1x02
    m = re.search(r'\d{1,2}[xX](\d{2,3})', filename)
    if m:
        return int(m.group(1))
    # E02 or Episode 02
    m = re.search(r'[Ee]pisode[\s._-]*(\d{1,3})|(?<!\d)[Ee](\d{2,3})(?!\d)', filename)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def process_download(hash_str, show_name, season):
    torrent = get_torrent(hash_str)
    if not torrent:
        return False, "Torrent not found"

    torrent_name = torrent.get("name", "")
    torrent_path = os.path.join(DOWNLOADS_DIR, torrent_name)

    # Collect video files
    video_files = []
    if os.path.isdir(torrent_path):
        for f in os.listdir(torrent_path):
            if f.lower().endswith(('.mkv', '.mp4')):
                video_files.append((torrent_path, f))
    elif os.path.isfile(torrent_path) and torrent_path.lower().endswith(('.mkv', '.mp4')):
        video_files.append((DOWNLOADS_DIR, torrent_name))

    if not video_files:
        return False, "No .mkv or .mp4 files found in completed download"

    video_files.sort(key=lambda x: x[1])

    # Build destination
    season_folder = os.path.join(TV_DIR, show_name, f"Season {season:02d}")
    os.makedirs(season_folder, exist_ok=True)

    show_dots = show_name.replace(' ', '.')
    season_code = f"S{season:02d}"
    moved = []

    for folder, fname in video_files:
        ep = extract_episode(fname)
        ext = os.path.splitext(fname)[1].lower()
        if ep is not None:
            new_name = f"{show_dots}.{season_code}E{ep:02d}{ext}"
        else:
            # No episode number detected — keep original name
            new_name = fname
        src = os.path.join(folder, fname)
        dst = os.path.join(season_folder, new_name)
        shutil.move(src, dst)
        moved.append(new_name)

    return True, moved


def remove_torrent(hash_str, delete_files=False):
    s = qb_session()
    if s:
        s.post(f"{QB_URL}/api/v2/torrents/delete",
               data={"hashes": hash_str, "deleteFiles": "true" if delete_files else "false"}, timeout=10)




def monitor(hash_str, show_name, season, job_id):
    jobs[job_id]['status'] = 'downloading'
    save_jobs()
    while True:
        try:
            t = get_torrent(hash_str)
            if not t:
                jobs[job_id].update({'status': 'error', 'message': 'Torrent disappeared'})
                time.sleep(10)
                jobs.pop(job_id, None)
                save_jobs()
                return
            progress = t.get('progress', 0)
            state    = t.get('state', '')
            jobs[job_id]['progress']     = round(progress * 100)
            jobs[job_id]['torrent_name'] = t.get('name', '')
            save_jobs()

            done_states = ('seeding', 'pausedUP', 'stalledUP', 'uploading', 'queuedUP')
            if progress >= 1.0 or state in done_states:
                jobs[job_id]['status'] = 'processing'
                save_jobs()
                ok, result = process_download(hash_str, show_name, season)
                if ok:
                    remove_torrent(hash_str)
                    jobs[job_id].update({'status': 'complete', 'files': result})
                    save_jobs()
                    time.sleep(10)
                    jobs.pop(job_id, None)
                    save_jobs()
                else:
                    jobs[job_id].update({'status': 'error', 'message': result})
                    save_jobs()
                return
        except Exception as e:
            jobs[job_id].update({'status': 'error', 'message': str(e)})
            save_jobs()
            return
        time.sleep(POLL_INTERVAL)


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TV Grabber</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#0d0d12;color:#e0e0e0;min-height:100vh;padding:20px 16px}
  h1{color:#f4623a;font-size:1.5rem;margin-bottom:4px}
  .sub{color:#666;font-size:.8rem;margin-bottom:22px}
  .card{background:#16161f;border:1px solid #252535;border-radius:12px;padding:18px;margin-bottom:18px}
  label{display:block;font-size:.72rem;color:#777;text-transform:uppercase;
        letter-spacing:.06em;margin-bottom:5px}
  input,textarea{width:100%;background:#0d0d12;border:1px solid #252535;border-radius:8px;
                 color:#e0e0e0;padding:11px 12px;font-size:.9rem;margin-bottom:14px}
  textarea{height:76px;resize:vertical;font-family:monospace;font-size:.78rem}
  input:focus,textarea:focus{outline:none;border-color:#f4623a}
  .row{display:flex;gap:10px}
  .row>div{flex:1}
  .row input{margin-bottom:0}
  button{width:100%;background:#f4623a;color:#fff;border:none;border-radius:8px;
         padding:13px;font-size:.95rem;font-weight:600;cursor:pointer;margin-top:14px}
  button:hover{background:#ff7a52}
  button:disabled{background:#333;color:#666;cursor:not-allowed}
  .sec{font-size:.72rem;font-weight:600;color:#555;text-transform:uppercase;
       letter-spacing:.08em;margin-bottom:10px}
  .job{background:#16161f;border:1px solid #252535;border-radius:10px;
       padding:14px;margin-bottom:10px}
  .jhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
  .jname{font-weight:600;font-size:.9rem;white-space:nowrap;overflow:hidden;
         text-overflow:ellipsis;max-width:70%}
  .badge{font-size:.7rem;padding:3px 9px;border-radius:20px;font-weight:700;flex-shrink:0}
  .pending   {background:#222;color:#888}
  .downloading{background:#0e2a45;color:#4db8ff}
  .processing{background:#2a1e0a;color:#ffaa4d}
  .complete  {background:#0a2a12;color:#3ddc68}
  .error     {background:#2a0a0a;color:#ff5555}
  .bar{background:#0d0d12;border-radius:4px;height:5px;overflow:hidden;margin-bottom:7px}
  .fill{height:100%;background:#f4623a;border-radius:4px;transition:width .4s}
  .detail{font-size:.75rem;color:#666}
  .files{margin-top:7px}
  .file{font-size:.73rem;color:#3ddc68;font-family:monospace;padding:1px 0}
  .empty{color:#444;font-size:.82rem;text-align:center;padding:24px}
  .err-msg{color:#ff5555;font-size:.75rem;margin-top:4px}
  .rm-btn{width:auto;background:transparent;border:1px solid #3a2020;color:#ff5555;
          font-size:.72rem;font-weight:600;padding:5px 12px;border-radius:6px;
          margin-top:10px;cursor:pointer}
  .rm-btn:hover{background:#2a0a0a}
</style>
</head>
<body>
<h1>📺 TV Grabber</h1>
<p class="sub">Paste a magnet link — files land in Plex automatically</p>

<div class="card">
  <label>Magnet Link</label>
  <textarea id="magnet" placeholder="magnet:?xt=urn:btih:…"></textarea>
  <div class="row">
    <div>
      <label>Show Name</label>
      <input id="show" type="text" placeholder="Breaking Bad">
    </div>
    <div style="max-width:90px">
      <label>Season</label>
      <input id="season" type="number" value="1" min="1" max="99">
    </div>
  </div>
  <button id="btn" onclick="submit()">Add Download</button>
  <div id="err" class="err-msg"></div>
</div>

<div class="sec">Downloads</div>
<div id="jobs"><div class="empty">No downloads yet</div></div>

<script>
const jobIds = [];

async function submit() {
  let magnet = document.getElementById('magnet').value.trim();
  const show   = document.getElementById('show').value.trim();
  const season = parseInt(document.getElementById('season').value);
  const err    = document.getElementById('err');
  err.textContent = '';

  // Accept bare hash or full magnet link
  if (!magnet.startsWith('magnet:') && /^[0-9a-fA-F]{40}$/.test(magnet)) {
    magnet = 'magnet:?xt=urn:btih:' + magnet;
    document.getElementById('magnet').value = magnet;
  }
  if (!magnet.startsWith('magnet:')) { err.textContent = 'Paste a magnet link or info hash'; return; }
  if (!show) { err.textContent = 'Enter a show name'; return; }

  const btn = document.getElementById('btn');
  btn.disabled = true; btn.textContent = 'Adding…';

  try {
    const r    = await fetch('/add', { method:'POST',
                  headers:{'Content-Type':'application/json'},
                  body: JSON.stringify({magnet, show_name: show, season}) });
    const data = await r.json();
    if (data.success) {
      document.getElementById('magnet').value = '';
      jobIds.unshift(data.job_id);
      renderJob(data.job_id, {show_name: show, season, status:'pending', progress:0});
    } else {
      err.textContent = data.error || 'Unknown error';
    }
  } catch(e) { err.textContent = 'Could not reach server'; }

  btn.disabled = false; btn.textContent = 'Add Download';
}

function renderJob(id, d) {
  const container = document.getElementById('jobs');
  const empty = container.querySelector('.empty');
  if (empty) empty.remove();

  let el = document.getElementById('job-' + id);
  if (!el) {
    el = document.createElement('div');
    el.className = 'job'; el.id = 'job-' + id;
    el.innerHTML = `<div class="jhead">
      <div class="jname"></div><span class="badge pending">Pending</span>
    </div>
    <div class="bar"><div class="fill" style="width:0%"></div></div>
    <div class="detail"></div><div class="files"></div>
    <button class="rm-btn" onclick="removeJob('${id}')">Remove</button>`;
    container.insertBefore(el, container.firstChild);
  }

  el.querySelector('.jname').textContent = d.torrent_name || d.show_name || id;
  const badge = el.querySelector('.badge');
  badge.className = 'badge ' + d.status;
  badge.textContent = d.status.charAt(0).toUpperCase() + d.status.slice(1);
  el.querySelector('.fill').style.width = (d.progress||0) + '%';

  const det = el.querySelector('.detail');
  if (d.status === 'downloading')
    det.textContent = `${d.progress||0}% · ${d.show_name} Season ${String(d.season).padStart(2,'0')}`;
  else if (d.status === 'processing')
    det.textContent = 'Renaming and moving files…';
  else if (d.status === 'complete') {
    det.textContent = `✓ TVShows/${d.show_name}/Season ${String(d.season).padStart(2,'0')}/`;
    if (d.files) el.querySelector('.files').innerHTML =
      d.files.map(f=>`<div class="file">↳ ${f}</div>`).join('');
  } else if (d.status === 'error')
    det.textContent = '✗ ' + (d.message || 'Error');

  el.querySelector('.rm-btn').style.display =
    (d.status === 'processing' || d.status === 'complete') ? 'none' : 'block';
}

async function removeJob(id) {
  if (!confirm('Remove this download? The torrent and any partial files will be deleted.')) return;
  try {
    const r = await fetch('/remove/' + id, { method: 'POST' });
    const data = await r.json();
    if (data.success) {
      const idx = jobIds.indexOf(id);
      if (idx > -1) jobIds.splice(idx, 1);
      const el = document.getElementById('job-' + id);
      if (el) el.remove();
      if (jobIds.length === 0)
        document.getElementById('jobs').innerHTML = '<div class="empty">No downloads yet</div>';
    }
  } catch(e) {}
}

async function poll() {
  for (const id of jobIds) {
    try {
      const r = await fetch('/status/' + id);
      const d = await r.json();
      renderJob(id, d);
    } catch(e) {}
  }
}

// Load active jobs on page load
(async () => {
  try {
    const r = await fetch('/jobs');
    const ids = await r.json();
    for (const id of ids) {
      jobIds.push(id);
      const r2 = await fetch('/status/' + id);
      const d = await r2.json();
      renderJob(id, d);
    }
  } catch(e) {}
})();

setInterval(poll, 20000);
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/jobs')
def list_jobs():
    return jsonify(list(jobs.keys()))


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/add', methods=['POST'])
def add():
    data      = request.json
    magnet    = data.get('magnet', '').strip()
    show_name = data.get('show_name', '').strip()
    season    = int(data.get('season', 1))

    if not magnet or not show_name:
        return jsonify({'success': False, 'error': 'Missing magnet or show name'})

    hash_str, error = add_magnet(magnet)
    if error:
        return jsonify({'success': False, 'error': error})

    job_id = hash_str[:10]
    jobs[job_id] = {
        'hash': hash_str, 'show_name': show_name, 'season': season,
        'status': 'pending', 'progress': 0, 'torrent_name': show_name
    }
    threading.Thread(target=monitor, args=(hash_str, show_name, season, job_id),
                     daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/status/<job_id>')
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(job)


@app.route('/remove/<job_id>', methods=['POST'])
def remove(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    remove_torrent(job['hash'], delete_files=True)
    jobs.pop(job_id, None)
    save_jobs()
    return jsonify({'success': True})


if __name__ == '__main__':
    jobs.update(load_jobs())
    print("TV Grabber running at http://0.0.0.0:5555")
    app.run(host='0.0.0.0', port=5555, debug=False)
