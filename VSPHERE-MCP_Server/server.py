import os
import logging
import ssl
from typing import Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv
from fastmcp import FastMCP

from pyVim import connect
from pyVmomi import vim

# Load environment variables from .env file
load_dotenv()


@dataclass
class Config:
    vcenter_host: str
    vcenter_user: str
    vcenter_password: str
    datacenter: Optional[str] = None
    cluster: Optional[str] = None
    datastore: Optional[str] = None
    network: Optional[str] = None
    insecure: bool = False
    log_level: str = "INFO"


class VMwareManager:
    def __init__(self, config: Config):
        self.config = config
        self.si = None
        self.content = None
        self.datacenter_obj = None
        self.resource_pool = None
        self.datastore_obj = None
        self.network_obj = None
        self._connect_vcenter()

    def _connect_vcenter(self):
        """Connect to vCenter/ESXi and retrieve main resource object references."""
        try:
            if self.config.insecure:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self.si = connect.SmartConnect(
                    host=self.config.vcenter_host,
                    user=self.config.vcenter_user,
                    pwd=self.config.vcenter_password,
                    sslContext=context)
            else:
                self.si = connect.SmartConnect(
                    host=self.config.vcenter_host,
                    user=self.config.vcenter_user,
                    pwd=self.config.vcenter_password)
        except Exception as e:
            logging.error(f"Failed to connect to vCenter/ESXi: {e}")
            raise

        self.content = self.si.RetrieveContent()
        logging.info("Successfully connected to VMware vCenter/ESXi API")

        # Retrieve target datacenter object
        if self.config.datacenter:
            self.datacenter_obj = next((dc for dc in self.content.rootFolder.childEntity
                                        if isinstance(dc, vim.Datacenter) and dc.name == self.config.datacenter), None)
            if not self.datacenter_obj:
                raise Exception(f"Datacenter {self.config.datacenter} not found")
        else:
            self.datacenter_obj = next((dc for dc in self.content.rootFolder.childEntity
                                        if isinstance(dc, vim.Datacenter)), None)
        if not self.datacenter_obj:
            raise Exception("No datacenter object found")

        # Retrieve resource pool
        compute_resource = None
        if self.config.cluster:
            for folder in self.datacenter_obj.hostFolder.childEntity:
                if isinstance(folder, vim.ClusterComputeResource) and folder.name == self.config.cluster:
                    compute_resource = folder
                    break
            if not compute_resource:
                raise Exception(f"Cluster {self.config.cluster} not found")
        else:
            compute_resource = next((cr for cr in self.datacenter_obj.hostFolder.childEntity
                                     if isinstance(cr, vim.ComputeResource)), None)
        if not compute_resource:
            raise Exception("No compute resource (cluster or host) found")
        self.resource_pool = compute_resource.resourcePool
        logging.info(f"Using resource pool: {self.resource_pool.name}")

        # Retrieve datastore object
        if self.config.datastore:
            self.datastore_obj = next((ds for ds in self.datacenter_obj.datastoreFolder.childEntity
                                       if isinstance(ds, vim.Datastore) and ds.name == self.config.datastore), None)
            if not self.datastore_obj:
                raise Exception(f"Datastore {self.config.datastore} not found")
        else:
            datastores = [ds for ds in self.datacenter_obj.datastoreFolder.childEntity if isinstance(ds, vim.Datastore)]
            if not datastores:
                raise Exception("No available datastore found in the datacenter")
            self.datastore_obj = max(datastores, key=lambda ds: ds.summary.freeSpace)
        logging.info(f"Using datastore: {self.datastore_obj.name}")

        # Retrieve network object
        if self.config.network:
            networks = self.datacenter_obj.networkFolder.childEntity
            self.network_obj = next((net for net in networks if net.name == self.config.network), None)
            if not self.network_obj:
                raise Exception(f"Network {self.config.network} not found")
            logging.info(f"Using network: {self.network_obj.name}")
        else:
            self.network_obj = None

    def list_vms(self) -> list:
        """List all virtual machine names."""
        vm_list = []
        container = self.content.viewManager.CreateContainerView(self.content.rootFolder, [vim.VirtualMachine], True)
        for vm in container.view:
            vm_list.append(vm.name)
        container.Destroy()
        return vm_list

    def find_vm(self, name: str) -> Optional[vim.VirtualMachine]:
        """Find virtual machine object by name."""
        container = self.content.viewManager.CreateContainerView(self.content.rootFolder, [vim.VirtualMachine], True)
        vm_obj = None
        for vm in container.view:
            if vm.name == name:
                vm_obj = vm
                break
        container.Destroy()
        return vm_obj

    def get_vm_performance(self, vm_name: str) -> Dict[str, Any]:
        """Retrieve performance data for the specified virtual machine."""
        vm = self.find_vm(vm_name)
        if not vm:
            raise Exception(f"VM {vm_name} not found")

        stats = {}
        qs = vm.summary.quickStats
        stats["cpu_usage"] = qs.overallCpuUsage  # MHz
        stats["memory_usage"] = qs.guestMemoryUsage  # MB
        committed = vm.summary.storage.committed if vm.summary.storage else 0
        stats["storage_usage"] = round(committed / (1024**3), 2)  # GB

        net_bytes_transmitted = 0
        net_bytes_received = 0
        try:
            pm = self.content.perfManager
            counter_ids = []
            for c in pm.perfCounter:
                counter_full_name = f"{c.groupInfo.key}.{c.nameInfo.key}.{c.rollupType}"
                if counter_full_name in ("net.transmitted.average", "net.received.average"):
                    counter_ids.append(c.key)
            if counter_ids:
                query = vim.PerformanceManager.QuerySpec(
                    maxSample=1, entity=vm,
                    metricId=[vim.PerformanceManager.MetricId(counterId=cid, instance="*") for cid in counter_ids])
                stats_res = pm.QueryStats(querySpec=[query])
                for series in stats_res[0].value:
                    if series.id.counterId == counter_ids[0]:
                        net_bytes_transmitted = sum(series.value)
                    elif series.id.counterId == counter_ids[1]:
                        net_bytes_received = sum(series.value)
            stats["network_transmit_KBps"] = net_bytes_transmitted
            stats["network_receive_KBps"] = net_bytes_received
        except Exception as e:
            logging.warning(f"Failed to retrieve network performance data: {e}")
            stats["network_transmit_KBps"] = None
            stats["network_receive_KBps"] = None
        return stats

    def create_vm(self, name: str, cpus: int, memory_mb: int, datastore: Optional[str] = None, network: Optional[str] = None) -> str:
        """Create a new virtual machine."""
        datastore_obj = self.datastore_obj
        network_obj = self.network_obj
        if datastore:
            datastore_obj = next((ds for ds in self.datacenter_obj.datastoreFolder.childEntity
                                  if isinstance(ds, vim.Datastore) and ds.name == datastore), None)
            if not datastore_obj:
                raise Exception(f"Specified datastore {datastore} not found")
        if network:
            networks = self.datacenter_obj.networkFolder.childEntity
            network_obj = next((net for net in networks if net.name == network), None)
            if not network_obj:
                raise Exception(f"Specified network {network} not found")

        vm_spec = vim.vm.ConfigSpec(name=name, memoryMB=memory_mb, numCPUs=cpus, guestId="otherGuest")
        device_specs = []

        # Add SCSI controller
        controller_spec = vim.vm.device.VirtualDeviceSpec()
        controller_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        controller_spec.device = vim.vm.device.ParaVirtualSCSIController()
        controller_spec.device.deviceInfo = vim.Description(label="SCSI Controller", summary="ParaVirtual SCSI Controller")
        controller_spec.device.busNumber = 0
        controller_spec.device.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing
        controller_spec.device.key = -101
        device_specs.append(controller_spec)

        # Add virtual disk
        disk_spec = vim.vm.device.VirtualDeviceSpec()
        disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        disk_spec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
        disk_spec.device = vim.vm.device.VirtualDisk()
        disk_spec.device.capacityInKB = 1024 * 1024 * 10  # 10GB
        disk_spec.device.deviceInfo = vim.Description(label="Hard Disk 1", summary="10 GB disk")
        disk_spec.device.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
        disk_spec.device.backing.diskMode = "persistent"
        disk_spec.device.backing.thinProvisioned = True
        disk_spec.device.backing.datastore = datastore_obj
        disk_spec.device.controllerKey = controller_spec.device.key
        disk_spec.device.unitNumber = 0
        device_specs.append(disk_spec)

        # Add network adapter if network is provided
        if network_obj:
            nic_spec = vim.vm.device.VirtualDeviceSpec()
            nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            nic_spec.device = vim.vm.device.VirtualVmxnet3()
            nic_spec.device.deviceInfo = vim.Description(label="Network Adapter 1", summary=network_obj.name)
            if isinstance(network_obj, vim.Network):
                nic_spec.device.backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(
                    network=network_obj, deviceName=network_obj.name)
            elif isinstance(network_obj, vim.dvs.DistributedVirtualPortgroup):
                dvs_uuid = network_obj.config.distributedVirtualSwitch.uuid
                port_key = network_obj.key
                nic_spec.device.backing = vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo(
                    port=vim.dvs.PortConnection(portgroupKey=port_key, switchUuid=dvs_uuid))
            nic_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo(startConnected=True, allowGuestControl=True)
            device_specs.append(nic_spec)

        vm_spec.deviceChange = device_specs
        vm_folder = self.datacenter_obj.vmFolder

        try:
            task = vm_folder.CreateVM_Task(config=vm_spec, pool=self.resource_pool)
            while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
                continue
            if task.info.state == vim.TaskInfo.State.error:
                raise task.info.error
        except Exception as e:
            logging.error(f"Failed to create virtual machine: {e}")
            raise
        logging.info(f"Virtual machine created: {name}")
        return f"VM '{name}' created."

    def clone_vm(self, template_name: str, new_name: str) -> str:
        """Clone a new virtual machine from an existing template or VM."""
        template_vm = self.find_vm(template_name)
        if not template_vm:
            raise Exception(f"Template virtual machine {template_name} not found")
        vm_folder = template_vm.parent
        if not isinstance(vm_folder, vim.Folder):
            vm_folder = self.datacenter_obj.vmFolder
        resource_pool = template_vm.resourcePool or self.resource_pool
        relocate_spec = vim.vm.RelocateSpec(pool=resource_pool, datastore=self.datastore_obj)
        clone_spec = vim.vm.CloneSpec(powerOn=False, template=False, location=relocate_spec)
        try:
            task = template_vm.Clone(folder=vm_folder, name=new_name, spec=clone_spec)
            while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
                continue
            if task.info.state == vim.TaskInfo.State.error:
                raise task.info.error
        except Exception as e:
            logging.error(f"Failed to clone virtual machine: {e}")
            raise
        logging.info(f"Cloned virtual machine {template_name} to new VM: {new_name}")
        return f"VM '{new_name}' cloned from '{template_name}'."

    def delete_vm(self, name: str) -> str:
        """Delete the specified virtual machine."""
        vm = self.find_vm(name)
        if not vm:
            raise Exception(f"Virtual machine {name} not found")
        try:
            task = vm.Destroy_Task()
            while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
                continue
            if task.info.state == vim.TaskInfo.State.error:
                raise task.info.error
        except Exception as e:
            logging.error(f"Failed to delete virtual machine: {e}")
            raise
        logging.info(f"Virtual machine deleted: {name}")
        return f"VM '{name}' deleted."

    def power_on_vm(self, name: str) -> str:
        """Power on the specified virtual machine."""
        vm = self.find_vm(name)
        if not vm:
            raise Exception(f"Virtual machine {name} not found")
        if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOn:
            return f"VM '{name}' is already powered on."
        task = vm.PowerOnVM_Task()
        while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
            continue
        if task.info.state == vim.TaskInfo.State.error:
            raise task.info.error
        logging.info(f"Virtual machine powered on: {name}")
        return f"VM '{name}' powered on."

    def power_off_vm(self, name: str) -> str:
        """Power off the specified virtual machine."""
        vm = self.find_vm(name)
        if not vm:
            raise Exception(f"Virtual machine {name} not found")
        if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOff:
            return f"VM '{name}' is already powered off."
        task = vm.PowerOffVM_Task()
        while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
            continue
        if task.info.state == vim.TaskInfo.State.error:
            raise task.info.error
        logging.info(f"Virtual machine powered off: {name}")
        return f"VM '{name}' powered off."


