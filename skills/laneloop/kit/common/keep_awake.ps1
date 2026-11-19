# Keeps Windows from sleeping until the process <ProcessId> exits. Started in the background by platform.sh keep_awake
# for each loop and watcher; the hold ends when this script does.
#   powershell -NoProfile -File keep_awake.ps1 <ProcessId>
param([int]$ProcessId)
Add-Type -Name Power -Namespace LaneLoop -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
'@
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: no idle sleep while this thread holds it (the display may still turn off)
[LaneLoop.Power]::SetThreadExecutionState([uint32]2147483649) | Out-Null
try { Wait-Process -Id $ProcessId -ErrorAction Stop } catch { }
