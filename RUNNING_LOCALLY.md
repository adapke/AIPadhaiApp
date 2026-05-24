# Running AI Pathshala locally (Windows + IntelliJ / PyCharm)

This guide gets you from a fresh clone to **localhost:8000** (student app)
and **localhost:8000/admin** (admin console) in one IntelliJ click.

## TL;DR — there is only one process to run

`v0.8.1` mounts the admin app inside the main app at `/admin/*`. So in
local dev:

| URL | What you get |
|---|---|
| `http://localhost:8000/`      | Student app (Studio + 16 modules) |
| `http://localhost:8000/admin` | Admin console (login → dashboard) |

A single `uvicorn padhai.web:app` command serves both. The repo ships an
IntelliJ run config named **AI Pathshala (student + admin)** that does
exactly this — pick it from the dropdown next to the green ▶ button.

---

## One-time setup

### 1. Install Python 3.11 or 3.12
Download from <https://www.python.org/downloads/windows/>. During install
**check "Add Python to PATH"**. Verify in PowerShell:

```powershell
python --version
# Python 3.11.x  (or 3.12.x)
```

### 2. Open the project in IntelliJ / PyCharm
- IntelliJ IDEA Community + the Python plugin works.
- PyCharm Community works.
- PyCharm Professional works (and gives you the FastAPI run config type,
  but the universal Python run configs we ship work in any edition).

Open the folder containing this repo. IntelliJ will auto-detect the
`.idea/runConfigurations/*.xml` files and show them in the run dropdown.

### 3. Create the project venv inside IntelliJ
**File → Settings → Project: Paymentoneclick → Python Interpreter →
⚙ → Add → Virtualenv Environment → New environment.**

- Location: `.venv` in the project root (already gitignored)
- Base interpreter: your Python 3.11 / 3.12 install
- Click OK; let IntelliJ create the venv (~30 seconds).

### 4. Install dependencies
Open IntelliJ's terminal (View → Tool Windows → Terminal — defaults to
PowerShell on Windows). The venv is already activated:

```powershell
pip install -r requirements.txt
```

This pulls FastAPI, uvicorn, anthropic, pillow, etc. ~2 min on a decent
network.

### 5. Add your Anthropic API key
Two options:

**Option A: edit the run config (one-off, easiest):**
Run → Edit Configurations → AI Pathshala (student + admin) →
Environment variables → add:

```
ANTHROPIC_API_KEY=sk-ant-...
```

**Option B: use a `.env` file (recommended if you have many secrets):**

```powershell
copy .env.example .env
notepad .env
```

Fill in `ANTHROPIC_API_KEY`. For `.env` to be picked up by IntelliJ run
configs, install the free
[EnvFile plugin](https://plugins.jetbrains.com/plugin/7861-envfile),
then in the run config check **Enable EnvFile** and point at `.env`.

### 6. Optional Windows system tools

The student app **runs without these** in dev (the fallback paths kick
in), but you'll hit them eventually:

| Tool | Why | Install (PowerShell as Admin) |
|---|---|---|
| **ffmpeg** | Video rendering | `winget install Gyan.FFmpeg` or `choco install ffmpeg` |
| **espeak-ng** | TTS fallback for Indic languages | `choco install espeak-ng` |
| **LibreOffice** | PPTX / DOCX ingest | `winget install TheDocumentFoundation.LibreOffice` |

After installing, restart IntelliJ so it picks up the updated PATH.

---

## Running

### The common case: one click

In the IntelliJ run dropdown (top-right), select **AI Pathshala (student + admin)**
and press the ▶ button. You'll see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Application startup complete.
```

Open both:
- **Student app**: <http://localhost:8000>
- **Admin console**: <http://localhost:8000/admin>

The first time you open `/admin`, you'll see a "First-time setup" form
to create the founding admin. After that, signups are closed.

### Hot reload

The `--reload` flag is on by default. Editing any `.py` file under
`padhai/` or `admin/` restarts the server in under a second. Browser
refresh picks up the new code.

### Split-out preview: running admin as its own process

If you want to validate that the admin app still runs **without** the
student app process — useful before doing the eventual repo split — use
the **Admin (standalone — split-out preview)** run config. It serves
admin on `http://localhost:8001` independently. Or run **Both
(student + standalone admin)** to start both processes at once.

---

## Project layout

```
Paymentoneclick/
├── padhai/                ← student app
│   ├── web.py             ← FastAPI; mounts admin/ at /admin/*
│   ├── render.py          ← video generation pipeline
│   ├── pedagogy.py        ← Claude-driven lesson script generation
│   ├── personalization.py ← PersonalizationProfile (the brain)
│   └── …
├── admin/                 ← admin console (zero `from padhai import …`)
│   ├── app.py             ← FastAPI; mounted at /admin/* in dev
│   ├── auth.py            ← own JWT, own admin_users table
│   ├── data.py            ← direct SQLite reads of jobs DB
│   ├── templates.py       ← server-rendered HTML
│   └── Dockerfile         ← kept for future split-out to its own repo
├── .idea/runConfigurations/  ← IntelliJ run configs (this guide)
├── .env.example           ← copy to .env, fill in secrets
├── Dockerfile             ← production image (FastAPI + ffmpeg + Piper)
└── render.yaml            ← Render.com deploy blueprint
```

---

## Common Windows gotchas

**`uvicorn: command not found`** — venv not activated. IntelliJ's
terminal should show `(.venv)` in the prompt. If not: Settings → Project
→ Python Interpreter → make sure the venv is selected, then close and
reopen the Terminal tool window.

**`ModuleNotFoundError: No module named 'padhai'`** — `PYTHONPATH` not
set. The shipped run configs set `PYTHONPATH=$PROJECT_DIR$` already; if
running from a plain `cmd.exe` outside IntelliJ:

```powershell
$env:PYTHONPATH = "$pwd"
python -m uvicorn padhai.web:app --reload
```

**`ANTHROPIC_API_KEY not set`** — you skipped step 5 above. Edit the
run config's Environment variables or set up `.env` + EnvFile.

**Port 8000 already in use** — change `--port 8000` in the run config
to a free one (e.g. 8080). Same for 8001 on the standalone admin run
config.

**Piper voice models missing** — that's fine in dev. Without
`PIPER_MODEL_HI=` etc. the server falls back to espeak (robotic but
works). For the natural-sounding voices, download from
<https://huggingface.co/rhasspy/piper-voices> and set the env vars.

**Admin DB is per-user** — admin signups live at
`%USERPROFILE%\.padhai\admin.db`. Delete that file to reset the
bootstrap state (useful when demoing the "Create first admin" screen
repeatedly).

---

## What to try first

1. ▶ Run **AI Pathshala (student + admin)**
2. Open <http://localhost:8000>
3. Sidebar → **✨ Video Studio**
4. Type any topic, e.g. *"Photosynthesis"*
5. Customize: language Hindi, video mode Explainer, 16:9
6. Click ⚡ Generate video
7. Watch the 8-step progress; the cartoon video drops in ~60-90s
8. Open another tab: <http://localhost:8000/admin> → create the first
   admin → see the job you just generated in the queue

If anything breaks, paste the IntelliJ run console output. The reload
loop keeps you moving fast.