def load_config() -> Config:
    """Load configuration from environment variables."""
    vcenter_host = os.getenv("VCENTER_HOST")
    vcenter_user = os.getenv("VCENTER_USER")
    vcenter_password = os.getenv("VCENTER_PASSWORD")

    if not all([vcenter_host, vcenter_user, vcenter_password]):
        raise Exception("Missing required environment variables: VCENTER_HOST, VCENTER_USER, VCENTER_PASSWORD")

    return Config(
        vcenter_host=vcenter_host,
        vcenter_user=vcenter_user,
        vcenter_password=vcenter_password,
        datacenter=os.getenv("VCENTER_DATACENTER"),
        cluster=os.getenv("VCENTER_CLUSTER"),
        datastore=os.getenv("VCENTER_DATASTORE"),
        network=os.getenv("VCENTER_NETWORK"),
        insecure=os.getenv("VCENTER_INSECURE", "false").lower() in ("1", "true", "yes"),
        log_level=os.getenv("MCP_LOG_LEVEL", "INFO"),
    )


# Initialize FastMCP server
mcp = FastMCP("VMware-MCP-Server")

# Global manager instance (initialized lazily)
_manager: Optional[VMwareManager] = None


def get_manager() -> VMwareManager:
    """Get or initialize the VMware manager."""
    global _manager
    if _manager is None:
        config = load_config()
        log_level = getattr(logging, config.log_level.upper(), logging.INFO)
        logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")
        _manager = VMwareManager(config)
    return _manager


