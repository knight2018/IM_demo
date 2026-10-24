param([string]$Proxy = 'http://127.0.0.1:7897')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# 代理仅作用于当前脚本及其 Codex 子进程，不修改 Windows 或用户全局设置。
$previousHttp = $env:HTTP_PROXY
$previousHttps = $env:HTTPS_PROXY
$previousNoProxy = $env:NO_PROXY
try {
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
    $env:NO_PROXY = '127.0.0.1,localhost,::1'
    # 本机连接专用；保持该进程运行，飞书服务才能继续生成回复。
    & codex app-server --listen ws://127.0.0.1:4500
    $serverExitCode = $LASTEXITCODE
} finally {
    $env:HTTP_PROXY = $previousHttp
    $env:HTTPS_PROXY = $previousHttps
    $env:NO_PROXY = $previousNoProxy
}
exit $serverExitCode
