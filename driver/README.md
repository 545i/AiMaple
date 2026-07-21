# MapleVhid — Windows 11 KMDF Virtual HID Driver

用 Microsoft **Virtual HID Framework (VHF)** 實作的虛擬 HID 裝置，
在系統中生出一支**虛擬鍵盤**與一支**虛擬滑鼠**。
User Mode 透過 IOCTL 送事件，驅動組成 HID Input Report 後以 `VhfReadReportSubmit()` 提交。

因為事件是從 HID class driver 這一層進入系統，對上層應用程式而言與實體鍵盤滑鼠沒有差別
（不使用 SendInput / Interception / ViGEm）。

---

## 目錄結構

```
driver/
├─ MapleVhid.sln
├─ MapleVhid/                   ← KMDF 驅動程式
│  ├─ MapleVhid.vcxproj
│  ├─ MapleVhid.inf             ← INF 安裝檔 (root-enumerated)
│  ├─ Public.h                  ← Kernel/User 共用的 IOCTL 與結構定義
│  ├─ ReportDescriptor.h        ← HID Report Descriptor (兩個 Top Level Collection)
│  ├─ Driver.h / Driver.c       ← DriverEntry、EvtDeviceAdd
│  ├─ Device.c                  ← PnP / Power callback
│  ├─ Vhf.c                     ← VhfCreate / VhfStart / Report 提交 / LED 回寫
│  └─ Queue.c                   ← IOCTL dispatch 與鍵盤滑鼠狀態機
├─ MapleVhidClient/             ← User Mode Client
│  ├─ MapleVhidApi.h / .c       ← 可重用的薄封裝
│  └─ main.c                    ← CLI 測試工具
├─ maple_vhid.py                ← Python (ctypes) 綁定，給 maple 的 FastAPI 用
├─ install.bat / uninstall.bat
└─ README.md
```

---

## 1. 環境需求

| 項目 | 版本 |
|---|---|
| OS | Windows 11 x64 |
| IDE | Visual Studio 2022（含「使用 C++ 的桌面開發」工作負載） |
| SDK | Windows 11 SDK（本機已有 10.0.26100.0） |
| WDK | Windows 11 WDK + **Windows Driver Kit 的 VS 擴充功能** |

> **目前這台機器只裝了 SDK，還沒有 WDK**（`Include\<ver>\km\vhf.h` 不存在）。
> 請先安裝 WDK：<https://learn.microsoft.com/windows-hardware/drivers/download-the-wdk>
> 安裝時務必勾選最後一步的 *WDK Visual Studio extension*，否則 VS 不會有
> `WindowsKernelModeDriver10.0` 這個 Platform Toolset，專案會開不起來。

---

## 2. 建置

```powershell
# 用 VS 開啟
start driver\MapleVhid.sln

# 或用命令列（Developer PowerShell）
msbuild driver\MapleVhid.sln /p:Configuration=Release /p:Platform=x64
```

輸出：

- `driver\x64\Release\MapleVhid\` — 驅動封裝（`.sys` / `.inf` / `.cat`）
- `driver\x64\Release\MapleVhidClient.exe`

---

## 3. 安裝

驅動程式必須簽章。開發階段用測試簽章：

```powershell
# 系統管理員 PowerShell，執行後重開機
bcdedit /set testsigning on
```

重開機後（桌面右下角會顯示「測試模式」）：

```powershell
# 系統管理員
.\driver\install.bat
```

`install.bat` 做的事等同於：

```powershell
pnputil /add-driver x64\Release\MapleVhid\MapleVhid.inf /install
pnputil /add-device root\MapleVhid
```

若 `pnputil /add-device` 不可用，改用 WDK 附的 devcon：

```powershell
devcon install x64\Release\MapleVhid\MapleVhid.inf root\MapleVhid
```

安裝成功後：

- **裝置管理員 → 系統裝置** 會出現 `Maple Virtual HID Device (Keyboard + Mouse)`
- **鍵盤** 與 **滑鼠和其他指標裝置** 底下各多一個 HID 裝置

移除：`.\driver\uninstall.bat`（會列出狀態並提示指令，刻意不自動刪）。

---

## 4. 使用

### CLI

```powershell
# 需系統管理員
MapleVhidClient.exe demo                 # 展示：打字 + 畫方形 + 滾輪
MapleVhidClient.exe type "hello world"
MapleVhidClient.exe key down 0xE1        # 按住 Left Shift
MapleVhidClient.exe key tap  0x04        # 敲 'a'
MapleVhidClient.exe key reset
MapleVhidClient.exe mouse move 100 -50
MapleVhidClient.exe mouse click l
MapleVhidClient.exe mouse wheel -3
MapleVhidClient.exe state                # 含主機端 Caps/Num Lock LED 狀態
```

### Python（給 maple 的 server 用）

```python
from driver.maple_vhid import MapleVhid

