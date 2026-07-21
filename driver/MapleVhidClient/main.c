/*++

Module Name:

    main.c

Abstract:

    MapleVhid 的 User Mode 測試 / 操作工具。
    需要以系統管理員身分執行。

--*/

#include "MapleVhidApi.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void
Usage(void)
{
    printf(
        "MapleVhidClient - Maple Virtual HID 控制工具 (需系統管理員權限)\n"
        "\n"
        "  key down <usage>        按下按鍵 (usage 為 HID Usage ID，可用 0x 前綴)\n"
        "  key up   <usage>        放開按鍵\n"
        "  key tap  <usage>        按下後立即放開\n"
        "  key reset               放開所有按鍵\n"
        "  type <文字>             逐字輸入一段 ASCII 文字\n"
        "\n"
        "  mouse move <dx> <dy>    相對移動\n"
        "  mouse down <l|r|m|x1|x2>\n"
        "  mouse up   <l|r|m|x1|x2>\n"
        "  mouse click <l|r|m|x1|x2>\n"
        "  mouse wheel <n>         垂直滾輪 (-127 ~ 127)\n"
        "  mouse hwheel <n>        水平滾輪\n"
        "  mouse reset             放開所有滑鼠鍵\n"
        "\n"
        "  state                   顯示目前狀態 (含主機端 LED)\n"
        "  demo                    跑一段展示動作\n"
        "\n"
        "常用 Usage: a=0x04 b=0x05 ... z=0x1D  1=0x1E ... 0=0x27\n"
        "            Enter=0x28 Esc=0x29 Space=0x2C Tab=0x2B\n"
        "            LeftCtrl=0xE0 LeftShift=0xE1 LeftAlt=0xE2 LeftGUI=0xE3\n");
}

static UCHAR
ParseButton(const char* text)
{
    if (_stricmp(text, "l") == 0 || _stricmp(text, "left") == 0)   return MAPLE_MOUSE_LEFT;
    if (_stricmp(text, "r") == 0 || _stricmp(text, "right") == 0)  return MAPLE_MOUSE_RIGHT;
    if (_stricmp(text, "m") == 0 || _stricmp(text, "middle") == 0) return MAPLE_MOUSE_MIDDLE;
    if (_stricmp(text, "x1") == 0)                                 return MAPLE_MOUSE_X1;
    if (_stricmp(text, "x2") == 0)                                 return MAPLE_MOUSE_X2;
    return 0;
}

static int
ParseNumber(const char* text)
{
    return (int)strtol(text, NULL, 0);   // 0 = 自動判斷 0x 十六進位
}

static void
TypeText(HANDLE device, const char* text)
{
    for (; *text != '\0'; text++) {
        UCHAR usage = 0, mods = 0;

        if (!MapleAsciiToUsage(*text, &usage, &mods)) {
            continue;
        }

        if (mods != 0) {
            MapleKeyDown(device, 0xE1);          // Left Shift
        }
        MapleKeyDown(device, usage);
        Sleep(8);
        MapleKeyUp(device, usage);
        if (mods != 0) {
            MapleKeyUp(device, 0xE1);
        }
        Sleep(8);
    }
}

static void
RunDemo(HANDLE device)
{
    int i;

    printf("3 秒後開始，請把游標移到記事本之類的視窗...\n");
    Sleep(3000);

    printf("[demo] 輸入文字\n");
    TypeText(device, "Hello from MapleVhid!\n");

    printf("[demo] 滑鼠畫方形\n");
    for (i = 0; i < 40; i++) { MapleMouseMove(device,  5,  0); Sleep(8); }
    for (i = 0; i < 40; i++) { MapleMouseMove(device,  0,  5); Sleep(8); }
    for (i = 0; i < 40; i++) { MapleMouseMove(device, -5,  0); Sleep(8); }
    for (i = 0; i < 40; i++) { MapleMouseMove(device,  0, -5); Sleep(8); }

    printf("[demo] 滾輪\n");
    for (i = 0; i < 5; i++) { MapleMouseWheel(device, -1, 0); Sleep(60); }
    for (i = 0; i < 5; i++) { MapleMouseWheel(device,  1, 0); Sleep(60); }

    printf("[demo] 完成\n");
}

