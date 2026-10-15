# Ken installer for Windows - https://kencomputer.dev
#   irm https://kencomputer.dev/install.ps1 | iex
# The Windows twin of install.sh: Claude Code + Telegram + memory, running in the background.
# Plain ASCII on purpose: Windows PowerShell can misread other characters in a downloaded script.

# Everything lives in a function: under `irm | iex`, `exit` would close the user's window.
function Install-Ken {
$Repo = if ($env:KEN_REPO) { $env:KEN_REPO } else { "https://github.com/prasann16/kencomputer.git" }
$KenHome = if ($env:KEN_HOME) { $env:KEN_HOME } else { Join-Path $HOME ".ken" }
$App = Join-Path $KenHome "app"
$BinDir = Join-Path $KenHome "bin"
$Python = Join-Path $KenHome "venv\Scripts\python.exe"
$KenPython = "3.13"
$env:PYTHONUTF8 = "1"

function Say($t) { Write-Host $t -ForegroundColor White }
function Dim($t) { Write-Host $t -ForegroundColor DarkGray }
function Ok($t) { Write-Host "  + $t" -ForegroundColor Green }
function Fail($t) { throw $t }
function Ask-Secret($prompt) {
  $s = Read-Host -Prompt $prompt -AsSecureString
  [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}
function Write-Utf8($path, $text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false)) }
function Refresh-Path {
  $env:PATH = [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH", "User") + ";$env:PATH"
}
function Tg($method) { Invoke-RestMethod -Uri "https://api.telegram.org/bot$script:BotToken/$method" -TimeoutSec 20 -ErrorAction Stop }
function Env-Value($name) {
  $f = Join-Path $KenHome ".env"
  if (-not (Test-Path $f)) { return "" }
  $m = Select-String -Path $f -Pattern "^$name=(.*)$" | Select-Object -First 1
  if ($m) { return $m.Matches[0].Groups[1].Value } else { return "" }
}

Say ""
Say "  ken *  - give your AI its own computer"
Dim "  You'll need Telegram and a Claude subscription."
Say ""

# ---------- 0. prerequisites ----------
if ($env:OS -ne "Windows_NT") { Fail "This installer is for Windows. On Mac or Linux: curl -fsSL kencomputer.dev/install | bash" }
Ok "OS: Windows"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail "Ken needs Git. Install it from https://git-scm.com/download/win and run this again."
  }
  Say "-> Installing Git (Ken and Claude Code use it)..."
  winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements | Out-Null
  Refresh-Path
  $env:PATH = "$env:ProgramFiles\Git\cmd;$env:PATH"
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Couldn't install Git. Install it from https://git-scm.com/download/win and run this again." }
}
Ok "Git ready"

# Every install runs the same Python, managed by uv (no admin rights; it downloads Python itself).
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say "-> Installing uv (manages Ken's Python)..."
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
  Refresh-Path
  $env:PATH = "$HOME\.local\bin;$env:PATH"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "Couldn't install uv - see https://docs.astral.sh/uv/ and run this again." }
}
Ok "uv ready ($(uv --version))"

# ---------- 2. fetch ken ----------
New-Item -ItemType Directory -Force -Path $KenHome, $BinDir | Out-Null
if (Test-Path (Join-Path $App ".git")) {
  git -C $App pull -q
  Ok "Ken updated"
} else {
  git clone -q $Repo $App
  if ($LASTEXITCODE -ne 0) { Fail "Couldn't download Ken. Check your internet connection and run this again." }
  Ok "Ken downloaded"
}

# ---------- 3. python env ----------
if (Test-Path $Python) {
  & $Python -c "import sys; raise SystemExit(0 if sys.version.startswith('$KenPython.') else 1)" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Dim "  (rebuilding environment on Python $KenPython)"
    Remove-Item -Recurse -Force (Join-Path $KenHome "venv")
  }
}
if (-not (Test-Path $Python)) { uv venv -q --seed --managed-python --python $KenPython (Join-Path $KenHome "venv") }
& $Python -m pip install -q --upgrade pip
$Requirements = Join-Path $App "requirements.txt"
& $Python -m pip install -q -U -r $Requirements
if ($LASTEXITCODE -ne 0) { Fail "Couldn't install Ken's Python packages. Run this again; if it keeps failing, open an issue on GitHub." }
Copy-Item $Requirements (Join-Path $KenHome "requirements.installed")  # winrun.py reinstalls when this differs
Ok "Python environment ready"