with MapleVhid() as hid:
    hid.key_down(0x04); hid.key_up(0x04)     # 'a'
    hid.mouse_move(120, -30)
    hid.mouse_click(MapleVhid.BTN_LEFT)
    hid.mouse_wheel(-3)
    print(hid.get_state())
```

Python 行程必須有系統管理員權限（裝置 SDDL 限定 SYSTEM + Administrators）。

### C

```c
#include "MapleVhidApi.h"

HANDLE dev = MapleVhidOpen();
MapleKeyDown(dev, 0x04);
MapleKeyUp(dev, 0x04);
MapleMouseUpdate(dev, MAPLE_MOUSE_LEFT, 0, 25, -10, 0, 0);  // 按下+移動一次送出
MapleVhidClose(dev);
```

---

## 5. IOCTL 介面

全部是 `METHOD_BUFFERED` + `FILE_WRITE_ACCESS`，定義在 `MapleVhid/Public.h`。

| IOCTL | 輸入 | 說明 |
|---|---|---|
| `IOCTL_MAPLE_VHID_KEY_DOWN` | `MAPLE_KEY_EVENT` | 按下按鍵；`0xE0~0xE7` 自動轉成 modifier bit |
| `IOCTL_MAPLE_VHID_KEY_UP` | `MAPLE_KEY_EVENT` | 放開按鍵 |
| `IOCTL_MAPLE_VHID_KEY_RESET` | — | 放開所有按鍵 |
| `IOCTL_MAPLE_VHID_KEYBOARD_REPORT` | `MAPLE_KEYBOARD_REPORT` | 直接送完整 report（同步更新內部狀態） |
| `IOCTL_MAPLE_VHID_MOUSE_UPDATE` | `MAPLE_MOUSE_EVENT` | 一次表達 按下/放開/移動/滾輪 |
| `IOCTL_MAPLE_VHID_MOUSE_RESET` | — | 放開所有滑鼠鍵 |
| `IOCTL_MAPLE_VHID_MOUSE_REPORT` | `MAPLE_MOUSE_REPORT` | 直接送完整 report |
| `IOCTL_MAPLE_VHID_GET_STATE` | out `MAPLE_STATE` | 目前按鍵狀態 + 主機端 LED |

開啟裝置的兩種方式：

- 符號連結 `\\.\MapleVhid`（腳本語言方便）
- Device interface GUID `{9B4C6F2A-3D17-4E58-9A21-0C7F5BE38411}`（SetupAPI 列舉）

---

## 6. 設計重點

**兩個 Top Level Collection，一個 VHF 實體。**
`ReportDescriptor.h` 裡 Report ID 1 是 Keyboard、Report ID 2 是 Mouse。
HIDCLASS 會分別建立 collection，Windows 再各自對映到 `kbdhid` 與 `mouhid`。

**驅動維護按鍵狀態。**
HID 鍵盤 report 是「目前按住哪些鍵」的快照，不是事件流。
所以 `KEY_DOWN` / `KEY_UP` 由 `Queue.c` 的狀態機維護 6-key rollover 陣列與 modifier bitmask，
每次變動再送出完整 report。滑鼠按鍵同理。

**16-bit 相對座標。**
滑鼠 X/Y 用 `REPORT_SIZE(16)`、範圍 ±32767，遠端串流大幅度移動時不會被截斷成 ±127。

**LED 回寫。**
主機的 Caps/Num Lock 狀態會透過 Output Report 送回來，由
`MapleEvtVhfAsyncOperationWriteReport` 收下並記錄，可用 `GET_STATE` 讀取。
這個 callback 是必要的 —— 不實作的話主機設定 LED 的 IRP 會失敗。

**離開 D0 時放開所有鍵。**
`MapleEvtDeviceD0Exit` 會送出空 report，避免休眠或移除裝置時按鍵卡住。

**存取控制。**
`WdfDeviceInitAssignSDDLString(SDDL_DEVOBJ_SYS_ALL_ADM_RWX)` — 只有
SYSTEM 與 Administrators 能開啟。能開這個裝置就等同能注入任意輸入。

---

## 7. 已知限制與後續

- **VID/PID `0xFEED/0x1101` 是自己編的**，僅供本機使用。要正式散佈得申請 USB VID。
- **正式部署需要 EV 憑證 + Microsoft 屬性簽署**，否則只能停在測試模式。
- `VhfReadReportSubmit()` 送太快會回 `STATUS_DEVICE_BUSY`，目前直接把錯誤往上回傳，
  由 User Mode 決定重送或丟棄。若要做高頻率注入（例如遊戲滑鼠），
  建議在驅動內加一層 ring buffer + `EvtVhfReadyForNextReadReport` 流量控制。
- 鍵盤是 6-key rollover（Boot Keyboard 相容）。需要更多同時按鍵得改成 bitmap 式 report。
- 尚未實際編譯驗證（本機缺 WDK）。裝好 WDK 後第一次建置若有 `TreatWarningAsError`
  相關的警告，可在 `MapleVhid.vcxproj` 暫時關掉再逐一處理。
