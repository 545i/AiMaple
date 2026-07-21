/*++

Module Name:

    Queue.c

Abstract:

    User Mode Client 的 IOCTL 入口。
    收到事件後更新內部狀態，組出 HID Input Report，交給 VHF 提交。

--*/

#include "Driver.h"

NTSTATUS
MapleQueueInitialize(
    _In_ WDFDEVICE Device
)
{
    WDF_IO_QUEUE_CONFIG queueConfig;
    WDFQUEUE            queue;
    NTSTATUS            status;

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&queueConfig, WdfIoQueueDispatchParallel);
    queueConfig.EvtIoDeviceControl = MapleEvtIoDeviceControl;

    status = WdfIoQueueCreate(Device,
                              &queueConfig,
                              WDF_NO_OBJECT_ATTRIBUTES,
                              &queue);

    if (!NT_SUCCESS(status)) {
        MapleError("WdfIoQueueCreate failed 0x%X", status);
    }

    return status;
}

//
// -------------------------- 鍵盤狀態機 --------------------------
//

static BOOLEAN
MapleIsModifier(
    _In_ UCHAR Usage
)
{
    return (Usage >= 0xE0 && Usage <= 0xE7);
}

static VOID
MapleKeyDownLocked(
    _In_ PDEVICE_CONTEXT DeviceContext,
    _In_ UCHAR           Usage
)
{
    ULONG i;
    ULONG freeSlot = MAPLE_KEYBOARD_MAX_KEYS;

    if (Usage == 0) {
        return;
    }

    if (MapleIsModifier(Usage)) {
        DeviceContext->Keyboard.Modifiers |= (UCHAR)(1u << (Usage - 0xE0));
        return;
    }

    for (i = 0; i < MAPLE_KEYBOARD_MAX_KEYS; i++) {
        if (DeviceContext->Keyboard.Keys[i] == Usage) {
            return;                                  // 已經按著，視為 auto-repeat
        }
        if (DeviceContext->Keyboard.Keys[i] == 0 && freeSlot == MAPLE_KEYBOARD_MAX_KEYS) {
            freeSlot = i;
        }
    }

    if (freeSlot < MAPLE_KEYBOARD_MAX_KEYS) {
        DeviceContext->Keyboard.Keys[freeSlot] = Usage;
    }
    //
    // 6 鍵全滿時直接忽略，行為與實體 Boot Keyboard 一致
    // (實體鍵盤會回報 ErrorRollOver，這裡選擇較安全的丟棄)。
    //
}

static VOID
MapleKeyUpLocked(
    _In_ PDEVICE_CONTEXT DeviceContext,
    _In_ UCHAR           Usage
)
{
    ULONG i;

    if (Usage == 0) {
        return;
    }

    if (MapleIsModifier(Usage)) {
        DeviceContext->Keyboard.Modifiers &= (UCHAR)~(1u << (Usage - 0xE0));
        return;
    }

    for (i = 0; i < MAPLE_KEYBOARD_MAX_KEYS; i++) {
        if (DeviceContext->Keyboard.Keys[i] == Usage) {
            DeviceContext->Keyboard.Keys[i] = 0;
            break;
        }
    }
}

//
// ---------------------------- IOCTL ----------------------------
//

static NTSTATUS
MapleHandleKeyEvent(
    _In_ PDEVICE_CONTEXT DeviceContext,
    _In_ WDFREQUEST      Request,
    _In_ BOOLEAN         Down
)
{
    PMAPLE_KEY_EVENT        input;
    size_t                  length;
    MAPLE_KEYBOARD_REPORT   report;
    NTSTATUS                status;

    status = WdfRequestRetrieveInputBuffer(Request,
                                           sizeof(MAPLE_KEY_EVENT),
                                           (PVOID*)&input,
                                           &length);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WdfSpinLockAcquire(DeviceContext->Lock);

    if (Down) {
        MapleKeyDownLocked(DeviceContext, input->Usage);
    } else {
        MapleKeyUpLocked(DeviceContext, input->Usage);
    }
    report = DeviceContext->Keyboard;

    WdfSpinLockRelease(DeviceContext->Lock);

    return MapleSubmitKeyboardReport(DeviceContext, &report);
}

static NTSTATUS
MapleHandleKeyReset(
    _In_ PDEVICE_CONTEXT DeviceContext
)
{
    MAPLE_KEYBOARD_REPORT report;

    WdfSpinLockAcquire(DeviceContext->Lock);
    RtlZeroMemory(&DeviceContext->Keyboard, sizeof(DeviceContext->Keyboard));
    DeviceContext->Keyboard.ReportId = MAPLE_REPORT_ID_KEYBOARD;
    report = DeviceContext->Keyboard;
    WdfSpinLockRelease(DeviceContext->Lock);

    return MapleSubmitKeyboardReport(DeviceContext, &report);
}

static NTSTATUS
MapleHandleKeyboardReport(
    _In_ PDEVICE_CONTEXT DeviceContext,
    _In_ WDFREQUEST      Request
)
/*++

Routine Description:

    直接送出一份完整的 Keyboard Report，並同步更新內部狀態，
    讓後續的 KEY_DOWN / KEY_UP 仍然接得上。

--*/
{
    PMAPLE_KEYBOARD_REPORT  input;
    size_t                  length;
    MAPLE_KEYBOARD_REPORT   report;
    NTSTATUS                status;

    status = WdfRequestRetrieveInputBuffer(Request,
                                           sizeof(MAPLE_KEYBOARD_REPORT),
                                           (PVOID*)&input,
                                           &length);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WdfSpinLockAcquire(DeviceContext->Lock);
    DeviceContext->Keyboard = *input;
    DeviceContext->Keyboard.ReportId = MAPLE_REPORT_ID_KEYBOARD;
    DeviceContext->Keyboard.Reserved = 0;
    report = DeviceContext->Keyboard;
    WdfSpinLockRelease(DeviceContext->Lock);

    return MapleSubmitKeyboardReport(DeviceContext, &report);
}