if (-not (Test-Path (Join-Path $HOME ".cache\huggingface\hub\models--Systran--faster-whisper-small"))) {
  Dim "  Fetching the voice-transcription model (one-time, ~460MB - hang tight)..."
  & $Python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')" 2>$null | Out-Null
}
Ok "Voice transcription ready"

# Claude Code ships inside the Agent SDK Ken just installed.
$Claude = & $Python -c "import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude.exe')"
if (-not (Test-Path $Claude)) {
  $c = Get-Command claude -ErrorAction SilentlyContinue
  if ($c) { $Claude = $c.Source } else { Fail "Couldn't find Claude Code. Run this again." }
}

# ---------- 4. telegram bot ----------
$script:BotToken = Env-Value "TELEGRAM_BOT_TOKEN"
if ($script:BotToken) {
  Ok "Telegram already configured (delete $KenHome\.env to redo)"
  $UserId = Env-Value "ALLOWED_USER_ID"
  $FirstName = "there"
} else {
  Say ""
  Say "-- Step 1 of 2: create your bot (1 minute) --"
  Dim "  1. Open Telegram (phone is fine) and message @BotFather"
  Dim "  2. Send:  /newbot   - pick any name (e.g. Ken), any username"
  Dim "  3. BotFather replies with a token like 123456:ABC-xyz..."
  Say ""
  while ($true) {
    $script:BotToken = (Ask-Secret "Paste your bot token").Trim()
    try { $BotUser = (Tg "getMe").result.username } catch { $BotUser = "" }
    if ($BotUser) { Ok "Connected to @$BotUser"; break }
    Write-Host "  That token didn't work - try again." -ForegroundColor Red
  }

  try { Tg "deleteWebhook" | Out-Null } catch {}
  Say ""
  Say "-- Step 2 of 2: introduce yourself --"
  $Code = "ken-" + (-join ((1..16) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) }))
  Dim "  Open @$BotUser in Telegram - https://t.me/$BotUser - and send this code:"
  Say "  $Code"
  Dim "  This links your private Telegram account. I'll wait up to 15 minutes."
  Write-Host -NoNewline "  waiting for your message to @$BotUser "
  $UserId = ""; $FirstName = ""
  for ($i = 0; $i -lt 450 -and -not $UserId; $i++) {
    try {
      $msg = (Tg "getUpdates?timeout=2").result | ForEach-Object { $_.message } |
        Where-Object { $_ -and $_.text -and $_.text.Trim() -eq $Code -and $_.chat.type -eq "private" } |
        Select-Object -Last 1
      if ($msg) { $UserId = [string]$msg.from.id; $FirstName = $msg.from.first_name }
    } catch {}
    if (-not $UserId) { Write-Host -NoNewline "."; Start-Sleep 2 }
  }
  Write-Host ""
  if (-not $UserId) {
    Fail "SETUP DID NOT FINISH - no message arrived. Run the installer again and message the bot when asked."
  }
  Ok "Hi $FirstName! Locked to your Telegram account ($UserId)"
}

# ---------- 5. claude auth ----------
Say ""
Say "-- Connecting Claude --"
$OauthToken = ""
& $Claude -p "Reply with exactly OK" --model haiku 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  $OauthToken = Env-Value "CLAUDE_CODE_OAUTH_TOKEN"
  Ok "Claude is already signed in on this computer"
} else {
  Dim "  Ken runs on your Claude subscription. We'll create a long-lived token."
  Dim "  A browser window will open - approve there. If Claude shows a code, paste it here."
  Say ""
  & $Claude setup-token 2>&1 | Tee-Object -Variable lines
  $out = ($lines | Out-String)
  $m = [regex]::Matches($out, "sk-ant-oat[A-Za-z0-9_-]+")
  if ($m.Count -gt 0) { $OauthToken = $m[$m.Count - 1].Value }
  if (-not $OauthToken) { $OauthToken = (Ask-Secret "Paste the token (starts with sk-ant-oat...)").Trim() }
  if (-not $OauthToken) { Fail "No Claude token - run the installer again." }
  Ok "Claude connected"
}