static void
PrintState(HANDLE device)
{
    MAPLE_STATE state;
    int i;

    if (!MapleGetState(device, &state)) {
        printf("讀取狀態失敗，GetLastError=%lu\n", GetLastError());
        return;
    }

    printf("Modifiers    : 0x%02X\n", state.Modifiers);
    printf("Keys         :");
    for (i = 0; i < MAPLE_KEYBOARD_MAX_KEYS; i++) {
        printf(" 0x%02X", state.Keys[i]);
    }
    printf("\n");
    printf("MouseButtons : 0x%02X\n", state.MouseButtons);
    printf("LEDs         : 0x%02X (Num=%d Caps=%d Scroll=%d)\n",
           state.Leds,
           (state.Leds & MAPLE_LED_NUM_LOCK)    ? 1 : 0,
           (state.Leds & MAPLE_LED_CAPS_LOCK)   ? 1 : 0,
           (state.Leds & MAPLE_LED_SCROLL_LOCK) ? 1 : 0);
}

int
main(int argc, char** argv)
{
    HANDLE device;
    int    result = 0;

    if (argc < 2) {
        Usage();
        return 1;
    }

    device = MapleVhidOpen();
    if (device == INVALID_HANDLE_VALUE) {
        printf("無法開啟 MapleVhid，GetLastError=%lu\n", GetLastError());
        printf("請確認驅動程式已安裝，且本程式以系統管理員身分執行。\n");
        return 2;
    }

    if (_stricmp(argv[1], "key") == 0 && argc >= 3) {

        if (_stricmp(argv[2], "reset") == 0) {
            MapleKeyReset(device);
        } else if (argc >= 4) {
            UCHAR usage = (UCHAR)ParseNumber(argv[3]);

            if (_stricmp(argv[2], "down") == 0) {
                MapleKeyDown(device, usage);
            } else if (_stricmp(argv[2], "up") == 0) {
                MapleKeyUp(device, usage);
            } else if (_stricmp(argv[2], "tap") == 0) {
                MapleKeyDown(device, usage);
                Sleep(20);
                MapleKeyUp(device, usage);
            } else {
                Usage();
                result = 1;
            }
        } else {
            Usage();
            result = 1;
        }

    } else if (_stricmp(argv[1], "type") == 0 && argc >= 3) {

        TypeText(device, argv[2]);

    } else if (_stricmp(argv[1], "mouse") == 0 && argc >= 3) {

        if (_stricmp(argv[2], "move") == 0 && argc >= 5) {
            MapleMouseMove(device, (SHORT)ParseNumber(argv[3]), (SHORT)ParseNumber(argv[4]));
        } else if (_stricmp(argv[2], "down") == 0 && argc >= 4) {
            MapleMouseButtonDown(device, ParseButton(argv[3]));
        } else if (_stricmp(argv[2], "up") == 0 && argc >= 4) {
            MapleMouseButtonUp(device, ParseButton(argv[3]));
        } else if (_stricmp(argv[2], "click") == 0 && argc >= 4) {
            UCHAR button = ParseButton(argv[3]);
            MapleMouseButtonDown(device, button);
            Sleep(20);
            MapleMouseButtonUp(device, button);
        } else if (_stricmp(argv[2], "wheel") == 0 && argc >= 4) {
            MapleMouseWheel(device, (CHAR)ParseNumber(argv[3]), 0);
        } else if (_stricmp(argv[2], "hwheel") == 0 && argc >= 4) {
            MapleMouseWheel(device, 0, (CHAR)ParseNumber(argv[3]));
        } else if (_stricmp(argv[2], "reset") == 0) {
            MapleMouseReset(device);
        } else {
            Usage();
            result = 1;
        }

    } else if (_stricmp(argv[1], "state") == 0) {

        PrintState(device);

    } else if (_stricmp(argv[1], "demo") == 0) {

        RunDemo(device);

    } else {
        Usage();
        result = 1;
    }

    MapleVhidClose(device);
    return result;
}
