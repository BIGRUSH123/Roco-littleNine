# native/tools/remote/login.ps1 — 起一个带调试端口的真实 Chrome，用于登录阿里云 DSW
#
# 为什么不用 gstack 自动化浏览器：它的可见窗口（browse handoff / --headed）在本机会崩
# （实测两次），而真实 Chrome + 独立 profile 稳定，且登录态持久保存在 profile 里。
#
# 用法:  pwsh -NoProfile -File native/tools/remote/login.ps1 [实例URL]
$ErrorActionPreference = 'Continue'

$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { $chrome = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" }
$profileDir = Join-Path $env:TEMP "dsw-cdp-profile"
$url = if ($args.Count -gt 0) { $args[0] } else { "https://account.aliyun.com/login/login.htm" }

Write-Output "chrome   : $chrome"
Write-Output "profile  : $profileDir"
Write-Output "url      : $url"

Start-Process -FilePath $chrome -ArgumentList @(
  "--user-data-dir=$profileDir",
  "--remote-debugging-port=9222",
  "--remote-allow-origins=*",
  "--no-first-run",
  "--no-default-browser-check",
  "--disable-popup-blocking",
  $url
)

for ($i = 1; $i -le 20; $i++) {
    Start-Sleep -Seconds 2
    try {
        $v = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 3
        Write-Output "CDP READY: $($v.Browser)"
        break
    } catch {
        Write-Output ("[{0}s] 等 CDP ..." -f ($i * 2))
    }
}

Write-Output ""
Write-Output "请在打开的窗口里登录阿里云（扫码/账号密码），然后："
Write-Output "  node native/tools/remote/cdp.mjs cookies native/tools/remote/cookies.json aliyun.com"
Write-Output "  env\python.exe -X utf8 native/tools/remote/dsw.py ping"
