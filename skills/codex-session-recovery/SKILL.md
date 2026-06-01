---
name: codex-session-recovery
description: Use when Codex Desktop sessions, threads, sidebar history, project conversations, or local session lists are missing after an update or restart, only one conversation is visible, sessions need to be restored for a workspace, or the user asks to repair Codex session_index/state/global-state/cwd metadata. This skill audits and restores local Codex session metadata safely.
---

# Codex Session Recovery

Use this skill to recover local Codex Desktop sessions that still exist on disk but are missing from the UI.

The common failure mode is:

- `sessions/**/*.jsonl` and `state_5.sqlite` still contain the threads.
- `session_index.jsonl` may miss some records.
- `.codex-global-state.json` may miss project/sidebar assignment keys such as `project-order`, `thread-workspace-root-hints`, `thread-project-assignments`, and `sidebar-project-thread-orders`.
- Running Codex Desktop may overwrite manual edits with its in-memory state.

## Safety Rules

- Never delete session files.
- Always make timestamped backups before writing.
- Prefer exact project-root matching from `session_meta.payload.cwd`.
- Do not treat full-text mentions of a path as project ownership unless the user explicitly asks for that broader match.
- If Codex Desktop is running, do not write global state unless the user explicitly approves a running-app write. Explain that Electron may overwrite the file again.
- If the UI still does not show restored threads after `missing_*` counts are zero, do not keep guessing global-state keys. Check whether the visible project root differs from the session `cwd`.
- `thread/list.cwd` is an exact match filter, not a prefix filter. A thread whose `cwd` is `D:\repo\subdir` may not appear under a UI project whose root is `D:\repo`.
- Codex Desktop 26.527 builds the project sidebar from a recent-thread cache that starts with `thread/list limit: 50`. If a fresh app-server `thread/list --cwd <root>` returns the old sessions but the UI project still says "No recent chats", the sessions may simply be below the first global recent page. Use `surface_codex_sidebar_threads.py` after backing up SQLite, JSONL rollouts, and global state. SQLite-only timestamp updates are not durable because app-server read-repair can rebuild them from JSONL on restart.
- Treat Windows extended-length paths as a separate exact-match risk. `\\?\D:\repo\project` and `D:\repo\project` normalize to the same filesystem path, but the Codex UI `cwd` filter may not treat them as the same string. If history is still hidden after metadata repair, inspect SQLite `threads.cwd` for a `\\?\` prefix and normalize it with `reparent_codex_sessions.py --old-root <root> --new-root <root> --write`.
- When the target directory is a Git subdirectory, compare `git rev-parse --show-toplevel` with `session_meta.payload.cwd`. If Codex groups the sidebar by the Git root, reparent matching sessions to the Git root only after backing up SQLite and JSONL files.

## Workflow

1. Resolve `CODEX_HOME`.
   - Prefer `$env:CODEX_HOME` when it points to a real directory.
   - Otherwise use `%USERPROFILE%\.codex`.
2. Resolve the target project root.
   - Use the user's provided path.
   - If omitted and the current workspace is the target, use the current working directory.
3. Run the bundled script in dry-run mode first.
4. Read the report:
   - `exact_project_sessions`
   - `missing_from_session_index`
   - `missing_global_hints`
   - `sqlite_exact_ui_cwd_threads`
   - `sqlite_non_exact_ui_cwd_threads`
   - `cwd_exact_match_risk`
   - `codex_processes_running`
   - `can_write_global_state_safely`
5. If Codex is running and global-state repair is needed:
   - Give the user a short, step-based status summary before asking for action.
   - Do not only say "fully quit Codex"; many users do not know how to close Electron, tray, and helper `codex.exe` processes.
   - Ask whether they want the agent to automatically close/restart Codex and continue recovery.
   - If the user agrees, use an external helper process so recovery continues after the current Codex window exits.
   - If the user declines, ask them to fully quit Codex Desktop, then run the script from an external terminal.
   - If the user accepts overwrite risk, run with `--allow-running-codex`, but explain that SQLite may still be locked and Electron may overwrite global state.
6. Run write mode. On Windows, prefer `--normalize-windows-extended-paths` so the main script also rewrites same-directory `\\?\D:\...` cwd values to the exact display root string.
7. Re-run dry-run mode to verify:
   - `missing_from_session_index: 0`
   - `missing_global_hints: 0`
   - `missing_project_assignments: 0`
   - `missing_sidebar_order: 0`
   - `cwd_exact_match_risk: false`
8. If the UI still does not show the threads:
   - Inspect `app-server` schema or generated JSON schema for `ThreadListParams`.
   - Remember that `cwd` is exact match.
   - Inspect `state_5.sqlite` `threads.cwd`; if rows use `\\?\D:\...`, run a same-root reparent to rewrite them to the display root string.
   - Run `git -C <project-root> rev-parse --show-toplevel`.
   - If the Git root differs from the project root, repair the display root or reparent sessions to the Git root.
   - If exact `cwd` is correct and a fresh app-server returns the hidden threads, inspect global `thread/list` rank. Codex Desktop may only have loaded the first 50 recent threads into the project sidebar.
   - In that case, run `surface_codex_sidebar_threads.py` to promote a bounded, round-robin set of restored project threads into the recent cache. This updates JSONL `task_complete.completed_at`, SQLite `updated_at`/`updated_at_ms`, and global project mappings with backups/report.
9. If recovery needs a restart, tell the user what will happen next and ask whether to perform it automatically. If they agree, run the restart helper. If not, tell them to reopen Codex Desktop and check the project list.
10. After starting any hidden external helper, verify that it actually took over before claiming it is running:
   - Check that the expected log file exists under the project root, or pass an explicit `-LogPath` under `C:\tmp`.
   - Check for a surviving `powershell.exe` helper process if the log is missing.
   - If no log appears and `Codex`/`codex` processes remain, treat the helper as failed to start or failed before logging. Do not tell the user recovery is underway.
   - Escalate to the UAC helper path when process inspection or stopping reports `Access is denied`, when Codex processes have `Path` as empty/null, or when WindowsApps Codex processes remain after `-StopCodexFirst`.

## User-Facing Prompt Pattern

When dry-run finds recoverable sessions but Codex is running, keep the user update concise and operational. Prefer this shape:

```text
1. 已按 `codex-session-recovery` 流程处理到安全恢复阶段。
2. Dry-run 结果：当前项目 `<project-root>` 找到 `<N>` 条历史 session。
3. `session_index` <缺失情况>，全局侧边栏映射 <缺失情况>，SQLite 的 `cwd` <是否存在 `\\?\` 精确匹配风险>。
4. 日志在这里：`<log-path>`。

现在需要重启 Codex Desktop 才能继续恢复。是否需要我帮你自动关闭并重启 Codex？
```

