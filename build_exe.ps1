# Builds release\ForeverAI\ForeverAI.exe (Windows, PowerShell 5.1+).
# Requirements: Python 3.12 with the packages from companion\requirements.txt and PyInstaller
#   python -m venv companion\.venv
#   companion\.venv\Scripts\python.exe -m pip install -r companion\requirements.txt pyinstaller -c companion\constraints.txt
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $Root "companion\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing companion\.venv - see the build section in README.md" }

$data = @(
  "--add-data", "$Root\addon\ForeverAI;addon\ForeverAI",
  "--add-data", "$Root\assets;assets"
)
$kb = Join-Path $Root "companion\data\kb.sqlite"   # optional starter knowledge base (public wiki / Blizzard data)
if (Test-Path $kb) { $data += @("--add-data", "$kb;seed") }

Push-Location (Join-Path $Root "companion")
& $py -m PyInstaller --noconfirm --clean --windowed --name ForeverAI `
  --icon "$Root\assets\foreverai.ico" `
  --distpath "$Root\release" --workpath "$Root\build" --specpath "$Root\build" `
  --collect-all curl_cffi `
  @data foreverai_app.py
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw "PyInstaller failed ($code)" }
Write-Host "Built: $Root\release\ForeverAI\ForeverAI.exe"
exit 0
