param(
    [string]$Repository,
    [string]$ProjectRoot,
    [string]$Branch,
    [string]$TunnelId,
    [string]$TunnelClient = "tunnel-client"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Repository) -and [string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $Repository = Read-Host "GitHub HTTPS repository URL (or pass -ProjectRoot for an existing local checkout)"
}
if (-not [string]::IsNullOrWhiteSpace($Repository) -and -not [string]::IsNullOrWhiteSpace($ProjectRoot)) {
    throw "Choose either -Repository or -ProjectRoot, not both."
}
if (-not [string]::IsNullOrWhiteSpace($Branch) -and [string]::IsNullOrWhiteSpace($Repository)) {
    throw "-Branch can only be used with -Repository."
}
if ([string]::IsNullOrWhiteSpace($TunnelId)) {
    $TunnelId = Read-Host "OpenAI Platform tunnel ID"
}
if ([string]::IsNullOrWhiteSpace($TunnelId)) {
    throw "A tunnel ID is required. Create or select one in https://platform.openai.com/settings/organization/tunnels."
}

$cliPath = Join-Path $PSScriptRoot "..\auren_cli.py"
if (Test-Path -LiteralPath $cliPath -PathType Leaf) {
    $python = (Get-Command python -ErrorAction Stop).Source
    $cliCommand = $python
    $cliPrefix = @((Resolve-Path $cliPath).Path)
} else {
    $cliCommand = (Get-Command auren -ErrorAction Stop).Source
    $cliPrefix = @()
}
$null = Get-Command $TunnelClient -ErrorAction Stop

$secureKey = Read-Host "OpenAI Secure MCP Tunnel runtime API key" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $arguments = @($cliPrefix + @("chatgpt-tunnel", "run", "--tunnel-id", $TunnelId, "--tunnel-client", $TunnelClient))
    if (-not [string]::IsNullOrWhiteSpace($Repository)) {
        $arguments += @("--repository", $Repository)
        if (-not [string]::IsNullOrWhiteSpace($Branch)) {
            $arguments += @("--branch", $Branch)
        }
    } else {
        $arguments += @("--project-root", (Resolve-Path $ProjectRoot).Path)
    }
    Write-Host "Starting AUREN and its private ChatGPT tunnel. Keep this window open."
    Write-Host "Use Ctrl+C to stop both services."
    & $cliCommand @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "AUREN ChatGPT tunnel exited with code $LASTEXITCODE."
    }
}
finally {
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secureKey.Dispose()
}