If the user says yes, proceed with the external helper instead of asking them to manually find every Codex process. If the user says no, provide the exact PowerShell command they can run after closing Codex.

## Automatic Close/Restart Guidance

Use automatic close/restart when all are true:

- Codex is running and `can_write_global_state_safely` is false, or SQLite reports `disk I/O error`.
- The user explicitly agrees to let the agent close/restart Codex.
- You can start an external PowerShell process that survives the current Codex window exiting.

Important Windows notes:

- Do not launch a helper that kills its own process tree. If using `taskkill`, avoid `/T` when the recovery PowerShell was started by Codex; `/T` can terminate the helper before it writes metadata.
- Prefer the bundled `run_recovery_after_codex_exit.ps1` when it is sufficient because it waits for Codex to exit and then runs verification.
- If you must create a temporary helper, avoid hard-coded non-ASCII project paths inside `.ps1` source. Use `$PSScriptRoot`, `(Get-Location).Path`, or pass the path as an argument so Windows PowerShell 5 encoding does not garble Chinese paths.
- If `Start-Process` fails with duplicate `Path`/`PATH` environment keys, remove the duplicate process-level `PATH` variable before starting the external process.
- If stopping Codex fails with `Access is denied`, use `Start-Process -Verb RunAs` and tell the user to approve the UAC prompt.
- If a hidden helper returns immediately, creates no log, or leaves all Codex processes running, do not retry the same hidden command. Re-run with an explicit `-LogPath` in `C:\tmp`; if the log is still absent or says access is denied, use the UAC helper.
- If a hidden helper launched with an explicit `C:\tmp` log path still creates no log after 2-3 seconds and the same `Codex`/`codex` processes remain, treat this as "helper did not take over" rather than "recovery is still running". Go directly to the UAC helper and report the missing log as the reason.
- After automatic recovery, re-run dry-run and require `missing_*: 0` and `cwd_exact_match_risk: false` before calling the recovery complete.

