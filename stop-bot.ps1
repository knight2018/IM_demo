$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$record = Get-Content -Raw -Encoding UTF8 'data/feishu-bot-process.json' | ConvertFrom-Json
$target = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)"
if (-not $target) { Write-Output '机器人已停止'; exit 0 }
if ($target.CommandLine -notmatch 'feishu_bot\.py') { throw '进程身份不匹配，停止操作已取消' }
if ($record.stop_file -notmatch '^stop-\d+-[a-f0-9]{32}\.request$') { throw '当前进程不支持平滑停止，请先检查是否存在正在生成的回复' }
# 信号文件位于项目内，目标进程会等待线程池清空后自行退出。
$requestFile = Join-Path (Join-Path $PSScriptRoot 'data') $record.stop_file
Set-Content -LiteralPath $requestFile -Value 'stop' -Encoding UTF8
$deadline = [DateTime]::UtcNow.AddMinutes(5)
while (Get-Process -Id $record.pid -ErrorAction SilentlyContinue) {
    if ([DateTime]::UtcNow -gt $deadline) { throw '仍有任务未结束，未强行终止机器人，请检查运行日志' }
    Start-Sleep -Milliseconds 500
}
Write-Output '已有回复处理完成，机器人已平滑停止'
