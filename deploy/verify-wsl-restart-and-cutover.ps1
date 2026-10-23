# Run from Windows PowerShell, after saving work in the WSL IDE.
# This terminates Ubuntu-20.04 and its active Codex/SSH sessions.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$distribution = 'Ubuntu-20.04'
$projectRoot = '/home/xjx/opencode-agent-demo'
$python = "$projectRoot/.venv/bin/python"
$verifier = "$projectRoot/scripts/verify_wsl_restart.py"
$ssh = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$knownHosts = "$env:USERPROFILE/.ssh/opencode_known_hosts"
$sshArgs = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'StrictHostKeyChecking=yes', '-o', "UserKnownHostsFile=$knownHosts", 'xjx@100.64.0.6')
$evidenceRoot = "\\wsl.localhost\$distribution\home\xjx\opencode-agent-demo\docs\runbooks"

function Assert-Exit([string]$operation) {
    if ($LASTEXITCODE -ne 0) { throw "$operation failed with exit code $LASTEXITCODE. Remote application has not been deliberately stopped." }
}

function Invoke-Verification([string]$phase) {
    & wsl.exe -d $distribution -u xjx --exec /usr/bin/env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus $python $verifier $phase
    Assert-Exit "WSL verification ($phase)"
}

# Check SSH before the disruptive step. Never reset Tailscale Serve rules.
& $ssh @sshArgs 'systemctl --user is-active 719agent-demo-model-gateway.service'
Assert-Exit 'Remote model gateway preflight'
Invoke-Verification 'baseline'
Write-Host 'Restarting WSL; the current WSL IDE and Codex connections will close.'
& wsl.exe --terminate $distribution
Assert-Exit 'Terminate WSL'
Start-Sleep -Seconds 3
Invoke-Verification 'after-restart'

# Check Windows forwarding and an actual other tailnet host before cutover.
& curl.exe --noproxy '*' --fail --max-time 15 --silent --output NUL http://127.0.0.1:18080/
Assert-Exit 'Windows localhost forwarding'
$remoteHealth = & $ssh @sshArgs "curl --noproxy '*' --fail --max-time 15 --silent http://100.64.0.17:18080/api/v1/health"
Assert-Exit 'Tailnet health from server9'
if (-not (($remoteHealth -join "`n" | ConvertFrom-Json).ready)) { throw 'Tailnet deployment is not ready; old services remain.' }
& $ssh @sshArgs "curl --noproxy '*' --fail --max-time 15 --silent --output /dev/null http://100.64.0.17:5173/"
Assert-Exit 'Existing LycheeRAG entry'

$oldServices = '719agent-demo-tailnet.service 719agent-demo-backend.service 719agent-demo-opencode.service 719agent-demo-loop.service'
& $ssh @sshArgs "systemctl --user disable --now $oldServices"
Assert-Exit 'Stop old remote application services'
& $ssh @sshArgs 'systemctl --user is-active 719agent-demo-model-gateway.service'
Assert-Exit 'Preserved remote model gateway'

@{
    completed_at = (Get-Date).ToUniversalTime().ToString('o')
    entry = 'http://100.64.0.17:18080'
    restart_verified = $true
    tailnet_verified_from_server9 = $true
    lychee_entry_verified = $true
    old_remote_application_disabled = $true
    remote_model_gateway_preserved = $true
} | ConvertTo-Json | Set-Content -Encoding UTF8 "$evidenceRoot\wsl-cutover-complete.json"
Write-Host 'Restart verification and cutover complete: http://100.64.0.17:18080'
