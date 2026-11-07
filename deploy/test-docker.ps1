# Run in the operator's normal PowerShell. Uses isolated fixture data and no API credits.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$record = Join-Path $project ".planning/reviews/2026-11-02-beta-launch/docker-$stamp"
New-Item -ItemType Directory -Path $record | Out-Null
$log = Join-Path $record 'docker.log'
$stack = "verifier-smoke-$($stamp.ToLowerInvariant())"
$image = "claim-verifier:smoke-$($stamp.ToLowerInvariant())"
$config = Join-Path $record 'fixture.env'
$passed = [System.Collections.Generic.List[string]]::new()
$original = @{}
$started = $false
$failure = $null
$auditFailure = $null
$runtimeCompleted = $false

function Invoke-Docker {
    param([string[]]$DockerArgs)
    # Arguments contain paths/config names only; never customer keys.
    [System.IO.File]::AppendAllText($log, ('docker ' + ($DockerArgs -join ' ') + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $code = -1
    $writer = [System.IO.StreamWriter]::new($log, $true, [System.Text.UTF8Encoding]::new($false))
    try {
        # Tee-Object writes UTF-16 under Windows PowerShell 5; use one encoding
        # throughout so a later agent can read the complete build/audit output.
        & docker @DockerArgs 2>&1 | ForEach-Object {
            $line = $_.ToString()
            $writer.WriteLine($line)
            Write-Host $line
        }
        $code = $LASTEXITCODE
    } finally {
        $writer.Dispose()
        $ErrorActionPreference = $previousPreference
    }
    if ($code -ne 0) { throw "Docker command failed with exit code $code. See $log" }
}

function Invoke-Compose {
    param([string[]]$DockerArgs)
    Invoke-Docker -DockerArgs (@('compose', '--project-name', $stack, '--env-file', $config,
                                 '-f', (Join-Path $project 'deploy/compose.yaml')) + $DockerArgs)
}

Push-Location -LiteralPath $project
try {
    Invoke-Docker -DockerArgs @('info', '--format', '{{.ServerVersion}}')
    $passed.Add('Docker engine reachable')

    # Choose a loopback port without touching an existing customer app on 8501.
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try { $listener.Start(); $port = $listener.LocalEndpoint.Port } finally { $listener.Stop() }
    $random = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($random) } finally { $generator.Dispose() }
    $master = [Convert]::ToBase64String($random).Replace('+','-').Replace('/','_')
    $settings = [ordered]@{
        BETA_ENV_FILE = $config.Replace('\','/')
        BETA_IMAGE = $image
        BETA_PORT = "$port"
        BETA_HOST = 'localhost'
        BETA_PRODUCTION = '0'
        SUPABASE_URL = 'https://docker-fixture.supabase.invalid'
        SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_docker_fixture'
        SUPABASE_SITE_URL = "http://localhost:$port"
        SUPABASE_ENABLE_SIGNUP = 'false'
        GOOGLE_API_KEY = 'docker-fixture-no-provider-calls'
        CUSTOMER_KEY_ENCRYPTION_KEY = $master
        CUSTOMER_DATA_ROOT = '/var/lib/verifier/customer_data'
        GEMINI_EMBEDDING_MODEL = 'gemini-embedding-001'
        GEMINI_EMBEDDING_DIMENSIONS = '768'
        BETA_BACKUP_ROOT = 'backups'
        BETA_BACKUP_KEY_ROOT = 'backup-secrets'
    }
    # These are generated test settings, not copies of the real .env/master key.
    $lines = foreach ($name in $settings.Keys) {
        $original[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
        "$name=$($settings[$name])"
    }
    [System.IO.File]::WriteAllLines($config, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))

    Invoke-Compose -DockerArgs @('config', '--quiet')
    Invoke-Docker -DockerArgs @('build', '--progress=plain', '--target', 'release', '-t', $image, '.')
    $passed.Add('Release image built; dependency imports/pip check/pinned model prediction passed')
    Invoke-Docker -DockerArgs @('image', 'inspect', '--format', '{{.Id}}', $image)
    try {
        # Refresh the advisory check even if the runtime layers are cached.
        Invoke-Docker -DockerArgs @('build', '--progress=plain', '--no-cache-filter', 'security-audit', '--target', 'security-audit', '-t', "$image-audit", '.')
        $passed.Add('Python dependency audit passed')
    } catch {
        $auditFailure = $_.Exception.Message
        Write-Warning 'The audit failed. Release remains blocked; continuing isolated fixture diagnostics only. The final result will remain failed.'
    }

    # Test the real native encryption dependency without generation/provider calls.
    $isolated = @('run', '--rm', '--network', 'none', '--read-only', '--user', '10001:10001',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                  '--tmpfs', '/tmp:rw,nosuid,size=268435456,mode=1777', '--entrypoint', 'python')
    Invoke-Docker -DockerArgs ($isolated + @($image, '-B', 'deploy/container_smoke.py'))
    $passed.Add('Customer imports, native encryption/PDF/image/vector operations and offline non-root reranker passed')
    Invoke-Docker -DockerArgs ($isolated + @($image, '-B', 'deploy/customer_ui_smoke.py'))
    $passed.Add('Real Streamlit Settings, navigation/drafts, PDF zoom, history, inline export, revision invalidation and revocation checks passed (fixtures; no browser/live auth)')
    $testMount = 'type=bind,source=' + (Join-Path $project 'tests') + ',target=/app/tests,readonly'
    foreach ($pattern in @('test_customer_connections.py', 'test_backup_restore.py', 'test_customer_beta.py',
                          'test_customer_access.py', 'test_customer_account_security.py', 'test_customer_account_auth.py',
                          'test_customer_acceptance.py', 'test_customer_ui_refinements.py', 'test_customer_mobile_layout.py',
                          'test_claim_parser.py', 'test_source_selection.py', 'test_visual_extraction.py', 'test_visual_pipeline.py', 'test_processing_diagnostics.py', 'test_supabase_storage.py')) {
        Invoke-Docker -DockerArgs ($isolated + @('--mount', $testMount, $image, '-B', '-m', 'unittest',
                                                'discover', '-s', 'tests', '-p', $pattern, '-v'))
    }
    $passed.Add('Native encryption, history/exports, account isolation/recovery and backup regressions passed inside image (auth/HTTP boundaries mocked)')

    $started = $true
    Invoke-Compose -DockerArgs @('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'web', 'worker')
    Invoke-Compose -DockerArgs @('ps')
    $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/_stcore/health" -TimeoutSec 10
    if ($health.StatusCode -ne 200) { throw 'Web health endpoint failed.' }
    $passed.Add('Web and single worker healthy under configured runtime restrictions')

    $seed = "import sys; sys.path.insert(0,'/app/app'); from pilot import store; from pathlib import Path; (store.root()/'docker-persistence.txt').write_text('fixture'); c=store.db(); conn=c.__enter__(); conn.execute('INSERT INTO workspaces VALUES(?,?)', ('a'*32,'Docker fixture')); c.__exit__(None,None,None); print('Fixture marker and workspace saved')"
    Invoke-Compose -DockerArgs @('exec', '-T', 'web', 'python', '-B', '-c', $seed)
    Invoke-Compose -DockerArgs @('up', '-d', '--force-recreate', '--no-build', '--wait', '--wait-timeout', '120', 'web', 'worker')
    $persist = "import sys; sys.path.insert(0,'/app/app'); from pilot import store; assert (store.root()/'docker-persistence.txt').read_text()=='fixture'; c=store.db(); conn=c.__enter__(); assert conn.execute('SELECT name FROM workspaces WHERE id=?', ('a'*32,)).fetchone()[0]=='Docker fixture'; c.__exit__(None,None,None); print('Data survived container recreation')"
    Invoke-Compose -DockerArgs @('exec', '-T', 'web', 'python', '-B', '-c', $persist)
    $passed.Add('Fixture file and SQLite state survived container recreation')

    Invoke-Compose -DockerArgs @('stop', 'web', 'worker')
    Invoke-Compose -DockerArgs @('run', '--rm', '--no-deps', 'backup', 'backup', '/backups/docker-smoke',
                               '--services-stopped', '--secret-output', '/backup-secrets/docker-smoke.key')
    Invoke-Compose -DockerArgs @('run', '--rm', '--no-deps', 'backup', 'verify', '/backups/docker-smoke')
    Invoke-Compose -DockerArgs @('run', '--rm', '--no-deps', 'backup', 'restore', '/backups/docker-smoke',
                               '/var/lib/verifier/restore-drill', '--customer-only')
    $restored = "from pathlib import Path; import sqlite3; p=Path('/var/lib/verifier/restore-drill/customer_data'); assert (p/'docker-persistence.txt').read_text()=='fixture'; c=sqlite3.connect(p/'control.sqlite3'); assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'; assert c.execute('SELECT name FROM workspaces WHERE id=?', ('a'*32,)).fetchone()[0]=='Docker fixture'; c.close(); print('Isolated container restore verified')"
    Invoke-Compose -DockerArgs @('run', '--rm', '--no-deps', '--entrypoint', 'python', 'backup', '-B', '-c', $restored)
    $passed.Add('Cold container backup, manifest verification and isolated restore passed')
    $runtimeCompleted = $true
} catch {
    $failure = $_.Exception.Message
    Write-Host "Docker check stopped: $failure" -ForegroundColor Red
} finally {
    if ($started) {
        try {
            Invoke-Compose -DockerArgs @('logs', '--no-color', '--tail', '80', 'web', 'worker')
            Invoke-Compose -DockerArgs @('stop', 'web', 'worker')
        } catch { Write-Warning 'Could not collect logs/stop the isolated smoke stack; inspect it in Docker Desktop.' }
    }
    foreach ($name in $original.Keys) {
        [Environment]::SetEnvironmentVariable($name, $original[$name], 'Process')
    }
    $result = [ordered]@{
        status = $(if ($null -eq $failure -and $null -eq $auditFailure) { 'passed' } else { 'failed' })
        image = $image
        compose_project = $stack
        passed = @($passed.ToArray())
        failure = $failure
        python_dependency_audit = $(if ($null -ne $auditFailure) { 'failed' } elseif ($passed.Contains('Python dependency audit passed')) { 'passed' } else { 'not run' })
        audit_failure = $auditFailure
        runtime_checks_completed = $runtimeCompleted
        real_customer_data_used = $false
        provider_calls = 0
        public_deployment = $false
        remaining = @('OS/base-image scan', 'Real configured customer app/browser acceptance', 'Host/HTTPS and scheduled off-host backup')
    }
    $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $record 'RESULT.json') -Encoding utf8
    Pop-Location
    Write-Host "Evidence saved to $record"
    Write-Host 'Isolated fixture volumes/images are retained for inspection. Real customer data was not mounted.'
}
if ($null -ne $failure) { throw $failure }
if ($null -ne $auditFailure) { throw "Fixture runtime checks completed, but the dependency audit failed. Release remains blocked. See $log" }
Write-Host 'Docker fixture checks passed. This does not publish or activate the real customer app.' -ForegroundColor Green
