# OpenStack MCP Server

An MCP (Model Context Protocol) server that provides **70+ read-only tools** to interact with OpenStack cloud infrastructure via SSH. Designed for AI assistants (Claude, etc.) to query and diagnose OpenStack environments.

## Supported OpenStack Services

| Service | Category | Tools |
|---------|----------|-------|
| **Nova** | Compute | List/show servers, flavors, hypervisors, aggregates, migrations, services |
| **Neutron** | Network | List/show networks, subnets, ports, routers, floating IPs, security groups, agents |
| **Keystone** | Identity | List/show projects, users, roles, services, endpoints, domains |
| **Cinder** | Block Storage | List/show volumes, snapshots, backups, volume types, services |
| **Glance** | Image | List/show images |
| **Heat** | Orchestration | List/show stacks, resources, events |
| **Swift** | Object Storage | List containers and objects |
| **Octavia** | Load Balancer | List/show load balancers, pools, listeners, health monitors |

## Additional Tools

| Category | Tools |
|----------|-------|
| **Log Retrieval** | Service logs, system logs (journald/syslog/dmesg), cross-service log search |
| **Health & Diagnostics** | Service status, endpoint checks, resource usage, quota checks, network diagnostics |
| **System** | Disk usage, process list, Docker container list |
| **General** | Execute any custom read-only OpenStack CLI command |

## Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/YOUR_USERNAME/openstack-mcp.git
cd openstack-mcp
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: .\venv\Scripts\Activate.ps1  # Windows
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your OpenStack controller SSH credentials
```

**Minimum required settings:**
- `OPENSTACK_HOST` — IP or hostname of your OpenStack controller
- `OPENSTACK_SSH_USER` — SSH username
- `OPENSTACK_SSH_PASSWORD` or `OPENSTACK_SSH_KEY_FILE` — SSH auth
- `OPENSTACK_RC_FILE` or individual `OS_*` variables — OpenStack credentials

### 3. Run

```bash
python openstack_mcp_server.py
```

### 4. Test configuration (optional)

```bash
python test_server_config.py
```

## Using with Claude Desktop

1. Add the following to your Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json` on Windows, `~/Library/Application Support/Claude/claude_desktop_config.json` on Mac):

```json
{
  "mcpServers": {
    "openstack-mcp": {
      "command": "python",
      "args": ["/path/to/openstack-mcp/openstack_mcp_server.py"],
      "env": {
        "OPENSTACK_HOST": "your-controller-ip",
        "OPENSTACK_SSH_USER": "your-ssh-user",
        "OPENSTACK_SSH_PASSWORD": "your-ssh-password",
        "OPENSTACK_RC_FILE": "/etc/kolla/admin-openrc.sh"
      }
    }
  }
}
```

2. Restart Claude Desktop
3. The OpenStack tools will appear in Claude Desktop's tool list

See [CLAUDE_DESKTOP_SETUP.md](CLAUDE_DESKTOP_SETUP.md) for detailed instructions.

## Configuration Reference

### SSH Connection

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSTACK_HOST` | Controller hostname/IP | `localhost` |
| `OPENSTACK_SSH_PORT` | SSH port | `22` |
| `OPENSTACK_SSH_USER` | SSH username | *(required)* |
| `OPENSTACK_SSH_PASSWORD` | SSH password | *(empty)* |
| `OPENSTACK_SSH_KEY_FILE` | SSH private key path | *(empty)* |
| `OPENSTACK_TIMEOUT` | Command timeout (seconds) | `60` |

### OpenStack Credentials

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSTACK_RC_FILE` | Path to admin-openrc.sh on the controller | *(empty)* |
| `OS_AUTH_URL` | Keystone auth URL | `http://localhost:5000/v3` |
| `OS_USERNAME` | OpenStack admin username | `admin` |
| `OS_PASSWORD` | OpenStack admin password | *(required)* |
| `OS_PROJECT_NAME` | Default project | `admin` |
| `OS_REGION_NAME` | Region name | `RegionOne` |
| `OS_IDENTITY_API_VERSION` | Identity API version | `3` |
| `OS_INTERFACE` | API interface type | `internal` |
| `OS_CACERT` | CA certificate path | `/etc/ssl/certs/ca-certificates.crt` |

### Log Retrieval

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSTACK_LOG_SOURCE` | `docker` (Kolla) or `file` (DevStack) | `docker` |
| `OPENSTACK_LOG_PATH` | Base path for log files | `/var/log` |

## Architecture

```
┌─────────────────┐     SSH      ┌──────────────────────┐
│  AI Assistant    │◄────────────►│  OpenStack Controller│
│  (Claude, etc.)  │   via MCP    │  (nova, neutron,     │
│                  │              │   keystone, etc.)     │
│  ┌────────────┐  │              │                      │
│  │ MCP Client │──┼──stdio───►┌─┴─────────────────┐    │
│  └────────────┘  │           │ openstack_mcp_server│    │
└─────────────────┘           │ (this project)      │    │
                              │                     │    │
                              │ SSH ────────────────►│    │
                              └─────────────────────┘    │
                                                         │
                              ┌───────────────────────────┘
                              │ openstack server list
                              │ docker logs nova_api
                              │ journalctl -u nova
                              └─── CLI commands executed
```

## Security

- The server operates in **read-only mode** — destructive commands (`delete`, `create`, `update`, `set`, `boot`, `reboot`, etc.) are blocked
- SSH credentials should be kept secure — use `.env` files (never commit) or environment variables
- The `.env` file is listed in `.gitignore`
- Consider using SSH key-based authentication instead of passwords

## Logs

Server logs are stored in the `logs/` directory with timestamps. Each run creates a new log file.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **Connection fails** | Check `OPENSTACK_HOST`, `OPENSTACK_SSH_PORT`, and SSH credentials |
| **Auth fails** | Verify `OPENSTACK_RC_FILE` path or `OS_*` variables |
| **Commands timeout** | Increase `OPENSTACK_TIMEOUT` |
| **OpenStack commands fail** | Ensure the `openstack` CLI is installed on the controller |
| **asyncssh not found** | Run `pip install asyncssh` |

## License

This project is licensed under the Apache License 2.0 — see [LICENSE](LICENSE) for details.
