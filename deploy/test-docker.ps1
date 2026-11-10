# Run in the operator's normal PowerShell or a GitHub-hosted Linux runner.
# Uses isolated fixture data and no model API credits.
param(
    [ValidateSet('scout', 'trivy')]
    [string]$ImageScanner = 'scout'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$record = Join-Path $project ".planning/reviews/2026-11-02-beta-launch/docker-$stamp"
New-Item -ItemType Directory -Path $record | Out-Null
if ($env:GITHUB_OUTPUT) {
    Add-Content -LiteralPath $env:GITHUB_OUTPUT -Value "record=$record" -Encoding utf8
}
$log = Join-Path $record 'docker.log'
$stack = "verifier-smoke-$($stamp.ToLowerInvariant())"
$image = "claim-verifier:smoke-$($stamp.ToLowerInvariant())"
$fixtureImage = "$image-local"
$config = Join-Path $record 'fixture.env'
$passed = [System.Collections.Generic.List[string]]::new()
$original = @{}
$started = $false
$failure = $null
$auditFailure = $null
$imageAuditFailure = $null
$inventoryCompleted = $false
$runtimeCompleted = $false

function Invoke-Docker {
    param([string[]]$DockerArgs, [string]$CapturePath)
    # Arguments contain paths/config names only; never customer keys.
    [System.IO.File]::AppendAllText($log, ('docker ' + ($DockerArgs -join ' ') + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $code = -1
    $writer = [System.IO.StreamWriter]::new($log, $true, [System.Text.UTF8Encoding]::new($false))
    $captureWriter = $null
    try {
        if ($CapturePath) {
            $captureWriter = [System.IO.StreamWriter]::new($CapturePath, $false, [System.Text.UTF8Encoding]::new($false))
        }
        # Tee-Object writes UTF-16 under Windows PowerShell 5; use one encoding
        # throughout so a later agent can read the complete build/audit output.
        & docker @DockerArgs 2>&1 | ForEach-Object {
            $line = $_.ToString()
            $writer.WriteLine($line)
            if ($null -ne $captureWriter) { $captureWriter.WriteLine($line) }
            Write-Host $line
        }
        $code = $LASTEXITCODE
    } finally {
        if ($null -ne $captureWriter) { $captureWriter.Dispose() }
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

function Invoke-ImageAudit {
    if ($ImageScanner -eq 'trivy') {
        # Public, version-pinned scanner: no Docker Hub account or customer keys.
        # The socket/report mounts are for the hosted Linux runner only.
        $scannerFailure = $null
        try {
            Invoke-Docker -DockerArgs @('run', '--rm',
                '--security-opt', 'no-new-privileges:true',
                '--mount', 'type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly',
                '--mount', ('type=bind,source=' + $record + ',target=/reports'),
                'ghcr.io/aquasecurity/trivy:0.76.2', 'image', '--image-src', 'docker',
                '--scanners', 'vuln', '--pkg-types', 'os,library', '--disable-telemetry',
                '--exit-code', '1', '--ignorefile', '/dev/null', '--format', 'json', '--list-all-pkgs',
                '--output', '/reports/image-audit.json', $image)
        } catch { $scannerFailure = $_ }
        if (Test-Path -LiteralPath (Join-Path $record 'image-audit.json')) {
            # Conversion still runs for a completed scan that found vulnerabilities.
            # Its success never clears the original scanner's failure status.
            Invoke-Docker -DockerArgs @('run', '--rm', '--network', 'none',
                '--security-opt', 'no-new-privileges:true',
                '--mount', ('type=bind,source=' + $record + ',target=/reports'),
                'ghcr.io/aquasecurity/trivy:0.76.2', 'convert', '--format', 'sarif',
                '--ignorefile', '/dev/null', '--output', '/reports/image-audit.sarif.json',
                '/reports/image-audit.json')
        } elseif ($null -eq $scannerFailure) {
            throw 'Trivy returned success without the required JSON report.'
        }
        if ($null -ne $scannerFailure) { throw $scannerFailure }
    } else {
        Invoke-Docker -DockerArgs @('scout', 'cves', '--exit-code', '--format', 'sarif',
            '--output', (Join-Path $record 'image-audit.sarif.json'), "local://$image")
    }
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
        BETA_IMAGE = $fixtureImage
        BETA_PORT = "$port"
        BETA_HOST = 'localhost'
        BETA_PRODUCTION = '0'
        CUSTOMER_STORAGE = 'local'
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
    Invoke-Docker -DockerArgs @('build', '--pull', '--progress=plain', '--target', 'release', '-t', $image, '.')
    $passed.Add('Cloud release image built without Chroma; dependency imports/pip check/pinned model prediction passed')
    Invoke-Docker -DockerArgs @('image', 'inspect', '--format', '{{.Id}}', $image)
    try {
        # Refresh the advisory check even if the runtime layers are cached.
        Invoke-Docker -DockerArgs @('build', '--progress=plain', '--no-cache-filter', 'security-audit', '--target', 'security-audit', '-t', "$image-audit", '.')
        $passed.Add('Python dependency audit passed')
    } catch {
        $auditFailure = $_.Exception.Message
        Write-Warning 'The audit failed. Release remains blocked; continuing isolated fixture diagnostics only. The final result will remain failed.'
    }

    try {
        Invoke-ImageAudit
        $passed.Add('Image and base-OS vulnerability scan passed')
    } catch {
        $imageAuditFailure = $_.Exception.Message
        Write-Warning 'Image/OS scan did not pass. Release remains blocked; continuing isolated fixture diagnostics only.'
    }

    # Test the real native encryption dependency without generation/provider calls.
    $isolated = @('run', '--rm', '--network', 'none', '--read-only', '--user', '10001:10001',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                  '--tmpfs', '/tmp:rw,nosuid,size=268435456,mode=1777', '--entrypoint', 'python')
    $inventoryPath = Join-Path $record 'runtime-inventory.json'
    Invoke-Docker -DockerArgs ($isolated + @($image, '-B', 'deploy/runtime_inventory.py')) -CapturePath $inventoryPath
    Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json | Out-Null
    $inventoryCompleted = $true
    $passed.Add('Read-only cloud runtime inventory collected; applicability not inferred')
    Invoke-Docker -DockerArgs ($isolated + @($image, '-B', 'deploy/container_smoke.py', 'cloud'))
    $passed.Add('Cloud image Chroma absence, native Postgres/encryption/PDF/PNG, scoped pgvector adapter (mocked DB) and offline non-root reranker passed')

    # Local persistence/migration drills intentionally use a separate Chroma image.
    # It is never the cloud release or the subject of its security gate.
    Invoke-Docker -DockerArgs @('build', '--progress=plain', '--target', 'local-release', '-t', $fixtureImage, '.')
    Invoke-Docker -DockerArgs @('image', 'inspect', '--format', '{{.Id}}', $fixtureImage)
    Invoke-Docker -DockerArgs ($isolated + @($fixtureImage, '-B', 'deploy/container_smoke.py', 'local'))
    $passed.Add('Separate local fixture image native Chroma and copied-index preservation passed')
    Invoke-Docker -DockerArgs ($isolated + @($fixtureImage, '-B', 'deploy/customer_ui_smoke.py'))
    $passed.Add('Real Streamlit Settings, navigation/drafts, PDF zoom, history, inline export, revision invalidation and revocation checks passed (fixtures; no browser/live auth)')
    $testMount = 'type=bind,source=' + (Join-Path $project 'tests') + ',target=/app/tests,readonly'
    foreach ($pattern in @('test_customer_connections.py', 'test_backup_restore.py', 'test_customer_beta.py',
                          'test_customer_access.py', 'test_customer_account_security.py', 'test_customer_account_auth.py',
                          'test_customer_acceptance.py', 'test_customer_ui_refinements.py', 'test_customer_mobile_layout.py',
                          'test_customer_selection_diagnostics.py',
                          'test_claim_parser.py', 'test_claim_pipeline.py', 'test_beta_information.py', 'test_beta_release.py', 'test_vercel_deployment.py', 'test_runtime_inventory.py', 'test_source_selection.py', 'test_visual_extraction.py', 'test_visual_pipeline.py', 'test_processing_diagnostics.py', 'test_supabase_storage.py')) {
        $mounts = @('--mount', $testMount)
        if ($pattern -eq 'test_vercel_deployment.py') {
            # Source contract files are inspection fixtures, not runtime assets.
            foreach ($name in @('Dockerfile', 'vercel.json', '.dockerignore', '.vercelignore')) {
                $mounts += @('--mount', ('type=bind,source=' + (Join-Path $project $name) + ',target=/app/' + $name + ',readonly'))
            }
        }
        Invoke-Docker -DockerArgs ($isolated + $mounts + @($fixtureImage, '-B', '-m', 'unittest',
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
        status = $(if ($null -eq $failure -and $null -eq $auditFailure -and $null -eq $imageAuditFailure) { 'passed' } else { 'failed' })
        image = $image
        local_fixture_image = $fixtureImage
        compose_project = $stack
        passed = @($passed.ToArray())
        failure = $failure
        python_dependency_audit = $(if ($null -ne $auditFailure) { 'failed' } elseif ($passed.Contains('Python dependency audit passed')) { 'passed' } else { 'not run' })
        audit_failure = $auditFailure
        image_security_audit = $(if ($null -ne $imageAuditFailure) { 'failed' } elseif ($passed.Contains('Image and base-OS vulnerability scan passed')) { 'passed' } else { 'not run' })
        image_security_scanner = $ImageScanner
        image_audit_failure = $imageAuditFailure
        runtime_inventory_completed = $inventoryCompleted
        runtime_checks_completed = $runtimeCompleted
        real_customer_data_used = $false
        provider_calls = 0
        public_deployment = $false
        remaining = @('Real configured cloud customer app/browser acceptance', 'Vercel activation and hosted reconnect/PDF/export checks', 'Scheduled cloud backup and isolated restore')
    }
    $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $record 'RESULT.json') -Encoding utf8
    Pop-Location
    Write-Host "Evidence saved to $record"
    Write-Host 'Isolated fixture volumes/images are retained for inspection. Real customer data was not mounted.'
}
if ($null -ne $failure) { throw $failure }
if ($null -ne $auditFailure) { throw "Fixture runtime checks completed, but the dependency audit failed. Release remains blocked. See $log" }
if ($null -ne $imageAuditFailure) { throw "Fixture runtime checks completed, but the image/OS scan did not pass. Release remains blocked. See $log" }
Write-Host 'Docker fixture checks passed. This does not publish or activate the real customer app.' -ForegroundColor Green