# ---------- 6. config + memory ----------
$Work = Join-Path $KenHome "work"
New-Item -ItemType Directory -Force -Path $Work, (Join-Path $KenHome "memory"), (Join-Path $KenHome "logs") | Out-Null
Write-Utf8 (Join-Path $KenHome ".env") (@(
  "TELEGRAM_BOT_TOKEN=$script:BotToken",
  "ALLOWED_USER_ID=$UserId",
  "CLAUDE_CODE_OAUTH_TOKEN=$OauthToken",
  "ANTHROPIC_API_KEY=",
  "CLAUDE_MODEL=",
  "WORKSPACE=$Work",
  "WHISPER_MODEL=small",
  "TASK_TIMEOUT_SECONDS=1800"
) -join "`n")

$Soul = Join-Path $Work "SOUL.md"
if (-not (Test-Path $Soul)) {
  $name = if ($FirstName -and $FirstName -ne "there") { $FirstName } else { "my human" }
  Write-Utf8 $Soul ([IO.File]::ReadAllText((Join-Path $App "SOUL.template.md")).Replace("{{NAME}}", $name))
}
$Jobs = Join-Path $KenHome "jobs.json"
if (-not (Test-Path $Jobs)) { Copy-Item (Join-Path $App "jobs.default.json") $Jobs }
$Hosting = Join-Path $KenHome "hosting.md"
if (-not (Test-Path $Hosting)) {
  Write-Utf8 $Hosting (@(
    "# This machine: self-hosted (Windows)",
    "",
    "- The human installed Ken themselves, on a Windows computer they control, with the open-source install script. Nobody at kencomputer.dev has any access to it.",
    "- Everything lives here: SOUL.md, memory, history, files, credentials. Voice notes are transcribed on this machine.",
    "- What leaves this machine: calls to the Claude API (Anthropic) to run you, on their own Claude subscription, and whatever a connected service is told to fetch or send.",
    "- Backups: nothing automatic. If this machine dies, ~/.ken is gone unless they back it up themselves. Say so if asked."
  ) -join "`n")
}
Ok "Config and memory written to $KenHome"

# ---------- 7. ken command ----------
Write-Utf8 (Join-Path $BinDir "ken.cmd") "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$App\ken.ps1`" %*`r`n"
$UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if (-not $UserPath) { $UserPath = "" }
if (-not ($UserPath.Split(";") -contains $BinDir)) {
  [Environment]::SetEnvironmentVariable("PATH", ($UserPath.TrimEnd(";") + ";$BinDir").TrimStart(";"), "User")
}

# ---------- 8. start at login, and now ----------
& (Join-Path $App "ken.ps1") stop | Out-Null
$Pythonw = Join-Path $KenHome "venv\Scripts\pythonw.exe"
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath("Startup")) "Ken.lnk"))
$lnk.TargetPath = $Pythonw
$lnk.Arguments = "`"$App\winrun.py`""
$lnk.WorkingDirectory = $KenHome
$lnk.WindowStyle = 7
$lnk.Save()
Start-Process -FilePath $Pythonw -ArgumentList "`"$App\winrun.py`"" -WorkingDirectory $KenHome
Ok "Running in the background (starts when you log in)"

Start-Sleep 3
Say ""
Say "  * It's alive."
Say ""
Dim "  Open Telegram - your assistant is waking up for the first time."
Dim "  Say hello. It has a question for you."
Dim ""
Dim "  Manage it (in a new window):  ken status . ken logs . ken update . ken restart"
Dim "  Its soul: $Soul  (or just tell it to remember things)"
Say ""
}

try { Install-Ken } catch { Write-Host ""; Write-Host "  x $($_.Exception.Message)" -ForegroundColor Red; Write-Host "" }
