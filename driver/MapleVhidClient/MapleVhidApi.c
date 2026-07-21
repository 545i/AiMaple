/*++

Module Name:

    MapleVhidApi.c

--*/

//
// 只有這個 .c 檔會真的產生 GUID_DEVINTERFACE_MAPLE_VHID 的實體。
//
#define INITGUID
#include "MapleVhidApi.h"
#include <setupapi.h>
#include <stdio.h>
#include <stdlib.h>

#pragma comment(lib, "setupapi.lib")

static HANDLE
MapleOpenByInterface(void)
{
    HDEVINFO                            devInfo;
    SP_DEVICE_INTERFACE_DATA            ifData;
    PSP_DEVICE_INTERFACE_DETAIL_DATA_W  detail = NULL;
    DWORD                               required = 0;
    HANDLE                              handle = INVALID_HANDLE_VALUE;

    devInfo = SetupDiGetClassDevsW(&GUID_DEVINTERFACE_MAPLE_VHID,
                                   NULL,
                                   NULL,
                                   DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (devInfo == INVALID_HANDLE_VALUE) {
        return INVALID_HANDLE_VALUE;
    }

    ZeroMemory(&ifData, sizeof(ifData));
    ifData.cbSize = sizeof(ifData);

    if (SetupDiEnumDeviceInterfaces(devInfo,
                                    NULL,
                                    &GUID_DEVINTERFACE_MAPLE_VHID,
                                    0,
                                    &ifData)) {

        SetupDiGetDeviceInterfaceDetailW(devInfo, &ifData, NULL, 0, &required, NULL);

        if (required > 0) {
            detail = (PSP_DEVICE_INTERFACE_DETAIL_DATA_W)malloc(required);
            if (detail != NULL) {
                detail->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W);

                if (SetupDiGetDeviceInterfaceDetailW(devInfo, &ifData, detail,
                                                     required, NULL, NULL)) {
                    handle = CreateFileW(detail->DevicePath,
                                         GENERIC_READ | GENERIC_WRITE,
                                         FILE_SHARE_READ | FILE_SHARE_WRITE,
                                         NULL,
                                         OPEN_EXISTING,
                                         0,
                                         NULL);
                }
                free(detail);
            }
        }
    }

    SetupDiDestroyDeviceInfoList(devInfo);
    return handle;
}

HANDLE
MapleVhidOpen(void)
{
    HANDLE handle;

    handle = CreateFileW(MAPLE_VHID_USER_PATH,
                         GENERIC_READ | GENERIC_WRITE,
                         FILE_SHARE_READ | FILE_SHARE_WRITE,
                         NULL,
                         OPEN_EXISTING,
                         0,
                         NULL);

    if (handle != INVALID_HANDLE_VALUE) {
        return handle;
    }

    return MapleOpenByInterface();
}

void
MapleVhidClose(HANDLE Device)
{
    if (Device != NULL && Device != INVALID_HANDLE_VALUE) {
        CloseHandle(Device);
    }
}

static BOOL
MapleIoctl(HANDLE Device, DWORD Code, void* In, DWORD InLen, void* Out, DWORD OutLen)
{
    DWORD returned = 0;

    return DeviceIoControl(Device, Code, In, InLen, Out, OutLen, &returned, NULL);
}

//
// ------------------------------ 鍵盤 ------------------------------
//

BOOL
MapleKeyDown(HANDLE Device, UCHAR Usage)
{
    MAPLE_KEY_EVENT ev = { Usage };
    return MapleIoctl(Device, IOCTL_MAPLE_VHID_KEY_DOWN, &ev, sizeof(ev), NULL, 0);
}

BOOL
MapleKeyUp(HANDLE Device, UCHAR Usage)
{
    MAPLE_KEY_EVENT ev = { Usage };
    return MapleIoctl(Device, IOCTL_MAPLE_VHID_KEY_UP, &ev, sizeof(ev), NULL, 0);
}

BOOL
MapleKeyReset(HANDLE Device)
{
    return MapleIoctl(Device, IOCTL_MAPLE_VHID_KEY_RESET, NULL, 0, NULL, 0);
}

BOOL
MapleKeyboardReport(HANDLE Device, UCHAR Modifiers, const UCHAR Keys[MAPLE_KEYBOARD_MAX_KEYS])
{
    MAPLE_KEYBOARD_REPORT report;

    ZeroMemory(&report, sizeof(report));
    report.ReportId  = MAPLE_REPORT_ID_KEYBOARD;
    report.Modifiers = Modifiers;
    if (Keys != NULL) {
        memcpy(report.Keys, Keys, MAPLE_KEYBOARD_MAX_KEYS);
    }

    return MapleIoctl(Device, IOCTL_MAPLE_VHID_KEYBOARD_REPORT,
                      &report, sizeof(report), NULL, 0);
}

