@echo off
REM MapleVhid 移除腳本 — 請以系統管理員身分執行
REM 先列出目前狀態，再由你確認要刪哪一份 oemXX.inf。

echo === 目前安裝的 MapleVhid 驅動封裝 ===
pnputil /enum-drivers | findstr /i /c:"MapleVhid" /c:"Published Name"

echo.
echo === 目前的 MapleVhid 裝置 ===
pnputil /enum-devices ^| findstr /i "MapleVhid"

echo.
echo 移除步驟：
echo   1. pnputil /remove-device "ROOT\SYSTEM\000X"     ^(上面列出的 Instance ID^)
echo   2. pnputil /delete-driver oemXX.inf /uninstall /force
echo.
echo 刻意不自動刪除，避免誤刪到其他驅動封裝。
