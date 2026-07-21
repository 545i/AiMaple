/*++

Module Name:

    ReportDescriptor.h

Abstract:

    HID Report Descriptor。
    單一 VHF 裝置上宣告兩個 Top Level Collection：

        Report ID 1 : Keyboard  (Generic Desktop / Keyboard)
        Report ID 2 : Mouse     (Generic Desktop / Mouse)

    HIDCLASS 會為每個 Top Level Collection 各建立一個 HID collection，
    Windows 也會分別把它們對映到 kbdhid / mouhid。

--*/

#pragma once

DECLSPEC_SELECTANY UCHAR g_MapleReportDescriptor[] =
{
    //
    // ================= Keyboard Collection (Report ID 1) =================
    //
    0x05, 0x01,                     // USAGE_PAGE (Generic Desktop)
    0x09, 0x06,                     // USAGE (Keyboard)
    0xA1, 0x01,                     // COLLECTION (Application)
    0x85, MAPLE_REPORT_ID_KEYBOARD, //   REPORT_ID (1)

    // --- Modifier keys：8 bits (Left Ctrl ~ Right GUI) ---
    0x05, 0x07,                     //   USAGE_PAGE (Keyboard/Keypad)
    0x19, 0xE0,                     //   USAGE_MINIMUM (Keyboard LeftControl)
    0x29, 0xE7,                     //   USAGE_MAXIMUM (Keyboard Right GUI)
    0x15, 0x00,                     //   LOGICAL_MINIMUM (0)
    0x25, 0x01,                     //   LOGICAL_MAXIMUM (1)
    0x75, 0x01,                     //   REPORT_SIZE (1)
    0x95, 0x08,                     //   REPORT_COUNT (8)
    0x81, 0x02,                     //   INPUT (Data,Var,Abs)

    // --- Reserved byte ---
    0x95, 0x01,                     //   REPORT_COUNT (1)
    0x75, 0x08,                     //   REPORT_SIZE (8)
    0x81, 0x03,                     //   INPUT (Cnst,Var,Abs)

    // --- LED output report：Num/Caps/Scroll/Compose/Kana ---
    0x95, 0x05,                     //   REPORT_COUNT (5)
    0x75, 0x01,                     //   REPORT_SIZE (1)
    0x05, 0x08,                     //   USAGE_PAGE (LEDs)
    0x19, 0x01,                     //   USAGE_MINIMUM (Num Lock)
    0x29, 0x05,                     //   USAGE_MAXIMUM (Kana)
    0x91, 0x02,                     //   OUTPUT (Data,Var,Abs)
    0x95, 0x01,                     //   REPORT_COUNT (1)
    0x75, 0x03,                     //   REPORT_SIZE (3)
    0x91, 0x03,                     //   OUTPUT (Cnst,Var,Abs)   ; padding

    // --- 6-key rollover array ---
    0x95, 0x06,                     //   REPORT_COUNT (6)
    0x75, 0x08,                     //   REPORT_SIZE (8)
    0x15, 0x00,                     //   LOGICAL_MINIMUM (0)
    0x26, 0xFF, 0x00,               //   LOGICAL_MAXIMUM (255)
    0x05, 0x07,                     //   USAGE_PAGE (Keyboard/Keypad)
    0x19, 0x00,                     //   USAGE_MINIMUM (0)
    0x2A, 0xFF, 0x00,               //   USAGE_MAXIMUM (255)
    0x81, 0x00,                     //   INPUT (Data,Ary,Abs)
    0xC0,                           // END_COLLECTION

    //
    // =================== Mouse Collection (Report ID 2) ===================
    //
    0x05, 0x01,                     // USAGE_PAGE (Generic Desktop)
    0x09, 0x02,                     // USAGE (Mouse)
    0xA1, 0x01,                     // COLLECTION (Application)
    0x85, MAPLE_REPORT_ID_MOUSE,    //   REPORT_ID (2)
    0x09, 0x01,                     //   USAGE (Pointer)
    0xA1, 0x00,                     //   COLLECTION (Physical)

    // --- 5 個按鍵 ---
    0x05, 0x09,                     //     USAGE_PAGE (Button)
    0x19, 0x01,                     //     USAGE_MINIMUM (Button 1)
    0x29, 0x05,                     //     USAGE_MAXIMUM (Button 5)
    0x15, 0x00,                     //     LOGICAL_MINIMUM (0)
    0x25, 0x01,                     //     LOGICAL_MAXIMUM (1)
    0x75, 0x01,                     //     REPORT_SIZE (1)
    0x95, 0x05,                     //     REPORT_COUNT (5)
    0x81, 0x02,                     //     INPUT (Data,Var,Abs)
    0x75, 0x03,                     //     REPORT_SIZE (3)
    0x95, 0x01,                     //     REPORT_COUNT (1)
    0x81, 0x03,                     //     INPUT (Cnst,Var,Abs)  ; padding

    // --- X / Y 相對位移，16-bit 提供高解析度 ---
    0x05, 0x01,                     //     USAGE_PAGE (Generic Desktop)
    0x09, 0x30,                     //     USAGE (X)
    0x09, 0x31,                     //     USAGE (Y)
    0x16, 0x01, 0x80,               //     LOGICAL_MINIMUM (-32767)
    0x26, 0xFF, 0x7F,               //     LOGICAL_MAXIMUM (32767)
    0x75, 0x10,                     //     REPORT_SIZE (16)
    0x95, 0x02,                     //     REPORT_COUNT (2)
    0x81, 0x06,                     //     INPUT (Data,Var,Rel)

    // --- 垂直滾輪 ---
    0x09, 0x38,                     //     USAGE (Wheel)
    0x15, 0x81,                     //     LOGICAL_MINIMUM (-127)
    0x25, 0x7F,                     //     LOGICAL_MAXIMUM (127)
    0x75, 0x08,                     //     REPORT_SIZE (8)
    0x95, 0x01,                     //     REPORT_COUNT (1)
    0x81, 0x06,                     //     INPUT (Data,Var,Rel)

    // --- 水平滾輪 (AC Pan) ---
    0x05, 0x0C,                     //     USAGE_PAGE (Consumer)
    0x0A, 0x38, 0x02,               //     USAGE (AC Pan)
    0x15, 0x81,                     //     LOGICAL_MINIMUM (-127)
    0x25, 0x7F,                     //     LOGICAL_MAXIMUM (127)
    0x75, 0x08,                     //     REPORT_SIZE (8)
    0x95, 0x01,                     //     REPORT_COUNT (1)
    0x81, 0x06,                     //     INPUT (Data,Var,Rel)

    0xC0,                           //   END_COLLECTION (Physical)
    0xC0                            // END_COLLECTION
};
