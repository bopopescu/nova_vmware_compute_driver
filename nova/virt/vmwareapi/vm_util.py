# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2011 Citrix Systems, Inc.
# Copyright 2011 OpenStack LLC.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
The VMware API VM utility module to build SOAP object specs.
"""

import copy
from nova.virt.vmwareapi import vim_util


def build_datastore_path(datastore_name, path):
    """Build the datastore compliant path."""
    return "[%s] %s" % (datastore_name, path)


def split_datastore_path(datastore_path):
    """
    Split the VMware style datastore path to get the Datastore
    name and the entity path.
    """
    spl = datastore_path.split('[', 1)[1].split(']', 1)
    path = ""
    if len(spl) == 1:
        datastore_url = spl[0]
    else:
        datastore_url, path = spl
    return datastore_url, path.strip()


def get_vm_create_spec(client_factory, instance, data_store_name,
                       vif_infos, os_type="otherGuest"):
    """Builds the VM Create spec."""
    config_spec = client_factory.create('ns0:VirtualMachineConfigSpec')
    config_spec.name = instance['name']
    config_spec.guestId = os_type

    vm_file_info = client_factory.create('ns0:VirtualMachineFileInfo')
    vm_file_info.vmPathName = "[" + data_store_name + "]"
    config_spec.files = vm_file_info

    tools_info = client_factory.create('ns0:ToolsConfigInfo')
    tools_info.afterPowerOn = True
    tools_info.afterResume = True
    tools_info.beforeGuestStandby = True
    tools_info.beforeGuestShutdown = True
    tools_info.beforeGuestReboot = True

    config_spec.tools = tools_info
    config_spec.numCPUs = int(instance['vcpus'])
    config_spec.memoryMB = int(instance['memory_mb'])

    vif_spec_list = []
    for vif_info in vif_infos:
        vif_spec = create_network_spec(client_factory, vif_info)
        vif_spec_list.append(vif_spec)

    device_config_spec = vif_spec_list

    config_spec.deviceChange = device_config_spec
    return config_spec


def create_controller_spec(client_factory, key, adapter_type="lsiLogic"):
    """
    Builds a Config Spec for the LSI or Bus Logic Controller's addition
    which acts as the controller for the virtual hard disk to be attached
    to the VM.
    """
    # Create a controller for the Virtual Hard Disk
    virtual_device_config = client_factory.create(
                            'ns0:VirtualDeviceConfigSpec')
    virtual_device_config.operation = "add"
    if adapter_type == "busLogic":
        virtual_controller = client_factory.create(
                                'ns0:VirtualBusLogicController')
    else:
        virtual_controller = client_factory.create(
                                'ns0:VirtualLsiLogicController')
    virtual_controller.key = key
    virtual_controller.busNumber = 0
    virtual_controller.sharedBus = "noSharing"
    virtual_device_config.device = virtual_controller
    return virtual_device_config


def create_network_spec(client_factory, vif_info):
    """
    Builds a config spec for the addition of a new network
    adapter to the VM.
    """
    network_spec = client_factory.create('ns0:VirtualDeviceConfigSpec')
    network_spec.operation = "add"

    # Get the recommended card type for the VM based on the guest OS of the VM
    net_device = client_factory.create('ns0:VirtualPCNet32')

    # NOTE(asomya): Only works on ESXi if the portgroup binding is set to
    # ephemeral. Invalid configuration if set to static and the NIC does
    # not come up on boot if set to dynamic.
    network_ref = vif_info['network_ref']
    network_name = vif_info['network_name']
    mac_address = vif_info['mac_address']
    backing = None
    if (network_ref and
        network_ref['type'] == "DistributedVirtualPortgroup"):
        backing_name = ''.join(['ns0:VirtualEthernetCardDistributed',
                                'VirtualPortBackingInfo'])
        backing = client_factory.create(backing_name)
        portgroup = client_factory.create(
                    'ns0:DistributedVirtualSwitchPortConnection')
        portgroup.switchUuid = network_ref['dvsw']
        portgroup.portgroupKey = network_ref['dvpg']
        backing.port = portgroup
    else:
        backing = client_factory.create(
                  'ns0:VirtualEthernetCardNetworkBackingInfo')
        backing.deviceName = network_name

    connectable_spec = client_factory.create('ns0:VirtualDeviceConnectInfo')
    connectable_spec.startConnected = True
    connectable_spec.allowGuestControl = True
    connectable_spec.connected = True

    net_device.connectable = connectable_spec
    net_device.backing = backing

    # The Server assigns a Key to the device. Here we pass a -ve temporary key.
    # -ve because actual keys are +ve numbers and we don't
    # want a clash with the key that server might associate with the device
    net_device.key = -47
    net_device.addressType = "manual"
    net_device.macAddress = mac_address
    net_device.wakeOnLanEnabled = True

    network_spec.device = net_device
    return network_spec


def get_vmdk_attach_config_spec(client_factory,
                                adapter_type="lsiLogic",
                                disk_type="preallocated",
                                file_path=None,
                                disk_size=None,
                                linked_clone=False,
                                controller_key=None,
                                unit_number=None,
                                device_name=None):
    """Builds the vmdk attach config spec."""
    config_spec = client_factory.create('ns0:VirtualMachineConfigSpec')

    # The controller Key pertains to the Key of the LSI Logic Controller, which
    # controls this Hard Disk
    device_config_spec = []
    # For IDE devices, there are these two default controllers created in the
    # VM having keys 200 and 201
    if controller_key is None:
        if adapter_type == "ide":
            controller_key = 200
        else:
            controller_key = -101
            controller_spec = create_controller_spec(client_factory,
                                                     controller_key,
                                                     adapter_type)
            device_config_spec.append(controller_spec)
    virtual_device_config_spec = create_virtual_disk_spec(client_factory,
                                controller_key, disk_type, file_path,
                                disk_size, linked_clone,
                                unit_number, device_name)

    device_config_spec.append(virtual_device_config_spec)

    config_spec.deviceChange = device_config_spec
    return config_spec


def get_vmdk_file_path_and_adapter_type(client_factory, hardware_devices):
    """Gets the vmdk file path and the storage adapter type."""
    if hardware_devices.__class__.__name__ == "ArrayOfVirtualDevice":
        hardware_devices = hardware_devices.VirtualDevice
    vmdk_file_path = None
    vmdk_controler_key = None

    adapter_type_dict = {}
    for device in hardware_devices:
        if (device.__class__.__name__ == "VirtualDisk" and
            device.backing.__class__.__name__ ==
                    "VirtualDiskFlatVer2BackingInfo"):
            vmdk_file_path = device.backing.fileName
            vmdk_controler_key = device.controllerKey
        elif device.__class__.__name__ == "VirtualLsiLogicController":
            adapter_type_dict[device.key] = "lsiLogic"
        elif device.__class__.__name__ == "VirtualBusLogicController":
            adapter_type_dict[device.key] = "busLogic"
        elif device.__class__.__name__ == "VirtualIDEController":
            adapter_type_dict[device.key] = "ide"
        elif device.__class__.__name__ == "VirtualLsiLogicSASController":
            adapter_type_dict[device.key] = "lsiLogic"

    adapter_type = adapter_type_dict.get(vmdk_controler_key, "")

    return vmdk_file_path, adapter_type


def get_vmdk_detach_config_spec(client_factory, device):
    """Builds the vmdk detach config spec."""
    config_spec = client_factory.create('ns0:VirtualMachineConfigSpec')

    device_config_spec = []
    virtual_device_config_spec = delete_virtual_disk_spec(client_factory,
                                                          device)

    device_config_spec.append(virtual_device_config_spec)

    config_spec.deviceChange = device_config_spec
    return config_spec


def get_vmdk_path_and_adapter_type(hardware_devices):
    """Gets the vmdk file path and the storage adapter type."""
    if hardware_devices.__class__.__name__ == "ArrayOfVirtualDevice":
        hardware_devices = hardware_devices.VirtualDevice
    vmdk_file_path = None
    vmdk_controler_key = None
    disk_type = None
    unit_number = 0

    adapter_type_dict = {}
    for device in hardware_devices:
        if device.__class__.__name__ == "VirtualDisk":
            if device.backing.__class__.__name__ == \
                    "VirtualDiskFlatVer2BackingInfo":
                vmdk_file_path = device.backing.fileName
                vmdk_controler_key = device.controllerKey
                if getattr(device.backing, 'thinProvisioned', False):
                    disk_type = "thin"
                else:
                    if getattr(device.backing, 'eagerlyScrub', False):
                        disk_type = "eagerZeroedThick"
                    else:
                        disk_type = "preallocated"
            if device.unitNumber > unit_number:
                unit_number = device.unitNumber
        elif device.__class__.__name__ == "VirtualLsiLogicController":
            adapter_type_dict[device.key] = "lsiLogic"
        elif device.__class__.__name__ == "VirtualBusLogicController":
            adapter_type_dict[device.key] = "busLogic"
        elif device.__class__.__name__ == "VirtualIDEController":
            adapter_type_dict[device.key] = "ide"
        elif device.__class__.__name__ == "VirtualLsiLogicSASController":
            adapter_type_dict[device.key] = "lsiLogic"

    adapter_type = adapter_type_dict.get(vmdk_controler_key, "")

    return (vmdk_file_path, vmdk_controler_key, adapter_type,
            disk_type, unit_number)


def get_rdm_disk(hardware_devices, uuid):
    """Gets the RDM disk key."""
    if hardware_devices.__class__.__name__ == "ArrayOfVirtualDevice":
        hardware_devices = hardware_devices.VirtualDevice

    for device in hardware_devices:
        if (device.__class__.__name__ == "VirtualDisk" and
            device.backing.__class__.__name__ ==
                "VirtualDiskRawDiskMappingVer1BackingInfo" and
            device.backing.lunUuid == uuid):
            return device


def get_copy_virtual_disk_spec(client_factory, adapter_type="lsilogic",
                               disk_type="preallocated"):
    """Builds the Virtual Disk copy spec."""
    dest_spec = client_factory.create('ns0:VirtualDiskSpec')
    dest_spec.adapterType = adapter_type
    dest_spec.diskType = disk_type
    return dest_spec


def get_vmdk_create_spec(client_factory, size_in_kb, adapter_type="lsiLogic",
                         disk_type="preallocated"):
    """Builds the virtual disk create spec."""
    create_vmdk_spec = client_factory.create('ns0:FileBackedVirtualDiskSpec')
    create_vmdk_spec.adapterType = adapter_type
    create_vmdk_spec.diskType = disk_type
    create_vmdk_spec.capacityKb = size_in_kb
    return create_vmdk_spec


def get_rdm_create_spec(client_factory, device, adapter_type="lsiLogic",
                        disk_type="rdmp"):
    """Builds the RDM virtual disk create spec."""
    create_vmdk_spec = client_factory.create('ns0:DeviceBackedVirtualDiskSpec')
    create_vmdk_spec.adapterType = adapter_type
    create_vmdk_spec.diskType = disk_type
    create_vmdk_spec.device = device
    return create_vmdk_spec


def create_virtual_disk_spec(client_factory, controller_key,
                             disk_type="preallocated",
                             file_path=None,
                             disk_size=None,
                             linked_clone=False,
                             unit_number=None,
                             device_name=None):
    """
    Builds spec for the creation of a new/ attaching of an already existing
    Virtual Disk to the VM.
    """
    virtual_device_config = client_factory.create(
                            'ns0:VirtualDeviceConfigSpec')
    virtual_device_config.operation = "add"
    if (file_path is None) or linked_clone:
        virtual_device_config.fileOperation = "create"

    virtual_disk = client_factory.create('ns0:VirtualDisk')

    if disk_type == "rdm" or disk_type == "rdmp":
        disk_file_backing = client_factory.create(
                            'ns0:VirtualDiskRawDiskMappingVer1BackingInfo')
        disk_file_backing.compatibilityMode = "virtualMode" \
            if disk_type == "rdm" else "physicalMode"
        disk_file_backing.diskMode = "independent_persistent"
        disk_file_backing.deviceName = device_name or ""
    else:
        disk_file_backing = client_factory.create(
                            'ns0:VirtualDiskFlatVer2BackingInfo')
        disk_file_backing.diskMode = "persistent"
        if disk_type == "thin":
            disk_file_backing.thinProvisioned = True
        else:
            if disk_type == "eagerZeroedThick":
                disk_file_backing.eagerlyScrub = True
    disk_file_backing.fileName = file_path or ""

    connectable_spec = client_factory.create('ns0:VirtualDeviceConnectInfo')
    connectable_spec.startConnected = True
    connectable_spec.allowGuestControl = False
    connectable_spec.connected = True

    if not linked_clone:
        virtual_disk.backing = disk_file_backing
    else:
        virtual_disk.backing = copy.copy(disk_file_backing)
        virtual_disk.backing.fileName = ""
        virtual_disk.backing.parent = disk_file_backing
    virtual_disk.connectable = connectable_spec

    # The Server assigns a Key to the device. Here we pass a -ve random key.
    # -ve because actual keys are +ve numbers and we don't
    # want a clash with the key that server might associate with the device
    virtual_disk.key = -100
    virtual_disk.controllerKey = controller_key
    virtual_disk.unitNumber = unit_number or 0
    virtual_disk.capacityInKB = disk_size or 0

    virtual_device_config.device = virtual_disk

    return virtual_device_config


def delete_virtual_disk_spec(client_factory, device):
    """
    Builds spec for the deletion of an already existing Virtual Disk from VM.
    """
    virtual_device_config = client_factory.create(
                            'ns0:VirtualDeviceConfigSpec')
    virtual_device_config.operation = "remove"
    virtual_device_config.fileOperation = "destroy"
    virtual_device_config.device = device

    return virtual_device_config


def get_dummy_vm_create_spec(client_factory, name, data_store_name):
    """Builds the dummy VM create spec."""
    config_spec = client_factory.create('ns0:VirtualMachineConfigSpec')

    config_spec.name = name
    config_spec.guestId = "otherGuest"

    vm_file_info = client_factory.create('ns0:VirtualMachineFileInfo')
    vm_file_info.vmPathName = "[" + data_store_name + "]"
    config_spec.files = vm_file_info

    tools_info = client_factory.create('ns0:ToolsConfigInfo')
    tools_info.afterPowerOn = True
    tools_info.afterResume = True
    tools_info.beforeGuestStandby = True
    tools_info.beforeGuestShutdown = True
    tools_info.beforeGuestReboot = True

    config_spec.tools = tools_info
    config_spec.numCPUs = 1
    config_spec.memoryMB = 4

    controller_key = -101
    controller_spec = create_controller_spec(client_factory, controller_key)
    disk_spec = create_virtual_disk_spec(client_factory, 1024, controller_key)

    device_config_spec = [controller_spec, disk_spec]

    config_spec.deviceChange = device_config_spec
    return config_spec


def get_machine_id_change_spec(client_factory, machine_id_str):
    """Builds the machine id change config spec."""
    virtual_machine_config_spec = client_factory.create(
                                  'ns0:VirtualMachineConfigSpec')

    opt = client_factory.create('ns0:OptionValue')
    opt.key = "machine.id"
    opt.value = machine_id_str
    virtual_machine_config_spec.extraConfig = [opt]
    return virtual_machine_config_spec


def get_add_vswitch_port_group_spec(client_factory, vswitch_name,
                                    port_group_name, vlan_id):
    """Builds the virtual switch port group add spec."""
    vswitch_port_group_spec = client_factory.create('ns0:HostPortGroupSpec')
    vswitch_port_group_spec.name = port_group_name
    vswitch_port_group_spec.vswitchName = vswitch_name

    # VLAN ID of 0 means that VLAN tagging is not to be done for the network.
    vswitch_port_group_spec.vlanId = int(vlan_id)

    policy = client_factory.create('ns0:HostNetworkPolicy')
    nicteaming = client_factory.create('ns0:HostNicTeamingPolicy')
    nicteaming.notifySwitches = True
    policy.nicTeaming = nicteaming

    vswitch_port_group_spec.policy = policy
    return vswitch_port_group_spec


def get_vm_ref_from_name(session, vm_name):
    """Get reference to the VM with the name specified."""
    vms = session._call_method(vim_util, "get_objects",
                "VirtualMachine", ["name"])
    for vm in vms:
        if vm.propSet[0].val == vm_name:
            return vm.obj
    return None