## UI Still Empty After Successful Global-State Repair

If `missing_from_session_index`, `missing_global_hints`, `missing_project_assignments`, and `missing_sidebar_order` are all zero but the left sidebar still does not show history, do not call the recovery successful yet. Check these in order:

1. Confirm Codex actually ran the latest recovery script. The log should include the expected step names. A waiting script started before an edit keeps running the old script content.
2. Confirm no `Codex` or `codex` processes are still alive before writing. Running Electron can overwrite state from memory.
3. Check the main script's cwd exact-match fields:

```text
sqlite_exact_ui_cwd_threads
sqlite_non_exact_ui_cwd_threads
sqlite_extended_path_threads
cwd_exact_match_risk
recommended_cwd_normalization
```

If `cwd_exact_match_risk` is `true`, the Codex UI may still hide the threads even though all global-state `missing_*` counts are zero.

4. Check for Windows extended-length path mismatch in SQLite:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $py -c "import sqlite3; con=sqlite3.connect(r'C:\Users\admin\.codex\state_5.sqlite'); [print(row) for row in con.execute(\"select id,cwd,title from threads where cwd like '%smartghub%' order by created_at desc\")]"
```

If `threads.cwd` starts with `\\?\` but the UI project root is the normal path, normalize the same project root with the main restore script:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
$root = (Get-Location).Path
& $py "$skill\scripts\restore_codex_project_sessions.py" --project-root $root --write --normalize-windows-extended-paths
```

5. Check the app-server contract:

```powershell
$env:CODEX_HOME = "C:\Users\admin\.codex"
codex app-server generate-json-schema --out .\app-server-schema
Get-Content .\app-server-schema\v2\ThreadListParams.json
```

The `cwd` filter says: only threads whose session cwd exactly matches one of the paths are returned.

6. Compare project root with Git root:

```powershell
git -C "D:\workspace\drone\cloudsmartgo\smart-go-web" rev-parse --show-toplevel
```

For this machine, `smart-go-web` is under Git root:

```text
D:\workspace\drone\cloudsmartgo
```

If the UI project is the Git root but thread rows use `D:\workspace\drone\cloudsmartgo\smart-go-web`, the sidebar can stay empty even though global-state mappings look correct.

7. In that case, reparent only the exact matched project sessions:
   - backup `state_5.sqlite`
   - backup every changed `sessions/**/*.jsonl`
   - update `threads.cwd`
   - update `session_meta.payload.cwd` and `turn_context.payload.cwd` entries in JSONL
   - update `.codex-global-state.json` project mappings to the display root

8. If a visible new test conversation appears but older conversations in the same project do not:
   - Query a fresh app-server with `thread/list` and `cwd=<project-root>`. If both new and old sessions are returned, the data is restored.
   - Query global `thread/list` by `updated_at`; if the old session is ranked after the first page (for example rank 146), the UI has not hydrated that metadata.
   - Run sidebar surfacing. Use the bounded form to put at least a few threads from each project into the first recent page:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
