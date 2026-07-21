/*++

Module Name:

    Public.h

Abstract:

    Kernel / User Mode 共用的介面定義。
    User Mode Client 透過 IOCTL 將 HID Report 送進驅動程式，
    驅動程式再以 VhfReadReportSubmit() 提交給 HID class。

--*/

#pragma once

#ifdef _KERNEL_MODE
#include <ntddk.h>
#else
#include <windows.h>
#include <winioctl.h>
#endif

//
// Device Interface GUID
// 使用前請在「其中一個」.c 檔 #include <initguid.h> 再 include 本檔。
// {9B4C6F2A-3D17-4E58-9A21-0C7F5BE38411}
//
DEFINE_GUID(GUID_DEVINTERFACE_MAPLE_VHID,
    0x9b4c6f2a, 0x3d17, 0x4e58, 0x9a, 0x21, 0x0c, 0x7f, 0x5b, 0xe3, 0x84, 0x11);

//
// 除了 Device Interface 之外，驅動程式也會建立固定的符號連結，
// 讓腳本語言 (例如 Python ctypes) 可以直接 CreateFile 開啟。
//
#define MAPLE_VHID_SYMBOLIC_LINK_NAME   L"\\DosDevices\\MapleVhid"
#define MAPLE_VHID_USER_PATH            L"\\\\.\\MapleVhid"

//
// Report ID：一個 VHF 實體上掛兩個 Top Level Collection
//
#define MAPLE_REPORT_ID_KEYBOARD    0x01
#define MAPLE_REPORT_ID_MOUSE       0x02

//
// 鍵盤同時最多按住 6 鍵 (Boot Keyboard 相容)
//
#define MAPLE_KEYBOARD_MAX_KEYS     6

//
// Modifier bit mask (HID Usage 0xE0 ~ 0xE7)
//
#define MAPLE_MOD_LEFT_CTRL     0x01
#define MAPLE_MOD_LEFT_SHIFT    0x02
#define MAPLE_MOD_LEFT_ALT      0x04
#define MAPLE_MOD_LEFT_GUI      0x08
#define MAPLE_MOD_RIGHT_CTRL    0x10
#define MAPLE_MOD_RIGHT_SHIFT   0x20
#define MAPLE_MOD_RIGHT_ALT     0x40
#define MAPLE_MOD_RIGHT_GUI     0x80

//
// Mouse button bit mask
//
#define MAPLE_MOUSE_LEFT        0x01
#define MAPLE_MOUSE_RIGHT       0x02
#define MAPLE_MOUSE_MIDDLE      0x04
#define MAPLE_MOUSE_X1          0x08
#define MAPLE_MOUSE_X2          0x10

//
// 鍵盤 LED bit mask (由驅動回報給 User Mode)
//
#define MAPLE_LED_NUM_LOCK      0x01
#define MAPLE_LED_CAPS_LOCK     0x02
#define MAPLE_LED_SCROLL_LOCK   0x04
#define MAPLE_LED_COMPOSE       0x08
#define MAPLE_LED_KANA          0x10

#pragma pack(push, 1)

//
// 實際送上 HID bus 的 Keyboard Input Report (9 bytes)
//
typedef struct _MAPLE_KEYBOARD_REPORT
{
    UCHAR ReportId;                             // = MAPLE_REPORT_ID_KEYBOARD
    UCHAR Modifiers;                            // MAPLE_MOD_*
    UCHAR Reserved;                             // 固定 0
    UCHAR Keys[MAPLE_KEYBOARD_MAX_KEYS];        // HID Usage Page 0x07 的 Usage ID
} MAPLE_KEYBOARD_REPORT, *PMAPLE_KEYBOARD_REPORT;