//
// ------------------------------ 滑鼠 ------------------------------
//

BOOL
MapleMouseUpdate(HANDLE Device, UCHAR ButtonsDown, UCHAR ButtonsUp,
                 SHORT DeltaX, SHORT DeltaY, CHAR Wheel, CHAR HWheel)
{
    MAPLE_MOUSE_EVENT ev;

    ZeroMemory(&ev, sizeof(ev));
    ev.ButtonsDown = ButtonsDown;
    ev.ButtonsUp   = ButtonsUp;
    ev.DeltaX      = DeltaX;
    ev.DeltaY      = DeltaY;
    ev.Wheel       = Wheel;
    ev.HWheel      = HWheel;

    return MapleIoctl(Device, IOCTL_MAPLE_VHID_MOUSE_UPDATE, &ev, sizeof(ev), NULL, 0);
}

BOOL MapleMouseMove(HANDLE Device, SHORT DeltaX, SHORT DeltaY)
{
    return MapleMouseUpdate(Device, 0, 0, DeltaX, DeltaY, 0, 0);
}

BOOL MapleMouseButtonDown(HANDLE Device, UCHAR Buttons)
{
    return MapleMouseUpdate(Device, Buttons, 0, 0, 0, 0, 0);
}

BOOL MapleMouseButtonUp(HANDLE Device, UCHAR Buttons)
{
    return MapleMouseUpdate(Device, 0, Buttons, 0, 0, 0, 0);
}

BOOL MapleMouseWheel(HANDLE Device, CHAR Vertical, CHAR Horizontal)
{
    return MapleMouseUpdate(Device, 0, 0, 0, 0, Vertical, Horizontal);
}

BOOL MapleMouseReset(HANDLE Device)
{
    return MapleIoctl(Device, IOCTL_MAPLE_VHID_MOUSE_RESET, NULL, 0, NULL, 0);
}

BOOL
MapleGetState(HANDLE Device, MAPLE_STATE* State)
{
    return MapleIoctl(Device, IOCTL_MAPLE_VHID_GET_STATE,
                      NULL, 0, State, (DWORD)sizeof(MAPLE_STATE));
}

//
// -------------------------- ASCII 對映表 --------------------------
//

BOOL
MapleAsciiToUsage(char Ch, UCHAR* Usage, UCHAR* Modifiers)
{
    UCHAR usage = 0;
    UCHAR mods  = 0;

    if (Ch >= 'a' && Ch <= 'z') {
        usage = (UCHAR)(0x04 + (Ch - 'a'));
    } else if (Ch >= 'A' && Ch <= 'Z') {
        usage = (UCHAR)(0x04 + (Ch - 'A'));
        mods  = MAPLE_MOD_LEFT_SHIFT;
    } else if (Ch >= '1' && Ch <= '9') {
        usage = (UCHAR)(0x1E + (Ch - '1'));
    } else {
        switch (Ch) {
        case '0':  usage = 0x27; break;
        case '\n': usage = 0x28; break;   // Enter
        case '\b': usage = 0x2A; break;   // Backspace
        case '\t': usage = 0x2B; break;   // Tab
        case ' ':  usage = 0x2C; break;
        case '-':  usage = 0x2D; break;
        case '=':  usage = 0x2E; break;
        case '[':  usage = 0x2F; break;
        case ']':  usage = 0x30; break;
        case '\\': usage = 0x31; break;
        case ';':  usage = 0x33; break;
        case '\'': usage = 0x34; break;
        case '`':  usage = 0x35; break;
        case ',':  usage = 0x36; break;
        case '.':  usage = 0x37; break;
        case '/':  usage = 0x38; break;
        case '!':  usage = 0x1E; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '@':  usage = 0x1F; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '#':  usage = 0x20; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '$':  usage = 0x21; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '%':  usage = 0x22; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '^':  usage = 0x23; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '&':  usage = 0x24; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '*':  usage = 0x25; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '(':  usage = 0x26; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case ')':  usage = 0x27; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '_':  usage = 0x2D; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '+':  usage = 0x2E; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '{':  usage = 0x2F; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '}':  usage = 0x30; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '|':  usage = 0x31; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case ':':  usage = 0x33; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '"':  usage = 0x34; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '~':  usage = 0x35; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '<':  usage = 0x36; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '>':  usage = 0x37; mods = MAPLE_MOD_LEFT_SHIFT; break;
        case '?':  usage = 0x38; mods = MAPLE_MOD_LEFT_SHIFT; break;
        default:   return FALSE;
        }
    }

    if (Usage != NULL)     { *Usage = usage; }
    if (Modifiers != NULL) { *Modifiers = mods; }
    return TRUE;
}
