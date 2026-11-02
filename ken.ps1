# ken - manage your Ken installation on Windows. https://kencomputer.dev
# The Windows twin of the `ken` script; install.ps1 puts a ken.cmd on your PATH that runs this.
param([string]$Command = "help", [string]$Arg = "")

$KenHome = if ($env:KEN_HOME) { $env:KEN_HOME } else { Join-Path $HOME ".ken" }
$App = Join-Path $KenHome "app"
$Pythonw = Join-Path $KenHome "venv\Scripts\pythonw.exe"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Ken.lnk"

function Get-KenProcesses {
  Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($App) }
}
function Start-Ken {
  Remove-Item (Join-Path $KenHome "off") -ErrorAction SilentlyContinue  # the note /kill leaves
  if (Get-KenProcesses) { return }
  Start-Process -FilePath $Pythonw -ArgumentList "`"$App\winrun.py`"" -WorkingDirectory $KenHome
}
function Stop-Ken {
  # winrun.py first, so it doesn't start Ken again.
  Get-KenProcesses | Sort-Object { -not $_.CommandLine.Contains("winrun.py") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

switch ($Command) {
  "start" { Start-Ken; "done." }
  "stop" { Stop-Ken; "done." }
  "restart" { Stop-Ken; Start-Sleep 2; Start-Ken; "done." }
  "status" { if (Get-KenProcesses) { "* ken is running" } else { "o ken is stopped" } }
  "logs" {
    $n = if ($Arg) { [int]$Arg } else { 50 }
    Get-Content (Join-Path $KenHome "logs\ken.log") -Tail $n -Encoding UTF8
  }
  "update" {
    "-> updating ken..."
    git -C $App pull
    Stop-Ken; Start-Sleep 2; Start-Ken  # winrun.py installs new requirements before starting
    "updated and restarted."
  }
  { $_ -in "open", "app" } {
    $tokenFile = Join-Path $KenHome "web-token"
    if (-not (Test-Path $tokenFile)) { "The app hasn't started yet. Is ken running? (ken status)"; break }
    $token = (Get-Content $tokenFile -Raw).Trim()
    $port = (Select-String -Path (Join-Path $KenHome ".env") -Pattern "^KEN_WEB_PORT=(.+)$" -ErrorAction SilentlyContinue).Matches.Groups[1].Value
    if (-not $port) { $port = "7777" }
    Start-Process "http://127.0.0.1:$port/login?token=$token"
    "opened Ken in your browser."
  }
  "setup" {
    # Telegram and Claude again, from the installer that's already here; it restarts Ken after.
    Stop-Ken
    $env:KEN_SETUP = "1"
    & (Join-Path $App "install.ps1")
  }
  "config" { notepad (Join-Path $KenHome ".env") }
  "memory" { notepad (Join-Path $KenHome "work\SOUL.md") }
  "uninstall" {
    $a = Read-Host "Remove Ken from this computer? Your memory & settings in $KenHome stay. [y/N]"
    if ($a -ne "y") { break }
    Stop-Ken
    Remove-Item $Shortcut -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $KenHome "venv") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $KenHome "bin") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $KenHome "app") -Recurse -Force -ErrorAction SilentlyContinue
    "Ken removed. Your memory & settings stay in $KenHome (delete that folder to remove those too)."
    "To put it back, run the installer again."
  }
  default {
@"
ken - your AI's computer

  ken open       open the app in your browser
  ken status     is it running?
  ken logs [n]   recent activity
  ken update     pull the latest version and restart
  ken restart    restart ken
  ken start/stop
  ken setup      connect Telegram and Claude again
  ken config     edit settings (.env)
  ken memory     edit your assistant's soul file (identity + memory)
  ken uninstall  remove Ken (keeps your memory & settings)
"@
  }
}
