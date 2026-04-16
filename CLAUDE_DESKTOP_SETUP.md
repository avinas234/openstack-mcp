# Claude Desktop Configuration Instructions

## How to Add the OpenStack MCP Server to Claude Desktop

### Step 1: Locate Claude Desktop Config File

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

**macOS:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

### Step 2: Edit the Config File

Open the config file in a text editor (create it if it doesn't exist) and add the `openstack-mcp` server entry:

```json
{
  "mcpServers": {
    "openstack-mcp": {
      "command": "python",
      "args": [
        "/absolute/path/to/openstack-mcp/openstack_mcp_server.py"
      ],
      "env": {
        "OPENSTACK_HOST": "YOUR_CONTROLLER_IP",
        "OPENSTACK_SSH_PORT": "22",
        "OPENSTACK_SSH_USER": "YOUR_SSH_USERNAME",
        "OPENSTACK_SSH_PASSWORD": "YOUR_SSH_PASSWORD",
        "OPENSTACK_RC_FILE": "/etc/kolla/admin-openrc.sh",
        "OPENSTACK_LOG_SOURCE": "docker",
        "OPENSTACK_TIMEOUT": "60"
      }
    }
  }
}
```

> **Note:** Replace all `YOUR_*` placeholders with your actual values. Update the path in `args` to the absolute path where you cloned this project.

### Alternative: Using SSH Key Authentication

```json
{
  "mcpServers": {
    "openstack-mcp": {
      "command": "python",
      "args": [
        "/absolute/path/to/openstack-mcp/openstack_mcp_server.py"
      ],
      "env": {
        "OPENSTACK_HOST": "YOUR_CONTROLLER_IP",
        "OPENSTACK_SSH_USER": "YOUR_SSH_USERNAME",
        "OPENSTACK_SSH_KEY_FILE": "/path/to/your/private/key",
        "OPENSTACK_RC_FILE": "/etc/kolla/admin-openrc.sh"
      }
    }
  }
}
```

### Alternative: Using Individual OpenStack Credentials

If you don't have an RC file on the controller, provide individual credentials:

```json
{
  "mcpServers": {
    "openstack-mcp": {
      "command": "python",
      "args": [
        "/absolute/path/to/openstack-mcp/openstack_mcp_server.py"
      ],
      "env": {
        "OPENSTACK_HOST": "YOUR_CONTROLLER_IP",
        "OPENSTACK_SSH_USER": "YOUR_SSH_USERNAME",
        "OPENSTACK_SSH_PASSWORD": "YOUR_SSH_PASSWORD",
        "OS_AUTH_URL": "http://YOUR_CONTROLLER_IP:5000/v3",
        "OS_USERNAME": "admin",
        "OS_PASSWORD": "YOUR_OPENSTACK_PASSWORD",
        "OS_PROJECT_NAME": "admin",
        "OS_PROJECT_DOMAIN_NAME": "Default",
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_IDENTITY_API_VERSION": "3",
        "OS_INTERFACE": "internal",
        "OS_ENDPOINT_TYPE": "internalURL",
        "OS_REGION_NAME": "RegionOne",
        "OS_AUTH_PLUGIN": "password",
        "OS_CACERT": "/etc/ssl/certs/ca-certificates.crt"
      }
    }
  }
}
```

### Step 3: Restart Claude Desktop

Close and reopen Claude Desktop for the configuration to take effect.

### Step 4: Verify It's Working

In Claude Desktop, you should now see OpenStack tools available. Try these prompts:

- "List all VM instances in my OpenStack cloud"
- "Show me the available networks and routers"
- "What is the status of all compute services?"
- "Get the recent logs from nova-api"
- "Show resource usage across all hypervisors"
- "Check the quota for the admin project"

## Troubleshooting

### Server Not Appearing in Claude Desktop
- Verify the path in `args` is correct and absolute
- Ensure the config file is valid JSON
- Check Claude Desktop logs for errors

### SSH Connection Errors
- Verify SSH connectivity: `ssh YOUR_SSH_USER@YOUR_CONTROLLER_IP`
- Ensure the SSH user has access to OpenStack CLI tools
- Check that `asyncssh` is installed: `pip install asyncssh`

### Command Timeouts
- Increase `OPENSTACK_TIMEOUT` (default: 60 seconds)
- Check network latency to the controller

### OpenStack Commands Fail
- Verify credentials by running `openstack token issue` manually on the controller
- Check that the `openstack` CLI client is installed on the controller
- Ensure the RC file path (`OPENSTACK_RC_FILE`) is correct

## Security Notes

- The Claude Desktop config file contains sensitive credentials — restrict file permissions
- Prefer SSH key-based authentication over passwords
- The MCP server only executes read-only commands — destructive operations are blocked
- Don't commit credentials to version control
