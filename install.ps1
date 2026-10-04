$ErrorActionPreference = "Stop"

$repo = "sourabhJainR/AUREN"
$version = if ($env:AUREN_VERSION) { $env:AUREN_VERSION } else { "latest" }
$tmp = Join-Path $env:TEMP ("auren-install-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $tmp | Out-Null

try {
  $bundle = Join-Path $tmp "auren-portable.zip"
  Invoke-WebRequest -Uri "https://github.com/$repo/releases/$version/download/auren-portable.zip" -OutFile $bundle
  $expanded = Join-Path $tmp "bundle"
  Expand-Archive -Path $bundle -DestinationPath $expanded -Force
  python (Join-Path $expanded "auren_cli.py") $bundle
} finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
