/*++

Module Name:

    MapleVhidApi.h

Abstract:

    User Mode 端的薄封裝，把 IOCTL 包成好用的函式。
    可以直接把 MapleVhidApi.c/.h 拉進其他專案使用。

--*/

#pragma once

#include <windows.h>
#include "..\MapleVhid\Public.h"

#ifdef __cplusplus
extern "C" {
#endif

//
// 開啟驅動程式。優先用符號連結 \\.\MapleVhid，
// 失敗時改用 SetupAPI 依 device interface GUID 列舉。
// 需要系統管理員權限。
//
HANDLE MapleVhidOpen(void);
void   MapleVhidClose(HANDLE Device);

// --- 鍵盤 ---
BOOL MapleKeyDown(HANDLE Device, UCHAR Usage);
BOOL MapleKeyUp(HANDLE Device, UCHAR Usage);
BOOL MapleKeyReset(HANDLE Device);
BOOL MapleKeyboardReport(HANDLE Device, UCHAR Modifiers, const UCHAR Keys[MAPLE_KEYBOARD_MAX_KEYS]);

// --- 滑鼠 ---
// 一次表達按下 / 放開 / 移動 / 滾輪；不需要的欄位填 0。
BOOL MapleMouseUpdate(HANDLE Device,
                      UCHAR ButtonsDown,
                      UCHAR ButtonsUp,
                      SHORT DeltaX,
                      SHORT DeltaY,
                      CHAR  Wheel,
                      CHAR  HWheel);

BOOL MapleMouseMove(HANDLE Device, SHORT DeltaX, SHORT DeltaY);
BOOL MapleMouseButtonDown(HANDLE Device, UCHAR Buttons);
BOOL MapleMouseButtonUp(HANDLE Device, UCHAR Buttons);
BOOL MapleMouseWheel(HANDLE Device, CHAR Vertical, CHAR Horizontal);
BOOL MapleMouseReset(HANDLE Device);

// --- 狀態 ---
BOOL MapleGetState(HANDLE Device, MAPLE_STATE* State);

//
// 把單一 ASCII 字元轉成 HID Usage + 需要的 Modifier。
// 回傳 FALSE 表示不支援該字元。
//
BOOL MapleAsciiToUsage(char Ch, UCHAR* Usage, UCHAR* Modifiers);

#ifdef __cplusplus
}
#endif