//
// 實際送上 HID bus 的 Mouse Input Report (8 bytes)
//
typedef struct _MAPLE_MOUSE_REPORT
{
    UCHAR ReportId;                             // = MAPLE_REPORT_ID_MOUSE
    UCHAR Buttons;                              // MAPLE_MOUSE_*
    SHORT DeltaX;                               // 相對位移 -32767 ~ 32767
    SHORT DeltaY;
    CHAR  Wheel;                                // 垂直滾輪 -127 ~ 127
    CHAR  HWheel;                               // 水平滾輪 (AC Pan)
} MAPLE_MOUSE_REPORT, *PMAPLE_MOUSE_REPORT;

//
// IOCTL_MAPLE_VHID_KEY_DOWN / KEY_UP 的輸入
// Usage 為 HID Keyboard Usage ID。
// 0xE0~0xE7 會自動被轉成 Modifier bit，其餘放進 Keys[] 陣列。
//
typedef struct _MAPLE_KEY_EVENT
{
    UCHAR Usage;
} MAPLE_KEY_EVENT, *PMAPLE_KEY_EVENT;

//
// IOCTL_MAPLE_VHID_MOUSE_UPDATE 的輸入。
// 一次可同時表達「按下 / 放開 / 移動 / 滾輪」，適合遠端串流合併事件。
// 驅動內部維護按鍵狀態：State |= ButtonsDown; State &= ~ButtonsUp;
// 若要絕對設定，把 ButtonsUp 設成 0xFF 再用 ButtonsDown 指定即可。
//
typedef struct _MAPLE_MOUSE_EVENT
{
    UCHAR ButtonsDown;
    UCHAR ButtonsUp;
    SHORT DeltaX;
    SHORT DeltaY;
    CHAR  Wheel;
    CHAR  HWheel;
} MAPLE_MOUSE_EVENT, *PMAPLE_MOUSE_EVENT;

//
// IOCTL_MAPLE_VHID_GET_STATE 的輸出
//
typedef struct _MAPLE_STATE
{
    UCHAR Modifiers;
    UCHAR Keys[MAPLE_KEYBOARD_MAX_KEYS];
    UCHAR MouseButtons;
    UCHAR Leds;                                 // 主機端 Caps/Num/Scroll Lock 狀態
} MAPLE_STATE, *PMAPLE_STATE;

#pragma pack(pop)

//
// IOCTL 定義
// METHOD_BUFFERED + FILE_WRITE_ACCESS：需要有寫入權限才能開啟，
// 一般使用者程序無法注入按鍵。
//
#define MAPLE_VHID_IOCTL(_index_) \
    CTL_CODE(FILE_DEVICE_UNKNOWN, (_index_), METHOD_BUFFERED, FILE_WRITE_ACCESS)

// --- 鍵盤 ---
#define IOCTL_MAPLE_VHID_KEY_DOWN           MAPLE_VHID_IOCTL(0x900)  // in: MAPLE_KEY_EVENT
#define IOCTL_MAPLE_VHID_KEY_UP             MAPLE_VHID_IOCTL(0x901)  // in: MAPLE_KEY_EVENT
#define IOCTL_MAPLE_VHID_KEY_RESET          MAPLE_VHID_IOCTL(0x902)  // 全部放開
#define IOCTL_MAPLE_VHID_KEYBOARD_REPORT    MAPLE_VHID_IOCTL(0x903)  // in: MAPLE_KEYBOARD_REPORT (raw)

// --- 滑鼠 ---
#define IOCTL_MAPLE_VHID_MOUSE_UPDATE       MAPLE_VHID_IOCTL(0x910)  // in: MAPLE_MOUSE_EVENT
#define IOCTL_MAPLE_VHID_MOUSE_RESET        MAPLE_VHID_IOCTL(0x911)  // 全部放開
#define IOCTL_MAPLE_VHID_MOUSE_REPORT       MAPLE_VHID_IOCTL(0x912)  // in: MAPLE_MOUSE_REPORT (raw)

// --- 狀態 ---
#define IOCTL_MAPLE_VHID_GET_STATE          MAPLE_VHID_IOCTL(0x920)  // out: MAPLE_STATE
