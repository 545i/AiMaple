@echo off
REM MapleVhid 安裝腳本 — 請以系統管理員身分執行
REM 前提：已開啟測試簽章 (bcdedit /set testsigning on) 並重開機

setlocal
set "PKG=%~dp0x64\Debug\MapleVhid"

if not exist "%PKG%\MapleVhid.inf" (
    set "PKG=%~dp0x64\Release\MapleVhid"
)

if not exist "%PKG%\MapleVhid.inf" (
    echo [錯誤] 找不到驅動封裝，請先在 Visual Studio 建置 MapleVhid 專案。
    exit /b 1
)

echo 使用封裝：%PKG%
pnputil /add-driver "%PKG%\MapleVhid.inf" /install
if errorlevel 1 (
    echo [錯誤] pnputil /add-driver 失敗。
    exit /b 1
)

echo 建立 root\MapleVhid 裝置節點...
pnputil /add-device root\MapleVhid 2>nul
if errorlevel 1 (
    echo [提示] pnputil /add-device 不支援或已存在；
    echo        若裝置未出現，請改用 WDK 附的 devcon：
    echo            devcon install "%PKG%\MapleVhid.inf" root\MapleVhid
)

echo 完成。可在裝置管理員的「系統裝置」下看到 Maple Virtual HID Device。
endlocal
