# 一键提交 + 推送：本地改完数据（或手动加了段子）后执行
# 用法：powershell -ExecutionPolicy Bypass -File .\update.ps1
#       powershell -ExecutionPolicy Bypass -File .\update.ps1 -Message "新增两条段子"
param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# 1) 基本环境检查
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 git，请先安装 Git for Windows。" -ForegroundColor Red
    exit 1
}
$remotes = @(git remote)
if (-not ($remotes -contains "origin")) {
    Write-Host "还没有配置远程仓库 origin。请先按 README 的「部署到 GitHub Pages」建仓并 push 一次。" -ForegroundColor Yellow
    exit 1
}
if (-not (git config user.name) -or -not (git config user.email)) {
    Write-Host "还没有设置提交身份，请先执行：" -ForegroundColor Yellow
    Write-Host '  git config --global user.name "你的名字"'
    Write-Host '  git config --global user.email "你的邮箱"'
    exit 1
}

# 2) 提交改动（提交信息写入 UTF-8 无 BOM 文件，避免中文在 Windows 下乱码）
if (-not $Message) {
    $Message = "data: 更新资讯数据 $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}
git add -A
$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "没有需要提交的改动。" -ForegroundColor Green
    exit 0
}
Write-Host "将要提交："
$staged | ForEach-Object { Write-Host "  $_" }

$msgFile = Join-Path $env:TEMP "robot-news-commit-msg.txt"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($msgFile, $Message, $utf8NoBom)
try {
    git commit -F $msgFile
} finally {
    Remove-Item $msgFile -Force -ErrorAction SilentlyContinue
}

# 3) 先同步云端（定时 workflow 可能已提交过数据），再推送
git pull --rebase --autostash origin main
git push -u origin main

Write-Host "已推送，GitHub Actions 会自动重新部署（约 1 分钟生效）。" -ForegroundColor Green
