# Set up a development environment without admin rights (Windows).
#
# Creates .tools\ (a venv holding only uv) with Python 3.11+ from the
# py launcher or PATH, then lets uv create .venv\ with the project and
# its dev dependencies. Nothing is installed outside this folder.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$py = $env:PYTHON
if (-not $py) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" } else { $py = "python" }
}

& $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python 3.11 or newer is required. Set `$env:PYTHON to its path and run again."
}

$uv = ".tools\Scripts\uv.exe"
if (-not (Test-Path $uv)) {
    Write-Host "Creating .tools\ with uv"
    & $py -m venv .tools
    if ($LASTEXITCODE -ne 0) { throw "could not create .tools" }
    & .tools\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .tools\Scripts\python.exe -m pip install --quiet uv
    if ($LASTEXITCODE -ne 0) { throw "could not install uv" }
}

$pyPath = & $py -c "import sys; print(sys.executable)"
& $uv sync --python $pyPath
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

Write-Host ""
Write-Host "Ready. Run the tests with:"
Write-Host "  .tools\Scripts\uv.exe run pytest"
