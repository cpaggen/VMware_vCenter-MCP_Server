import pytest
from unittest.mock import Mock, patch

import server


@pytest.fixture
def mock_manager():
    """Create a mock VMwareManager."""
    manager = Mock(spec=server.VMwareManager)
    manager.list_vms.return_value = ["vm1", "vm2", "vm3"]
    manager.create_vm.return_value = "VM 'test-vm' created."
    manager.clone_vm.return_value = "VM 'clone-vm' cloned from 'template'."
    manager.delete_vm.return_value = "VM 'test-vm' deleted."
    manager.power_on_vm.return_value = "VM 'test-vm' powered on."
    manager.power_off_vm.return_value = "VM 'test-vm' powered off."
    manager.get_vm_performance.return_value = {
        "cpu_usage": 100,
        "memory_usage": 512,
        "storage_usage": 10.5,
        "network_transmit_KBps": 1000,
        "network_receive_KBps": 500,
    }
    return manager


@pytest.fixture
def patch_manager(mock_manager):
    """Patch get_manager to return our mock."""
    with patch.object(server, "_manager", mock_manager):
        with patch.object(server, "get_manager", return_value=mock_manager):
            yield mock_manager


class TestListVMs:
    def test_list_vms_returns_list(self, patch_manager):
        result = server._list_vms()
        assert result == ["vm1", "vm2", "vm3"]
        patch_manager.list_vms.assert_called_once()


class TestCreateVM:
    def test_create_vm_basic(self, patch_manager):
        result = server._create_vm(name="test-vm", cpu=2, memory=1024)
        assert "created" in result
        patch_manager.create_vm.assert_called_once_with("test-vm", 2, 1024, None, None)

    def test_create_vm_with_options(self, patch_manager):
        result = server._create_vm(
            name="test-vm", cpu=4, memory=2048, datastore="ds1", network="net1"
        )
        assert "created" in result
        patch_manager.create_vm.assert_called_once_with(
            "test-vm", 4, 2048, "ds1", "net1"
        )


class TestCloneVM:
    def test_clone_vm(self, patch_manager):
        result = server._clone_vm(template_name="template", new_name="clone-vm")
        assert "cloned" in result
        patch_manager.clone_vm.assert_called_once_with("template", "clone-vm")


class TestDeleteVM:
    def test_delete_vm(self, patch_manager):
        result = server._delete_vm(name="test-vm")
        assert "deleted" in result
        patch_manager.delete_vm.assert_called_once_with("test-vm")


class TestPowerOperations:
    def test_power_on(self, patch_manager):
        result = server._power_on(name="test-vm")
        assert "powered on" in result
        patch_manager.power_on_vm.assert_called_once_with("test-vm")

    def test_power_off(self, patch_manager):
        result = server._power_off(name="test-vm")
        assert "powered off" in result
        patch_manager.power_off_vm.assert_called_once_with("test-vm")


class TestGetVMStats:
    def test_get_vm_stats(self, patch_manager):
        result = server._get_vm_stats(vm_name="test-vm")
        assert result["cpu_usage"] == 100
        assert result["memory_usage"] == 512
        assert result["storage_usage"] == 10.5
        patch_manager.get_vm_performance.assert_called_once_with("test-vm")


class TestConfig:
    def test_load_config_missing_required(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(Exception, match="Missing required"):
                server.load_config()

    def test_load_config_success(self):
        env = {
            "VCENTER_HOST": "vcenter.test.com",
            "VCENTER_USER": "admin",
            "VCENTER_PASSWORD": "secret",
            "VCENTER_INSECURE": "true",
        }
        with patch.dict("os.environ", env, clear=True):
            config = server.load_config()
            assert config.vcenter_host == "vcenter.test.com"
            assert config.vcenter_user == "admin"
            assert config.vcenter_password == "secret"
            assert config.insecure is True
