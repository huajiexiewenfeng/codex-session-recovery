param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$LogPath = "",
    [switch]$StopCodexFirst,
    [switch]$SkipNormalizeCwd,
    [switch]$SurfaceSidebar,
    [int]$SurfacePerProject = 2,
    [int]$SurfaceMaxTotal = 50
)

$ErrorActionPreference = "Stop"

$projectRootResolved = (Resolve-Path -LiteralPath $ProjectRoot).Path
$safeName = ($projectRootResolved -replace '^[A-Za-z]:\\?', '' -replace '[\\/:*?"<>| ]+', '-').Trim('-')
if ([string]::IsNullOrWhiteSpace($safeName)) {
    $safeName = "project"
}

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $projectRootResolved ".codex-session-recovery-$safeName.log"
}

$python = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$recoverScript = Join-Path $scriptDir "restore_codex_project_sessions.py"
$reparentScript = Join-Path $scriptDir "reparent_codex_sessions.py"
$surfaceScript = Join-Path $scriptDir "surface_codex_sidebar_threads.py"

"[$(Get-Date -Format o)] Starting Codex session recovery helper..." | Out-File -FilePath $LogPath -Encoding utf8
"[$(Get-Date -Format o)] Project root: $projectRootResolved" | Out-File -FilePath $LogPath -Encoding utf8 -Append

if ($StopCodexFirst) {
    "[$(Get-Date -Format o)] Stopping Codex processes before recovery..." | Out-File -FilePath $LogPath -Encoding utf8 -Append
    Get-Process | Where-Object { $_.ProcessName -match '^(Codex|codex)$' } | Stop-Process -Force -ErrorAction Continue 2>&1 | Out-File -FilePath $LogPath -Encoding utf8 -Append
    Start-Sleep -Milliseconds 500
}

"[$(Get-Date -Format o)] Waiting for Codex processes to exit..." | Out-File -FilePath $LogPath -Encoding utf8 -Append

while (Get-Process | Where-Object { $_.ProcessName -match '^(Codex|codex)$' }) {
    Start-Sleep -Seconds 2
}

"[$(Get-Date -Format o)] Codex processes exited. Running recovery..." | Out-File -FilePath $LogPath -Encoding utf8 -Append
& $python $recoverScript --project-root $projectRootResolved --write --normalize-windows-extended-paths 2>&1 | Out-File -FilePath $LogPath -Encoding utf8 -Append

if (-not $SkipNormalizeCwd) {
    "[$(Get-Date -Format o)] Running compatibility same-root reparent for exact UI matching..." | Out-File -FilePath $LogPath -Encoding utf8 -Append
    & $python $reparentScript --old-root $projectRootResolved --new-root $projectRootResolved --write 2>&1 | Out-File -FilePath $LogPath -Encoding utf8 -Append
}

if ($SurfaceSidebar) {
    $surfaceReport = Join-Path "C:\tmp" ("codex-sidebar-surface-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
    "[$(Get-Date -Format o)] Surfacing restored sessions into the sidebar recent page..." | Out-File -FilePath $LogPath -Encoding utf8 -Append
    & $python $surfaceScript --write --per-project $SurfacePerProject --max-total $SurfaceMaxTotal --report-path $surfaceReport 2>&1 | Out-File -FilePath $LogPath -Encoding utf8 -Append
    "[$(Get-Date -Format o)] Sidebar surface report: $surfaceReport" | Out-File -FilePath $LogPath -Encoding utf8 -Append
}

"[$(Get-Date -Format o)] Verifying recovery..." | Out-File -FilePath $LogPath -Encoding utf8 -Append
& $python $recoverScript --project-root $projectRootResolved 2>&1 | Out-File -FilePath $LogPath -Encoding utf8 -Append

"[$(Get-Date -Format o)] Done." | Out-File -FilePath $LogPath -Encoding utf8 -Append
