/*++

Module Name:

    Driver.c

Abstract:

    DriverEntry 與 EvtDeviceAdd。
    本驅動程式是 root-enumerated 的軟體裝置 (INF 中的 root\MapleVhid)，
    不擁有實體硬體資源，PnP 管理員仍會走完整的 PrepareHardware 流程。

--*/

//
// 只有這個 .c 檔會真的產生 GUID_DEVINTERFACE_MAPLE_VHID 的實體。
//
#define INITGUID
#include "Driver.h"

NTSTATUS
DriverEntry(
    _In_ PDRIVER_OBJECT  DriverObject,
    _In_ PUNICODE_STRING RegistryPath
)
{
    WDF_DRIVER_CONFIG       config;
    WDF_OBJECT_ATTRIBUTES   attributes;
    NTSTATUS                status;

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.EvtCleanupCallback = MapleEvtDriverContextCleanup;

    WDF_DRIVER_CONFIG_INIT(&config, MapleEvtDeviceAdd);

    status = WdfDriverCreate(DriverObject,
                             RegistryPath,
                             &attributes,
                             &config,
                             WDF_NO_HANDLE);

    if (!NT_SUCCESS(status)) {
        MapleError("WdfDriverCreate failed 0x%X", status);
        return status;
    }

    MapleTrace("DriverEntry ok");
    return STATUS_SUCCESS;
}

VOID
MapleEvtDriverContextCleanup(
    _In_ WDFOBJECT DriverObject
)
{
    UNREFERENCED_PARAMETER(DriverObject);
    MapleTrace("Driver unloaded");
}

NTSTATUS
MapleEvtDeviceAdd(
    _In_    WDFDRIVER       Driver,
    _Inout_ PWDFDEVICE_INIT DeviceInit
)
{
    WDF_OBJECT_ATTRIBUTES           attributes;
    WDF_PNPPOWER_EVENT_CALLBACKS    pnpPowerCallbacks;
    WDFDEVICE                       device;
    PDEVICE_CONTEXT                 deviceContext;
    DECLARE_CONST_UNICODE_STRING(symbolicLink, MAPLE_VHID_SYMBOLIC_LINK_NAME);
    NTSTATUS                        status;

    UNREFERENCED_PARAMETER(Driver);

    WDF_PNPPOWER_EVENT_CALLBACKS_INIT(&pnpPowerCallbacks);
    pnpPowerCallbacks.EvtDevicePrepareHardware = MapleEvtDevicePrepareHardware;
    pnpPowerCallbacks.EvtDeviceReleaseHardware = MapleEvtDeviceReleaseHardware;
    pnpPowerCallbacks.EvtDeviceD0Exit          = MapleEvtDeviceD0Exit;
    WdfDeviceInitSetPnpPowerEventCallbacks(DeviceInit, &pnpPowerCallbacks);

    //
    // 只允許系統管理員 / LocalSystem 開啟這個裝置：
    // 能開啟就等同能注入任意鍵盤滑鼠事件。
    //
    status = WdfDeviceInitAssignSDDLString(DeviceInit,
                                           &SDDL_DEVOBJ_SYS_ALL_ADM_RWX);
    if (!NT_SUCCESS(status)) {
        MapleError("WdfDeviceInitAssignSDDLString failed 0x%X", status);
        return status;
    }

    WdfDeviceInitSetIoType(DeviceInit, WdfDeviceIoBuffered);

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, DEVICE_CONTEXT);

    status = WdfDeviceCreate(&DeviceInit, &attributes, &device);
    if (!NT_SUCCESS(status)) {
        MapleError("WdfDeviceCreate failed 0x%X", status);
        return status;
    }

    deviceContext = DeviceGetContext(device);
    RtlZeroMemory(deviceContext, sizeof(DEVICE_CONTEXT));
    deviceContext->Device                = device;
    deviceContext->Keyboard.ReportId     = MAPLE_REPORT_ID_KEYBOARD;

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = device;

    status = WdfSpinLockCreate(&attributes, &deviceContext->Lock);
    if (!NT_SUCCESS(status)) {
        MapleError("WdfSpinLockCreate failed 0x%X", status);
        return status;
    }

    //
    // Device interface：給 SetupAPI 列舉用。
    //
    status = WdfDeviceCreateDeviceInterface(device,
                                            &GUID_DEVINTERFACE_MAPLE_VHID,
                                            NULL);
    if (!NT_SUCCESS(status)) {
        MapleError("WdfDeviceCreateDeviceInterface failed 0x%X", status);
        return status;
    }

    //
    // 固定符號連結 \\.\MapleVhid：給腳本語言直接 CreateFile 用。
    // 失敗不視為致命錯誤 (例如同時裝了兩份時會撞名)。
    //
    status = WdfDeviceCreateSymbolicLink(device, &symbolicLink);
    if (!NT_SUCCESS(status)) {
        MapleError("WdfDeviceCreateSymbolicLink failed 0x%X (non-fatal)", status);
    }

    status = MapleQueueInitialize(device);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    MapleTrace("DeviceAdd ok");
    return STATUS_SUCCESS;
}
