/*++

Module Name:

    Device.c

Abstract:

    PnP / Power callback。VHF 的生命週期綁在 PrepareHardware / ReleaseHardware。

--*/

#include "Driver.h"

NTSTATUS
MapleEvtDevicePrepareHardware(
    _In_ WDFDEVICE    Device,
    _In_ WDFCMRESLIST ResourcesRaw,
    _In_ WDFCMRESLIST ResourcesTranslated
)
{
    PDEVICE_CONTEXT deviceContext = DeviceGetContext(Device);

    UNREFERENCED_PARAMETER(ResourcesRaw);
    UNREFERENCED_PARAMETER(ResourcesTranslated);

    return MapleVhfCreate(deviceContext);
}

NTSTATUS
MapleEvtDeviceReleaseHardware(
    _In_ WDFDEVICE    Device,
    _In_ WDFCMRESLIST ResourcesTranslated
)
{
    PDEVICE_CONTEXT deviceContext = DeviceGetContext(Device);

    UNREFERENCED_PARAMETER(ResourcesTranslated);

    MapleVhfDestroy(deviceContext);
    return STATUS_SUCCESS;
}

NTSTATUS
MapleEvtDeviceD0Exit(
    _In_ WDFDEVICE              Device,
    _In_ WDF_POWER_DEVICE_STATE TargetState
)
/*++

Routine Description:

    離開 D0 之前把所有按鍵放開，避免休眠 / 移除裝置時按鍵卡住。

--*/
{
    PDEVICE_CONTEXT         deviceContext = DeviceGetContext(Device);
    MAPLE_KEYBOARD_REPORT   keyboard;
    MAPLE_MOUSE_REPORT      mouse;

    UNREFERENCED_PARAMETER(TargetState);

    WdfSpinLockAcquire(deviceContext->Lock);

    RtlZeroMemory(&deviceContext->Keyboard, sizeof(deviceContext->Keyboard));
    deviceContext->Keyboard.ReportId = MAPLE_REPORT_ID_KEYBOARD;
    deviceContext->MouseButtons      = 0;

    keyboard = deviceContext->Keyboard;

    RtlZeroMemory(&mouse, sizeof(mouse));
    mouse.ReportId = MAPLE_REPORT_ID_MOUSE;

    WdfSpinLockRelease(deviceContext->Lock);

    (VOID)MapleSubmitKeyboardReport(deviceContext, &keyboard);
    (VOID)MapleSubmitMouseReport(deviceContext, &mouse);

    return STATUS_SUCCESS;
}
