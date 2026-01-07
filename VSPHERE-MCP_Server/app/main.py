import os
import logging
import ssl
import re
from typing import Optional, Dict
from dotenv import load_dotenv
from fastmcp import FastMCP

from pyVim import connect
from pyVmomi import vim

# Initialize FastMCP server
mcp = FastMCP("VMware-MCP-Server")

class VMwareManager:
    def __init__(self):
        load_dotenv()
        
        self.vcenter_host = os.getenv("VCENTER_HOST")
        self.vcenter_user = os.getenv("VCENTER_USER")
        self.vcenter_password = os.getenv("VCENTER_PASSWORD")
        self.insecure = os.getenv("VCENTER_INSECURE", "false").lower() in ("1", "true", "yes")

        log_level_str = os.getenv("MCP_LOG_LEVEL", "INFO")
        log_level = getattr(logging, log_level_str.upper(), logging.INFO)
        logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

        if not all([self.vcenter_host, self.vcenter_user, self.vcenter_password]):
            raise Exception("Missing required environment variables")

        self.si = None
        self.content = None
        self._connect_vcenter()

    def _connect_vcenter(self):
        """Connect to vCenter/ESXi."""
        try:
            if self.insecure:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self.si = connect.SmartConnect(
                    host=self.vcenter_host,
                    user=self.vcenter_user,
                    pwd=self.vcenter_password,
                    sslContext=context)
            else:
                self.si = connect.SmartConnect(
                    host=self.vcenter_host,
                    user=self.vcenter_user,
                    pwd=self.vcenter_password)
        except Exception as e:
            logging.error(f"Failed to connect: {e}")
            raise

        self.content = self.si.RetrieveContent()
        logging.info("Connected to VMware API")

    def _get_parent_info(self, vm) -> Dict[str, str]:
        """Walk up the inventory tree to find Datacenter and Cluster/Host."""
        info = {"datacenter": "Unknown", "cluster": "Unknown"}
        
        # 1. Find Cluster or Host
        # VM -> Parent is usually a Folder or ResourcePool, we need to go up until we hit a ComputeResource
        curr = vm.runtime.host if hasattr(vm, 'runtime') else None
        
        # If we can get the host directly from runtime
        if curr:
            # The parent of the host is usually the Cluster (if clustered)
            parent = curr.parent
            if isinstance(parent, vim.ClusterComputeResource):
                info["cluster"] = parent.name
            else:
                info["cluster"] = curr.name # Standalone host
        
        # 2. Find Datacenter
        # Walk up until we find a Datacenter object
        curr = vm.parent
        while curr:
            if isinstance(curr, vim.Datacenter):
                info["datacenter"] = curr.name
                break
            curr = curr.parent
            
        return info

    def find_vm_by_mac(self, target_mac: str) -> str:
        """Find a VM name, Datacenter, and Cluster by its MAC address."""
        clean_target = re.sub(r'[^a-fA-F0-9]', '', target_mac).lower()
        
        container = self.content.viewManager.CreateContainerView(
            self.content.rootFolder, [vim.VirtualMachine], True
        )
        
        result = None
        
        try:
            for vm in container.view:
                if not vm.config or not vm.config.hardware:
                    continue

                for device in vm.config.hardware.device:
                    if isinstance(device, vim.vm.device.VirtualEthernetCard):
                        vm_mac = re.sub(r'[^a-fA-F0-9]', '', device.macAddress).lower()
                        
                        if vm_mac == clean_target:
                            # Found match, get location info
                            loc_info = self._get_parent_info(vm)
                            result = (
                                f"Found VM: {vm.name}\n"
                                f"Datacenter: {loc_info['datacenter']}\n"
                                f"Cluster/Host: {loc_info['cluster']}\n"
                                f"MAC Address: {device.macAddress}"
                            )
                            break 
                if result:
                    break
        finally:
            container.Destroy()
            
        if result:
            return result
        else:
            return f"No VM found with MAC address {target_mac}"

# Global instance for lazy loading
_manager: Optional[VMwareManager] = None

def get_manager() -> VMwareManager:
    global _manager
    if _manager is None:
        _manager = VMwareManager()
    return _manager

# --- Tools ---

@mcp.tool()
def find_vm_by_mac(mac_address: str) -> str:
    """Find a VMware VM from its MAC address (e.g., 00:50:56:XX:XX:XX). 
    Args:
        target_MAC_address (str): The MAC address of the VM (e.g., 00:50:56:0A:0B:0C).
    Returns: 
        VM_Name (str), 
        Datacenter_Name (str),
        Cluster_Name (str).
    """
    return get_manager().find_vm_by_mac(mac_address)

if __name__ == "__main__":
    mcp.run()
