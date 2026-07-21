/*++

Module Name:

    Driver.h

Abstract:

    MapleVhid 驅動程式共用標頭。

--*/

#pragma once

#include <ntddk.h>
#include <wdf.h>
#include <wdmsec.h>     // SDDL_DEVOBJ_* (需連結 wdmsec.lib)
#include <hidport.h>
#include <vhf.h>

#include "Public.h"
#include "ReportDescriptor.h"

#define MAPLE_POOL_TAG  'lpaM'   // "Mapl"

#define MapleTrace(_fmt_, ...) \
    DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_INFO_LEVEL, "MapleVhid: " _fmt_ "\n", __VA_ARGS__)

#define MapleError(_fmt_, ...) \
    DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_ERROR_LEVEL, "MapleVhid!ERR: " _fmt_ "\n", __VA_ARGS__)

//
// 虛擬裝置識別碼。VendorID 使用 0xFEED 這類未分配值僅供本機使用，
// 若要正式散佈請換成自己申請的 USB VID/PID。
//
#define MAPLE_VENDOR_ID     0xFEED
#define MAPLE_PRODUCT_ID    0x1101
#define MAPLE_VERSION       0x0100

typedef struct _DEVICE_CONTEXT
{
    WDFDEVICE               Device;

    //
    // VHF handle。由 EvtDevicePrepareHardware 建立、
    // EvtDeviceReleaseHardware 刪除。
    //
    VHFHANDLE               VhfHandle;
    BOOLEAN                 VhfStarted;

    //
    // 保護底下所有狀態。IOCTL 可能在 DISPATCH_LEVEL 平行進來。
    //
    WDFSPINLOCK             Lock;

    MAPLE_KEYBOARD_REPORT   Keyboard;       // 目前按住的鍵
    UCHAR                   MouseButtons;   // 目前按住的滑鼠鍵
    UCHAR                   Leds;           // 主機端送來的 LED 狀態

} DEVICE_CONTEXT, *PDEVICE_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(DEVICE_CONTEXT, DeviceGetContext)

//
// Driver.c
//
DRIVER_INITIALIZE                   DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD           MapleEvtDeviceAdd;
EVT_WDF_OBJECT_CONTEXT_CLEANUP      MapleEvtDriverContextCleanup;

//
// Device.c
//
EVT_WDF_DEVICE_PREPARE_HARDWARE     MapleEvtDevicePrepareHardware;
EVT_WDF_DEVICE_RELEASE_HARDWARE     MapleEvtDeviceReleaseHardware;
EVT_WDF_DEVICE_D0_EXIT              MapleEvtDeviceD0Exit;

//
// Vhf.c
//
NTSTATUS MapleVhfCreate(_In_ PDEVICE_CONTEXT DeviceContext);
VOID     MapleVhfDestroy(_In_ PDEVICE_CONTEXT DeviceContext);

_IRQL_requires_max_(DISPATCH_LEVEL)
NTSTATUS MapleSubmitKeyboardReport(_In_ PDEVICE_CONTEXT DeviceContext,
                                   _In_ PMAPLE_KEYBOARD_REPORT Report);

_IRQL_requires_max_(DISPATCH_LEVEL)
NTSTATUS MapleSubmitMouseReport(_In_ PDEVICE_CONTEXT DeviceContext,
                                _In_ PMAPLE_MOUSE_REPORT Report);

//
// Queue.c
//
NTSTATUS MapleQueueInitialize(_In_ WDFDEVICE Device);
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL  MapleEvtIoDeviceControl;
