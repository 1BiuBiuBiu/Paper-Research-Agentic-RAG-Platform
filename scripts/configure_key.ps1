# Native-only credential entry. Do not echo, log, or pass credentials in argv.
[CmdletBinding()]
param([ValidatePattern('^(DEEPSEEK_API_KEY|TAVILY_API_KEY|RESEARCH_MODEL_KEY_[A-Z0-9_]{1,80})$')]
    [string]$Variable = 'DEEPSEEK_API_KEY')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$Root = Split-Path $PSScriptRoot -Parent
$Mutex = [Threading.Mutex]::new($false, 'Local\ResearchRAG-KeyEditor')
try { $Locked = $Mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $Locked = $true }
if (-not $Locked) {
    [Windows.Forms.MessageBox]::Show('已有密钥配置窗口，请先完成或关闭该窗口。', '密钥配置') | Out-Null
    $Mutex.Dispose()
    exit 0
}
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Saver = Join-Path $PSScriptRoot 'save_model_key.py'
$Form = New-Object Windows.Forms.Form
$Form.Text = '科研论文平台 · 本机 API 密钥'
$Form.ClientSize = New-Object Drawing.Size(540, 245)
$Form.StartPosition = 'CenterScreen'
$Form.FormBorderStyle = 'FixedDialog'
$Form.MaximizeBox = $false
$Form.TopMost = $true
$Label = New-Object Windows.Forms.Label
$Label.Location = New-Object Drawing.Point(18, 18)
$Label.Size = New-Object Drawing.Size(505, 55)
$Label.Text = "为 $Variable 输入 API Key。`r`n密钥仅保存到本机 .env，不进入网页、命令行参数或日志。"
$Secret = New-Object Windows.Forms.TextBox
$Secret.Location = New-Object Drawing.Point(18, 82)
$Secret.Size = New-Object Drawing.Size(500, 30)
$Secret.UseSystemPasswordChar = $true
$Secret.MaxLength = 4096
$Notice = New-Object Windows.Forms.Label
$Notice.Location = New-Object Drawing.Point(18, 122)
$Notice.Size = New-Object Drawing.Size(500, 45)
$Notice.Text = '保存后请停止并重新启动平台以生效。当前解析任务不会被此窗口中断。'
$Save = New-Object Windows.Forms.Button
$Save.Text = '保存密钥'
$Save.Location = New-Object Drawing.Point(305, 185)
$Save.Size = New-Object Drawing.Size(100, 35)
$Cancel = New-Object Windows.Forms.Button
$Cancel.Text = '取消'
$Cancel.Location = New-Object Drawing.Point(418, 185)
$Cancel.Size = New-Object Drawing.Size(100, 35)
$Cancel.Add_Click({ $Secret.Clear(); $Form.Close() })
$Save.Add_Click({
    if ([string]::IsNullOrWhiteSpace($Secret.Text)) {
        $Notice.Text = '请输入密钥；取消不会更改原配置。'
        return
    }
    $Save.Enabled = $false
    $Writer = New-Object Diagnostics.Process
    try {
        $Writer.StartInfo.FileName = $Python
        $Writer.StartInfo.Arguments = '"' + $Saver + '" --variable ' + $Variable
        $Writer.StartInfo.WorkingDirectory = $Root
        $Writer.StartInfo.UseShellExecute = $false
        $Writer.StartInfo.CreateNoWindow = $true
        $Writer.StartInfo.RedirectStandardInput = $true
        $Writer.StartInfo.RedirectStandardOutput = $true
        $Writer.StartInfo.RedirectStandardError = $true
        $Writer.Start() | Out-Null
        $Encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Secret.Text))
        $Writer.StandardInput.WriteLine($Encoded)
        $Writer.StandardInput.Close()
        $Encoded = $null
        $Secret.Clear()
        if (-not $Writer.WaitForExit(10000)) {
            $Writer.Kill()
            throw 'timeout'
        }
        if ($Writer.ExitCode -ne 0) { throw 'save failed' }
        [Windows.Forms.MessageBox]::Show(
            '密钥已保存。请在当前任务结束后停止并重新启动平台，然后刷新设置页。',
            '保存成功', [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        $Form.Close()
    } catch {
        $Secret.Clear()
        $Notice.Text = '保存失败，请检查本机 .venv 和 .env 文件权限后重新输入。'
    } finally {
        $Writer.Dispose()
        $Save.Enabled = $true
    }
})
$Form.Controls.AddRange(@($Label, $Secret, $Notice, $Save, $Cancel))
$Form.AcceptButton = $Save
$Form.CancelButton = $Cancel
$Form.Add_Shown({ $Secret.Focus() })
try { $Form.ShowDialog() | Out-Null } finally {
    $Secret.Clear(); $Form.Dispose(); $Mutex.ReleaseMutex(); $Mutex.Dispose()
}
