# VMware vSphere MCP Server

A Model Context Protocol (MCP) server for finding VMware VMs by MAC address.

## Setup

1. Install dependencies:
   ```bash
   uv sync
   ```

2. Configure environment variables:
   ```bash
   cp app/.env.example app/.env
   # Edit app/.env with your vCenter credentials
   ```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `VCENTER_HOST` | Yes | vCenter/ESXi hostname or IP |
| `VCENTER_USER` | Yes | Username (e.g., `user@domain`) |
| `VCENTER_PASSWORD` | Yes | Password |
| `VCENTER_INSECURE` | No | Skip SSL verification (default: `false`) |
| `MCP_LOG_LEVEL` | No | Log level (default: `INFO`) |

## Usage

This MCP server is designed to be invoked by an MCP client such as **Claude Desktop** or **Visual Studio Code** with the Claude extension.

### Claude Desktop

Add to your Claude Desktop configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vsphere": {
      "command": "uv",
      "args": ["run", "python", "app/main.py"],
      "cwd": "/path/to/VSPHERE-MCP_Server",
      "env": {
        "VCENTER_HOST": "your-vcenter-host",
        "VCENTER_USER": "your-username",
        "VCENTER_PASSWORD": "your-password",
        "VCENTER_INSECURE": "true"
      }
    }
  }
}
```

### Visual Studio Code

Add to your VS Code settings (`.vscode/settings.json` or user settings):

```json
{
  "claude.mcpServers": {
    "vsphere": {
      "command": "uv",
      "args": ["run", "python", "app/main.py"],
      "cwd": "/path/to/VSPHERE-MCP_Server",
      "env": {
        "VCENTER_HOST": "your-vcenter-host",
        "VCENTER_USER": "your-username",
        "VCENTER_PASSWORD": "your-password",
        "VCENTER_INSECURE": "true"
      }
    }
  }
}
```

Alternatively, you can use the `app/.env` file for credentials instead of inline `env` configuration.

## Tools

- `find_vm_by_mac(mac_address)` - Find a VM by its MAC address. Returns VM name, datacenter, and cluster.
