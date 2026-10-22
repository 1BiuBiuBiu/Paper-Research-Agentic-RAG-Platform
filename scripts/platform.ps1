# Windows PowerShell 5.1; no installation, model calls or credential output.
[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop')][string]$Action = 'Start',
    [ValidateRange(1024, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1024, 65535)][int]$UiPort = 8501,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$RunDir = Join-Path $ProjectRoot "output\launcher\$ApiPort-$UiPort"
$StateFile = Join-Path $RunDir 'services.json'
$ApiUrl = "http://127.0.0.1:$ApiPort"
$UiUrl = "http://127.0.0.1:$UiPort"
$Created = [Collections.Generic.List[object]]::new()
$Saved = @()
$Locked = $false
$ExitCode = 0
$Digest = [Security.Cryptography.SHA256]::Create()
$RootHash = [BitConverter]::ToString($Digest.ComputeHash(
    [Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant())
)).Replace('-', '').Substring(0, 16)
$Digest.Dispose()
$Mutex = [Threading.Mutex]::new($false, "Local\ResearchRAG-$RootHash-$ApiPort-$UiPort")

function Read-LocalHttp([string]$Url) {
    $Response = $null
    try {
        $Request = [Net.WebRequest]::Create($Url)
        $Request.Proxy = $null
        $Request.Timeout = 1200
        $Response = $Request.GetResponse()
        $Reader = [IO.StreamReader]::new($Response.GetResponseStream())
        try { return $Reader.ReadToEnd() } finally { $Reader.Dispose() }
    } catch { return $null } finally {
        if ($null -ne $Response) { $Response.Dispose() }
    }
}

function Test-Ready([string]$Kind) {
    if ($Kind -eq 'api') {
        try {
            $Health = Read-LocalHttp "$ApiUrl/health" | ConvertFrom-Json
            return ($Health.status -eq 'ok' -and $Health.parser -eq 'docling')
        } catch { return $false }
    }
    return ((Read-LocalHttp "$UiUrl/_stcore/health") -eq 'ok')
}

function Get-Listener([int]$Port) {
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Test-OurListener([int]$ProcessId, [string]$Kind) {
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    $Pattern = if ($Kind -eq 'api') { '-m uvicorn app\.api:app' } else {
        '-m streamlit run (?:app[/\\]main\.py|"[^"]*app[/\\]main\.py")'
    }
    if ($null -eq $Process -or $Process.CommandLine -notmatch $Pattern) { return $false }
    # venv python.exe may launch the base interpreter as its child on Windows.
    for ($Depth = 0; $Depth -lt 4 -and $null -ne $Process; $Depth++) {
        if ($Process.ExecutablePath -eq $PythonPath) { return $true }
        $ParentId = $Process.ParentProcessId
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ParentId"
    }
    return $false
}

function Test-Ticket($Ticket) {
    $Process = Get-Process -Id $Ticket.process_id -ErrorAction SilentlyContinue
    if ($null -eq $Process) { return $false }
    try {
        $SameTime = $Process.StartTime.ToUniversalTime().ToString('o') -eq $Ticket.started_utc
        return ($SameTime -and (Test-OurListener $Process.Id $Ticket.kind))
    } catch { return $false }
}

function Stop-Tree([int]$ProcessId) {
    $Children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId")
    foreach ($Child in $Children) { Stop-Tree $Child.ProcessId }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Save-Tickets($Tickets) {
    $Json = ConvertTo-Json -InputObject @($Tickets) -Depth 4
    [IO.File]::WriteAllText("$StateFile.tmp", $Json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath "$StateFile.tmp" -Destination $StateFile -Force
}

function Start-ServiceProcess([string]$Kind, [int]$Port, [string]$Arguments) {
    $Listeners = @(Get-Listener $Port)
    if ($Listeners.Count -gt 0) {
        foreach ($Listener in $Listeners) {
            if (-not (Test-OurListener $Listener.OwningProcess $Kind)) {
                throw "端口 $Port 被其他程序占用；请先检查端口。启动器不会关闭该程序。"
            }
        }
        if (Test-Ready $Kind) {
            Write-Host "已复用运行中的 $Kind 服务（$Port）。"
            return
        }
        throw "本项目 $Kind 进程占用 $Port，但健康检查失败。请检查原启动窗口或日志后重试。"
    }
    $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $Process = Start-Process -FilePath $PythonPath -ArgumentList $Arguments `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $RunDir "$Stamp-$Kind.out.log") `
        -RedirectStandardError (Join-Path $RunDir "$Stamp-$Kind.err.log")
    $Ticket = [pscustomobject]@{
        kind = $Kind; process_id = $Process.Id
        started_utc = $Process.StartTime.ToUniversalTime().ToString('o')
    }
    $Created.Add($Ticket)
    Save-Tickets (@($Saved) + @($Created.ToArray()))
    $Deadline = (Get-Date).AddSeconds(60)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { throw "$Kind 服务提前退出，请查看日志：$RunDir" }
        if (Test-Ready $Kind) {
            $Active = @(Get-Listener $Port)
            if ($Active.Count -eq 0) { throw "$Kind 服务端口尚未监听。" }
            foreach ($Listener in $Active) {
                if (-not (Test-OurListener $Listener.OwningProcess $Kind)) {
                    throw "端口 $Port 启动期间被其他程序占用。"
                }
            }
            Write-Host "$Kind 服务已就绪（$Port）。"
            return
        }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $Deadline)
    throw "$Kind 服务启动超时，请查看日志：$RunDir"
}

try {
    if ($ApiPort -eq $UiPort) { throw '后端和页面不能使用同一个端口。' }
    try { $Locked = $Mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] {
        $Locked = $true
    }
    if (-not $Locked) { throw '另一个启动或停止操作正在进行，请稍后重试。' }
    New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
    if (Test-Path -LiteralPath $StateFile) {
        # PowerShell 5.1 emits a JSON array as ONE pipeline item; enumerate explicitly.
        $Loaded = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $Saved = @(foreach ($Ticket in $Loaded) {
            if (Test-Ticket $Ticket) { $Ticket }
        })
    }
    if ($Action -eq 'Stop') {
        foreach ($Ticket in $Saved) {
            # Revalidate creation time and project identity before terminating a tree.
            if (Test-Ticket $Ticket) { Stop-Tree $Ticket.process_id }
        }
        Save-Tickets @()
        Write-Host '已停止由此启动器创建的服务；原先手动运行的服务请在其窗口按 Ctrl+C。'
    } else {
        if (-not (Test-Path -LiteralPath $PythonPath)) {
            throw '缺少本机 .venv 环境。请在项目目录安装 uv 并执行 uv sync --locked，详见 README。'
        }
        $PreviousApi = $env:API_BASE
        try {
            $env:API_BASE = $ApiUrl
            Start-ServiceProcess 'api' $ApiPort "-m uvicorn app.api:app --host 127.0.0.1 --port $ApiPort"
            Start-ServiceProcess 'ui' $UiPort (
                "-m streamlit run app/main.py --server.address 127.0.0.1 --server.port $UiPort " +
                '--server.headless true --browser.gatherUsageStats false'
            )
        } finally { $env:API_BASE = $PreviousApi }
        Write-Host "平台已启动：$UiUrl"
        Write-Host '可以关闭此窗口；结束使用时双击“停止平台.cmd”。'
        Write-Host "运行日志：$RunDir"
        if (-not $NoBrowser) {
            try { Start-Process $UiUrl } catch {
                Write-Host "浏览器未自动打开，请手动访问：$UiUrl" -ForegroundColor Yellow
            }
        }
    }
} catch {
    foreach ($Ticket in $Created) {
        if (Test-Ticket $Ticket) { Stop-Tree $Ticket.process_id }
    }
    if ($Locked -and (Test-Path -LiteralPath $RunDir)) { Save-Tickets $Saved }
    Write-Host "操作失败：$($_.Exception.Message)" -ForegroundColor Red
    $ExitCode = 1
} finally {
    if ($Locked) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
exit $ExitCode