static NTSTATUS
MapleHandleMouseUpdate(
    _In_ PDEVICE_CONTEXT DeviceContext,
    _In_ WDFREQUEST      Request
)
{
    PMAPLE_MOUSE_EVENT  input;
    size_t              length;
    MAPLE_MOUSE_REPORT  report;
    NTSTATUS            status;

    status = WdfRequestRetrieveInputBuffer(Request,
                                           sizeof(MAPLE_MOUSE_EVENT),
                                           (PVOID*)&input,
                                           &length);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    RtlZeroMemory(&report, sizeof(report));

    WdfSpinLockAcquire(DeviceContext->Lock);

    DeviceContext->MouseButtons &= (UCHAR)~input->ButtonsUp;
    DeviceContext->MouseButtons |= input->ButtonsDown;

    report.Buttons = DeviceContext->MouseButtons;

    WdfSpinLockRelease(DeviceContext->Lock);

    report.ReportId = MAPLE_REPORT_ID_MOUSE;
    report.DeltaX   = input->DeltaX;
    report.DeltaY   = input->DeltaY;
    report.Wheel    = input->Wheel;
    report.HWheel   = input->HWheel;

    return MapleSubmitMouseReport(DeviceContext, &report);
}

static NTSTATUS
MapleHandleMouseReset(
    _In_ PDEVICE_CONTEXT DeviceContext
)
{
    MAPLE_MOUSE_REPORT report;

    RtlZeroMemory(&report, sizeof(report));
    report.ReportId = MAPLE_REPORT_ID_MOUSE;

    WdfSpinLockAcquire(DeviceContext->Lock);
    DeviceContext->MouseButtons = 0;
    WdfSpinLockRelease(DeviceContext->Lock);

    return MapleSubmitMouseReport(DeviceContext, &report);
}

static NTSTATUS
MapleHandleMouseReport(
    _In_ PDEVICE_CONTEXT DeviceContext,
    _In_ WDFREQUEST      Request
)
{
    PMAPLE_MOUSE_REPORT input;
    size_t              length;
    MAPLE_MOUSE_REPORT  report;
    NTSTATUS            status;

    status = WdfRequestRetrieveInputBuffer(Request,
                                           sizeof(MAPLE_MOUSE_REPORT),
                                           (PVOID*)&input,
                                           &length);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    report = *input;
    report.ReportId = MAPLE_REPORT_ID_MOUSE;

    WdfSpinLockAcquire(DeviceContext->Lock);
    DeviceContext->MouseButtons = report.Buttons;
    WdfSpinLockRelease(DeviceContext->Lock);

    return MapleSubmitMouseReport(DeviceContext, &report);
}

static NTSTATUS
MapleHandleGetState(
    _In_  PDEVICE_CONTEXT DeviceContext,
    _In_  WDFREQUEST      Request,
    _Out_ size_t*         BytesReturned
)
{
    PMAPLE_STATE    output;
    size_t          length;
    NTSTATUS        status;

    *BytesReturned = 0;

    status = WdfRequestRetrieveOutputBuffer(Request,
                                            sizeof(MAPLE_STATE),
                                            (PVOID*)&output,
                                            &length);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WdfSpinLockAcquire(DeviceContext->Lock);

    output->Modifiers    = DeviceContext->Keyboard.Modifiers;
    RtlCopyMemory(output->Keys,
                  DeviceContext->Keyboard.Keys,
                  MAPLE_KEYBOARD_MAX_KEYS);
    output->MouseButtons = DeviceContext->MouseButtons;
    output->Leds         = DeviceContext->Leds;

    WdfSpinLockRelease(DeviceContext->Lock);

    *BytesReturned = sizeof(MAPLE_STATE);
    return STATUS_SUCCESS;
}

VOID
MapleEvtIoDeviceControl(
    _In_ WDFQUEUE   Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t     OutputBufferLength,
    _In_ size_t     InputBufferLength,
    _In_ ULONG      IoControlCode
)
{
    PDEVICE_CONTEXT deviceContext;
    size_t          bytesReturned = 0;
    NTSTATUS        status;

    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    deviceContext = DeviceGetContext(WdfIoQueueGetDevice(Queue));

    switch (IoControlCode) {

    case IOCTL_MAPLE_VHID_KEY_DOWN:
        status = MapleHandleKeyEvent(deviceContext, Request, TRUE);
        break;

    case IOCTL_MAPLE_VHID_KEY_UP:
        status = MapleHandleKeyEvent(deviceContext, Request, FALSE);
        break;

    case IOCTL_MAPLE_VHID_KEY_RESET:
        status = MapleHandleKeyReset(deviceContext);
        break;

    case IOCTL_MAPLE_VHID_KEYBOARD_REPORT:
        status = MapleHandleKeyboardReport(deviceContext, Request);
        break;

    case IOCTL_MAPLE_VHID_MOUSE_UPDATE:
        status = MapleHandleMouseUpdate(deviceContext, Request);
        break;

    case IOCTL_MAPLE_VHID_MOUSE_RESET:
        status = MapleHandleMouseReset(deviceContext);
        break;

    case IOCTL_MAPLE_VHID_MOUSE_REPORT:
        status = MapleHandleMouseReport(deviceContext, Request);
        break;

    case IOCTL_MAPLE_VHID_GET_STATE:
        status = MapleHandleGetState(deviceContext, Request, &bytesReturned);
        break;

    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
        break;
    }

    WdfRequestCompleteWithInformation(Request, status, bytesReturned);
}