& $py "$skill\scripts\surface_codex_sidebar_threads.py" --per-project 2 --max-total 50
& $py "$skill\scripts\surface_codex_sidebar_threads.py" --per-project 2 --max-total 50 --write --report-path "C:\tmp\codex-sidebar-surface.json"
```

   - If a pin/unpin test proves the UI can show a hydrated old thread but loses it after restart, do not promote every session into the recent page. Promoting all sessions can let large projects dominate the first 50 rows again. Use the bounded round-robin form above (`--per-project 2 --max-total 50`) so each project gets representative threads in the first recent page.

   - Only use the all-session form as a metadata normalization pass, not as the UI hydration step:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
& $py "$skill\scripts\surface_codex_sidebar_threads.py" --per-project 0 --max-total 0 --write --report-path "C:\tmp\codex-sidebar-surface-all.json"
```

   - Restart Codex Desktop after write mode so the sidebar rebuilds its recent-thread cache.

## Commands

Use the bundled Python if regular `python` is unavailable:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
& $py "$skill\scripts\restore_codex_project_sessions.py" --project-root "D:\workspace\drone\cloudsmartgo\smart-go-web"
```

Global-state-only recovery, useful when `state_5.sqlite` is locked or the old script's SQLite report path fails:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
& $py "$skill\scripts\recover_codex_global_state.py" --project-root "D:\workspace\drone\cloudsmartgo\smart-go-web" --project-display-root "D:\workspace\drone\cloudsmartgo" --write
```

Reparent sessions when app-server `thread/list.cwd` exact matching is the suspected root cause:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
& $py "$skill\scripts\reparent_codex_sessions.py" --old-root "D:\workspace\drone\cloudsmartgo\smart-go-web" --new-root "D:\workspace\drone\cloudsmartgo" --write
```

Normalize Windows `\\?\` cwd prefixes without changing the project root:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
$root = (Get-Location).Path
& $py "$skill\scripts\reparent_codex_sessions.py" --old-root $root --new-root $root --write
```

