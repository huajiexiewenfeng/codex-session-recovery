# Codex Session Recovery Skill

[English](README.md) | [中文](README.zh-CN.md)

Codex Session Recovery Skill is an unofficial recovery workflow for Codex Desktop project history disappearing after updates. It helps recover local sessions that still exist on disk but are no longer visible in the new Codex UI because local session metadata, project assignments, sidebar state, SQLite `cwd` values, or Windows path formats no longer match.

This repository packages the `codex-session-recovery` skill and its helper scripts so it can be installed and reused with Codex Skills.

## What Problem It Solves

After a Codex Desktop update, some users see symptoms such as:

- Project chat history disappears.
- The sidebar shows no old chats for a project.
- `sessions/**/*.jsonl` still exists locally.
- `state_5.sqlite` still contains old threads.
- Older Windows or WSL paths no longer match the current project root.
- Paths like `\\?\D:\repo\project` and `D:\repo\project` refer to the same folder but do not match as UI filter strings.

In these cases, the session data may not be deleted. The new Codex UI may simply be unable to find it through the current index, project mapping, or exact `cwd` lookup.

## Screenshots

Skill usage: dry-run finds local history sessions, missing sidebar/project metadata, and Windows `cwd` exact-match risk before writing.

![Codex Session Recovery skill usage](assets/skill-usage.png)

Recovery result: after metadata repair and path normalization, the historical project session appears in Codex Desktop again.

![Codex Session Recovery success](assets/recovery-success.png)

## Safety

The skill is designed for local recovery and is intentionally conservative:

- Runs in dry-run mode first.
- Matches sessions by exact project root.
- Does not delete session files.
- Creates timestamped backups before writing.
- Detects running Codex processes before modifying global state.
- Normalizes Windows extended-length paths only when requested by the recovery workflow.
- Verifies recovery by requiring missing metadata counts to reach zero.

This is not an official OpenAI fix. Use it as an unofficial local workaround while upstream issues are being investigated.

## Install

Install the skill with Skills CLI:

```bash
npx skills add https://github.com/huajiexiewenfeng/codex-session-recovery --skill codex-session-recovery
```

List available skills from the repository:

```bash
npx skills add https://github.com/huajiexiewenfeng/codex-session-recovery --list
```

## Codex Usage Examples

After installation, ask Codex in plain language:

```text
Use codex-session-recovery to recover the missing history sessions for the current project.
```

```text
My Codex Desktop project history disappeared after an update. Please dry-run the recovery first and show me the report.
```

```text
Repair the current project's Codex session metadata, but make backups and verify missing_* counts afterward.
```

## Direct Script Examples

Run a dry-run audit from the repository root:

```bash
python -B skills/codex-session-recovery/scripts/restore_codex_project_sessions.py --project-root "D:\path\to\project"
```

Write recovery changes after reviewing the dry-run report:

```bash
python -B skills/codex-session-recovery/scripts/restore_codex_project_sessions.py --project-root "D:\path\to\project" --write --normalize-windows-extended-paths
```

Normalize same-root Windows `\\?\` path mismatches:

```bash
python -B skills/codex-session-recovery/scripts/reparent_codex_sessions.py --old-root "D:\path\to\project" --new-root "D:\path\to\project" --write
```

Use the Windows helper when Codex Desktop must exit before recovery:

```powershell
$skill = "skills\codex-session-recovery"
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","$skill\scripts\run_recovery_after_codex_exit.ps1","-ProjectRoot",(Get-Location).Path,"-LogPath","C:\tmp\codex-session-recovery.log","-StopCodexFirst") -WindowStyle Hidden
```

If the hidden helper creates no log and Codex processes remain, run the helper with UAC:

```powershell
$skill = "skills\codex-session-recovery"
$script = "$skill\scripts\run_recovery_after_codex_exit.ps1"
$root = (Get-Location).Path
$log = "C:\tmp\codex-session-recovery-uac.log"
$args = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -ProjectRoot `"$root`" -LogPath `"$log`" -StopCodexFirst"
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $args -Verb RunAs -WindowStyle Normal
```

## Recovery Report Fields

Important dry-run and verification fields:

| Field | Meaning |
| --- | --- |
| `exact_project_sessions` | Sessions whose metadata exactly matches the project root |
| `missing_from_session_index` | Sessions missing from `session_index.jsonl` |
| `missing_global_hints` | Sessions missing from global workspace root hints |
| `missing_project_assignments` | Sessions missing from project assignment state |
| `missing_sidebar_order` | Sessions missing from sidebar project ordering |
| `sqlite_exact_ui_cwd_threads` | SQLite threads whose `cwd` exactly matches the UI project root |
| `sqlite_non_exact_ui_cwd_threads` | SQLite threads related to the project but not exact UI matches |
| `sqlite_extended_path_threads` | Threads using Windows `\\?\` extended-length paths |
| `cwd_exact_match_risk` | Whether the UI may hide threads due to string-exact `cwd` mismatch |

## Success Criteria

Do not treat script completion alone as success. A successful recovery should verify:

- `missing_from_session_index: 0`
- `missing_global_hints: 0`
- `missing_project_assignments: 0`
- `missing_sidebar_order: 0`
- `cwd_exact_match_risk: false`

If these are zero but the UI is still empty, check whether the visible project root differs from the session `cwd`, including Git root vs subdirectory mismatches.

## Related Upstream Issues

Examples of related Codex Desktop history disappearance reports:

- https://github.com/openai/codex/issues/20741
- https://github.com/openai/codex/issues/22796
- https://github.com/openai/codex/issues/23193
- https://github.com/openai/codex/issues/23979
- https://github.com/openai/codex/issues/24364

## Verify

Validate skill metadata:

```bash
python C:\Users\admin\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills/codex-session-recovery
```

Verify local Skills CLI discovery:

```bash
npx skills add . --list
```

## License

MIT
