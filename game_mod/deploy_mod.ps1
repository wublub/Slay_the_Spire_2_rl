$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$gameRoot = Resolve-Path (Join-Path $repoRoot "..")
$project = Join-Path $PSScriptRoot "Sts2RlBridge\Sts2RlBridge.csproj"
$buildOut = Join-Path $PSScriptRoot "Sts2RlBridge\bin\Release\net9.0"
$modRoots = @(
    (Join-Path $gameRoot "mods\Sts2RlBridge")
)

dotnet build $project -c Release | Out-Host

$deployed = @()
$failed = @()

foreach ($modRoot in $modRoots) {
    try {
        New-Item -ItemType Directory -Force -Path $modRoot | Out-Null
        Copy-Item (Join-Path $buildOut "Sts2RlBridge.dll") $modRoot -Force

        $manifest = @{
            id = "Sts2RlBridge"
            name = "STS2 RL Bridge"
            author = "Codex"
            version = "0.1.0"
            description = "Bridge trained RL models into Slay the Spire 2 via local WebSocket."
            has_dll = $true
            has_pck = $false
            affects_gameplay = $true
            dependencies = @()
        } | ConvertTo-Json -Depth 4

        @("mod_manifest.json", "mod.json") | ForEach-Object {
            $path = Join-Path $modRoot $_
            if (Test-Path $path) {
                Remove-Item -LiteralPath $path -Force
            }
        }

        Set-Content -Path (Join-Path $modRoot "manifest.json") -Value $manifest -Encoding UTF8
        $deployed += $modRoot
    }
    catch {
        $failed += [PSCustomObject]@{
            Path = $modRoot
            Error = $_.Exception.Message
        }
    }
}

if ($deployed.Count -gt 0) {
    Write-Host "Deployed to:"
    $deployed | ForEach-Object { Write-Host "  $_" }
}

if ($failed.Count -gt 0) {
    Write-Warning "Some deploy targets failed:"
    $failed | ForEach-Object { Write-Warning ("  " + $_.Path + " :: " + $_.Error) }
}

if ($deployed.Count -eq 0) {
    throw "Deploy failed for all targets."
}