Write mode:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
& $py "$skill\scripts\restore_codex_project_sessions.py" --project-root "D:\workspace\drone\cloudsmartgo\smart-go-web" --write
```

Windows write mode with exact UI cwd normalization:

```powershell
$py = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
& $py "$skill\scripts\restore_codex_project_sessions.py" --project-root (Get-Location).Path --write --normalize-windows-extended-paths
```

If the user has already fully closed Codex Desktop, write mode is safe. If Codex is still running and the user explicitly accepts the risk:

```powershell
& $py "$skill\scripts\restore_codex_project_sessions.py" --project-root "D:\workspace\drone\cloudsmartgo\smart-go-web" --write --allow-running-codex
```

When a running Codex instance makes it impossible to write safely, start the bundled external PowerShell helper. It defaults to the current directory as `ProjectRoot`, waits until all `Codex`/`codex` processes exit, runs recovery, normalizes same-root `cwd` values, and verifies the result:

```powershell
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","$skill\scripts\run_recovery_after_codex_exit.ps1","-ProjectRoot",(Get-Location).Path) -WindowStyle Hidden
```

If the log only says `Waiting for Codex processes to exit...`, there are still Codex processes alive. Check with:

```powershell
Get-Process | Where-Object { $_.ProcessName -match '^(Codex|codex)$' }
```

If the user explicitly asks the agent to close Codex, or Codex keeps auto-restarting before the helper can observe a clean exit, use the helper with `-StopCodexFirst` from an external process:

```powershell
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","$skill\scripts\run_recovery_after_codex_exit.ps1","-ProjectRoot",(Get-Location).Path,"-StopCodexFirst") -WindowStyle Hidden
```

To also surface old sessions into the sidebar's recent cache after the recovery write:

```powershell
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","$skill\scripts\run_recovery_after_codex_exit.ps1","-ProjectRoot",(Get-Location).Path,"-StopCodexFirst","-SurfaceSidebar","-SurfacePerProject","2","-SurfaceMaxTotal","50") -WindowStyle Hidden
```

For metadata normalization across all restored interactive sessions, use `-SurfacePerProject 0 -SurfaceMaxTotal 0`. This may update many JSONL rollout files, but it is not a replacement for the bounded first-page UI hydration step. After running all-session normalization, run bounded surfacing again with `-SurfacePerProject 2 -SurfaceMaxTotal 50`.

Immediately verify helper takeover:

```powershell
$root = (Get-Location).Path
$safeName = ($root -replace '^[A-Za-z]:\\?', '' -replace '[\\/:*?"<>| ]+', '-').Trim('-')
$log = Join-Path $root ".codex-session-recovery-$safeName.log"
Start-Sleep -Seconds 2
if (-not (Test-Path -LiteralPath $log)) {
    Get-Process powershell -ErrorAction SilentlyContinue | Select-Object Id,StartTime,Path
    Get-Process | Where-Object { $_.ProcessName -match '^(Codex|codex)$' } | Select-Object Id,ProcessName,Path
    throw "Recovery helper did not create its log; use explicit -LogPath or UAC helper."
}
Get-Content -LiteralPath $log -Tail 20
```

If the user agreed to automatic recovery and normal `Start-Process` fails because the process environment contains both `Path` and `PATH`, remove the duplicate process-level key before starting the helper:

```powershell
$envs = [System.Environment]::GetEnvironmentVariables("Process")
if ($envs.Contains("Path") -and $envs.Contains("PATH")) {
    $pathValue = $envs["Path"]
    [System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [System.Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","$skill\scripts\run_recovery_after_codex_exit.ps1","-ProjectRoot",(Get-Location).Path,"-StopCodexFirst") -WindowStyle Hidden
```

If `Stop-Process` fails with `Access is denied`, ask the user to approve an elevated helper with UAC:

```powershell
$skill = "C:\Users\admin\.codex\skills\codex-session-recovery"
$script = "$skill\scripts\run_recovery_after_codex_exit.ps1"
$root = (Get-Location).Path
$log = "C:\tmp\codex-session-recovery-$((Split-Path -Leaf $root) -replace '[\\/:*?""<>| ]+', '-').log"
$args = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -ProjectRoot `"$root`" -LogPath `"$log`" -StopCodexFirst"
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $args -Verb RunAs -WindowStyle Normal
```

When creating a one-off helper script, do not use `taskkill /T` from a process launched by Codex. It can kill the helper itself before recovery runs. Use `taskkill /F /IM Codex.exe` and `taskkill /F /IM codex.exe` without `/T`, then run the recovery and verification commands.

## Output Interpretation

- `write_performed: false` in dry-run mode is expected.
- `missing_from_session_index: 0` means the old lightweight index is repaired.
- `missing_global_hints: 0` and `missing_project_assignments: 0` mean the sidebar/project state has the target mappings.
- `cwd_exact_match_risk: false` means the SQLite `threads.cwd` values are exact string matches for the UI project root. Treat this as part of the success criteria, not an optional diagnostic.
- If `codex_processes_running` is non-empty and global-state fields remain missing after a write, Codex likely overwrote the file from memory. Close Codex fully and run the script externally.
- If these counts are zero but the UI still shows no threads, suspect exact `cwd` mismatch between app-server `thread/list` and the UI project root.
- If `missing_*` counts are zero and global state contains the project, but the UI is still empty, inspect `state_5.sqlite` for `threads.cwd` values like `\\?\D:\...`. A same-root reparent is required because the UI filter can be string-exact.
- If the log says `Done.` but does not include the expected reparent step, the background process was started before the script was updated. Start a new wait script.
- If the log stops immediately after `Stopping Codex processes...`, suspect the helper was killed by its own close command or is blocked by permissions. Use the automatic close/restart guidance above.