# Tool implementation functions (for testing)
def _list_vms() -> list[str]:
    """List all virtual machines."""
    return get_manager().list_vms()


def _create_vm(name: str, cpu: int, memory: int, datastore: Optional[str] = None, network: Optional[str] = None) -> str:
    """Create a new virtual machine."""
    return get_manager().create_vm(name, cpu, memory, datastore, network)


def _clone_vm(template_name: str, new_name: str) -> str:
    """Clone a virtual machine from a template or existing VM."""
    return get_manager().clone_vm(template_name, new_name)


def _delete_vm(name: str) -> str:
    """Delete a virtual machine."""
    return get_manager().delete_vm(name)


def _power_on(name: str) -> str:
    """Power on a virtual machine."""
    return get_manager().power_on_vm(name)


def _power_off(name: str) -> str:
    """Power off a virtual machine."""
    return get_manager().power_off_vm(name)


def _get_vm_stats(vm_name: str) -> dict:
    """Get CPU, memory, storage, and network usage for a VM."""
    return get_manager().get_vm_performance(vm_name)


# Register tools with FastMCP
@mcp.tool()
def list_vms() -> list[str]:
    """List all virtual machines."""
    return _list_vms()


@mcp.tool()
def create_vm(name: str, cpu: int, memory: int, datastore: Optional[str] = None, network: Optional[str] = None) -> str:
    """Create a new virtual machine.

    Args:
        name: Name of the VM to create
        cpu: Number of CPUs
        memory: Memory in MB
        datastore: Optional datastore name
        network: Optional network name
    """
    return _create_vm(name, cpu, memory, datastore, network)


@mcp.tool()
def clone_vm(template_name: str, new_name: str) -> str:
    """Clone a virtual machine from a template or existing VM.

    Args:
        template_name: Name of the template/source VM
        new_name: Name for the new cloned VM
    """
    return _clone_vm(template_name, new_name)


@mcp.tool()
def delete_vm(name: str) -> str:
    """Delete a virtual machine.

    Args:
        name: Name of the VM to delete
    """
    return _delete_vm(name)


@mcp.tool()
def power_on(name: str) -> str:
    """Power on a virtual machine.

    Args:
        name: Name of the VM to power on
    """
    return _power_on(name)


@mcp.tool()
def power_off(name: str) -> str:
    """Power off a virtual machine.

    Args:
        name: Name of the VM to power off
    """
    return _power_off(name)


@mcp.tool()
def get_vm_stats(vm_name: str) -> dict:
    """Get CPU, memory, storage, and network usage for a VM.

    Args:
        vm_name: Name of the VM to get stats for
    """
    return _get_vm_stats(vm_name)


if __name__ == "__main__":
    mcp.run()
