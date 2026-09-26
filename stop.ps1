# 关闭 小虫管理器（源码模式）与已安装版本。
$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
  Where-Object { $_.CommandLine -and $_.CommandLine -like '*server.py*' -and $_.CommandLine -like '*小虫*' }
if (-not $procs) { Write-Host '源码模式没在运行。' } else {
  foreach ($p in $procs) { Write-Host ('Stopping PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}
$app = Get-Process -Name '小虫管理器' -ErrorAction SilentlyContinue
if ($app) { $app | Stop-Process -Force; Write-Host '已关闭已安装版本。' }
Write-Host 'Done.'
