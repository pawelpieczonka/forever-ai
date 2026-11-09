# Builds the Windows app: release\windows\ForeverAI-Windows\ForeverAI-Windows.exe (PowerShell 5.1+). With -Package it
# also writes release\ForeverAI-<version>-Windows-x64.zip, the file published on GitHub Releases
# (.github/workflows/release.yml).
# Requirements: Python 3.12 with the packages from companion\requirements.txt and PyInstaller
#   python -m venv companion\.venv
#   companion\.venv\Scripts\python.exe -m pip install -r companion\requirements.txt pyinstaller -c companion\constraints.txt
param([switch]$Package)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $Root "companion\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing companion\.venv - see the build section in README.md" }
$name = "ForeverAI-Windows"
$init = Get-Content (Join-Path $Root "companion\foreverai\__init__.py") -Raw
if ($init -notmatch '__version__ = "(.+?)"') { throw "No __version__ in companion\foreverai\__init__.py" }
$version = $Matches[1]

$data = @(
  "--add-data", "$Root\addon\ForeverAI;addon\ForeverAI",
  "--add-data", "$Root\assets;assets"
)
# starter knowledge base (public wiki / Blizzard data): your own fresher one, else the one in the repository
foreach ($kb in @((Join-Path $Root "companion\data\kb.sqlite"), (Join-Path $Root "seed\kb.sqlite"))) {
  if (Test-Path $kb) { $data += @("--add-data", "$kb;seed"); break }
}

Push-Location (Join-Path $Root "companion")
& $py -m PyInstaller --noconfirm --clean --windowed --name $name `
  --icon "$Root\assets\foreverai.ico" `
  --distpath "$Root\release\windows" --workpath "$Root\build\windows" --specpath "$Root\build\windows" `
  --collect-all curl_cffi `
  @data foreverai_app.py
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw "PyInstaller failed ($code)" }
Write-Host "Built: $Root\release\windows\$name\$name.exe (ForeverAI $version)"

if ($Package) {
  $archive = Join-Path $Root "release\ForeverAI-$version-Windows-x64.zip"
  if (Test-Path $archive) { Remove-Item $archive }
  Compress-Archive -Path (Join-Path $Root "release\windows\$name") -DestinationPath $archive
  Write-Host "Package: $archive"
}
exit 0
