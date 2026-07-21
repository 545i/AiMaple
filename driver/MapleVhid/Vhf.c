/*++

Module Name:

    Vhf.c

Abstract:

    Virtual HID Framework 的建立、銷毀，以及 Input Report 的提交。

    VhfCreate() 會在 HID class 之上生出一個虛擬 HID 裝置；
    我們宣告的 Report Descriptor 內含 Keyboard 與 Mouse 兩個
    Top Level Collection，因此系統會同時看到一支虛擬鍵盤與一支虛擬滑鼠。

--*/

#include "Driver.h"

EVT_VHF_ASYNC_OPERATION MapleEvtVhfAsyncOperationWriteReport;
EVT_VHF_CLEANUP         MapleEvtVhfCleanup;

NTSTATUS
MapleVhfCreate(
    _In_ PDEVICE_CONTEXT DeviceContext
)
{
    VHF_CONFIG  config;
    NTSTATUS    status;

    if (DeviceContext->VhfHandle != NULL) {
        return STATUS_SUCCESS;
    }

    VHF_CONFIG_INIT(&config,
                    WdfDeviceWdmGetDeviceObject(DeviceContext->Device),
                    (USHORT)sizeof(g_MapleReportDescriptor),
                    g_MapleReportDescriptor);

    config.VendorID      = MAPLE_VENDOR_ID;
    config.ProductID     = MAPLE_PRODUCT_ID;
    config.VersionNumber = MAPLE_VERSION;

    config.VhfClientContext = DeviceContext;

    //
    // 主機 (kbdclass) 會送 Output Report 過來設定 Caps/Num Lock LED，
    // 不處理的話那個 IRP 會失敗，因此必須實作。
    //
    config.EvtVhfAsyncOperationWriteReport = MapleEvtVhfAsyncOperationWriteReport;
    config.EvtVhfCleanup                   = MapleEvtVhfCleanup;

    status = VhfCreate(&config, &DeviceContext->VhfHandle);
    if (!NT_SUCCESS(status)) {
        MapleError("VhfCreate failed 0x%X", status);
        DeviceContext->VhfHandle = NULL;
        return status;
    }

    status = VhfStart(DeviceContext->VhfHandle);
    if (!NT_SUCCESS(status)) {
        MapleError("VhfStart failed 0x%X", status);
        VhfDelete(DeviceContext->VhfHandle, TRUE);
        DeviceContext->VhfHandle = NULL;
        return status;
    }

    DeviceContext->VhfStarted = TRUE;
    MapleTrace("VHF started (descriptor %u bytes)",
               (ULONG)sizeof(g_MapleReportDescriptor));

    return STATUS_SUCCESS;
}

VOID
MapleVhfDestroy(
    _In_ PDEVICE_CONTEXT DeviceContext
)
{
    if (DeviceContext->VhfHandle != NULL) {
        //
        // Wait = TRUE：等所有進行中的 VHF callback 結束後才返回。
        //
        VhfDelete(DeviceContext->VhfHandle, TRUE);
        DeviceContext->VhfHandle  = NULL;
        DeviceContext->VhfStarted = FALSE;
        MapleTrace("VHF deleted");
    }
}

VOID
MapleEvtVhfCleanup(
    _In_ PVOID VhfClientContext
)
{
    UNREFERENCED_PARAMETER(VhfClientContext);
    MapleTrace("VHF cleanup");
}

VOID
MapleEvtVhfAsyncOperationWriteReport(
    _In_ PVOID              VhfClientContext,
    _In_ VHFOPERATIONHANDLE VhfOperationHandle,
    _In_ PVOID              VhfOperationContext,
    _In_ PHID_XFER_PACKET   HidTransferPacket
)
/*++

Routine Description:

    接收主機送來的 Output Report。目前只有鍵盤 LED。

--*/
{
    PDEVICE_CONTEXT deviceContext = (PDEVICE_CONTEXT)VhfClientContext;
    UCHAR           leds          = 0;

    UNREFERENCED_PARAMETER(VhfOperationContext);

    if (HidTransferPacket->reportId == MAPLE_REPORT_ID_KEYBOARD &&
        HidTransferPacket->reportBuffer != NULL &&
        HidTransferPacket->reportBufferLen > 0) {

        //
        // 依 HIDCLASS 的實作，buffer 可能含或不含開頭的 Report ID，
        // 兩種格式都吃。
        //
        if (HidTransferPacket->reportBufferLen >= 2 &&
            HidTransferPacket->reportBuffer[0] == MAPLE_REPORT_ID_KEYBOARD) {
            leds = HidTransferPacket->reportBuffer[1];
        } else {
            leds = HidTransferPacket->reportBuffer[0];
        }

        WdfSpinLockAcquire(deviceContext->Lock);
        deviceContext->Leds = leds;
        WdfSpinLockRelease(deviceContext->Lock);

        MapleTrace("LED state = 0x%02X", leds);
    }

    VhfAsyncOperationComplete(VhfOperationHandle, STATUS_SUCCESS);
}

_IRQL_requires_max_(DISPATCH_LEVEL)
static NTSTATUS
MapleSubmitReport(
    _In_ PDEVICE_CONTEXT DeviceContext,
    _In_ PVOID           Buffer,
    _In_ ULONG           Length,
    _In_ UCHAR           ReportId
)
{
    HID_XFER_PACKET packet;
    NTSTATUS        status;

    if (DeviceContext->VhfHandle == NULL || !DeviceContext->VhfStarted) {
        return STATUS_DEVICE_NOT_READY;
    }

    RtlZeroMemory(&packet, sizeof(packet));
    packet.reportBuffer    = (PUCHAR)Buffer;
    packet.reportBufferLen = Length;
    packet.reportId        = ReportId;

    status = VhfReadReportSubmit(DeviceContext->VhfHandle, &packet);
    if (!NT_SUCCESS(status)) {
        //
        // 送太快時 VHF 會回 STATUS_DEVICE_BUSY，這裡直接把錯誤回傳給
        // User Mode 由上層決定是否重送 / 丟棄。
        //
        MapleError("VhfReadReportSubmit(id=%u) failed 0x%X", ReportId, status);
    }

    return status;
}

_IRQL_requires_max_(DISPATCH_LEVEL)
NTSTATUS
MapleSubmitKeyboardReport(
    _In_ PDEVICE_CONTEXT       DeviceContext,
    _In_ PMAPLE_KEYBOARD_REPORT Report
)
{
    Report->ReportId = MAPLE_REPORT_ID_KEYBOARD;
    Report->Reserved = 0;

    return MapleSubmitReport(DeviceContext,
                             Report,
                             sizeof(MAPLE_KEYBOARD_REPORT),
                             MAPLE_REPORT_ID_KEYBOARD);
}

_IRQL_requires_max_(DISPATCH_LEVEL)
NTSTATUS
MapleSubmitMouseReport(
    _In_ PDEVICE_CONTEXT    DeviceContext,
    _In_ PMAPLE_MOUSE_REPORT Report
)
{
    Report->ReportId = MAPLE_REPORT_ID_MOUSE;

    return MapleSubmitReport(DeviceContext,
                             Report,
                             sizeof(MAPLE_MOUSE_REPORT),
                             MAPLE_REPORT_ID_MOUSE);
}
