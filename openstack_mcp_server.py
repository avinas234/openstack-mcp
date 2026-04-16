"""
OpenStack MCP Server

An MCP (Model Context Protocol) server that provides comprehensive tools
to interact with OpenStack cloud infrastructure via SSH. It exposes
read-only OpenStack CLI operations as tools for AI assistants.

Supported OpenStack Services:
    - Nova (Compute): VMs, flavors, hypervisors, compute services
    - Neutron (Network): Networks, subnets, ports, routers, security groups
    - Keystone (Identity): Projects, users, roles, services, endpoints
    - Cinder (Block Storage): Volumes, snapshots, backups, volume types
    - Glance (Image): Images
    - Heat (Orchestration): Stacks, resources, events
    - Swift (Object Storage): Containers, objects
    - Octavia (Load Balancer): Load balancers, pools, listeners

Additional Tools:
    - Log Retrieval: Service logs, system logs, cross-service log search
    - Health & Diagnostics: Service status, endpoint checks, resource usage
    - System: Disk usage, process list, Docker containers

Installation:
    pip install -r requirements.txt

Usage:
    python openstack_mcp_server.py

Configuration:
    See .env.example for all available options.
"""

import asyncio
import os
import sys
import re
from typing import Optional, Any
from datetime import datetime
from dataclasses import dataclass
import logging
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# SSH support
try:
    import asyncssh
    SSH_AVAILABLE = True
except ImportError:
    SSH_AVAILABLE = False

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================================================
# Logging Setup
# ============================================================================

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"openstack_mcp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stderr)
    ],
    force=True
)
logger = logging.getLogger("openstack-mcp")


def safe_flush():
    """Safely flush stderr without crashing on closed pipes."""
    try:
        if hasattr(sys.stderr, 'flush'):
            sys.stderr.flush()
    except (OSError, ValueError):
        pass


logger.info("=" * 60)
logger.info("OpenStack MCP Server Starting")
logger.info(f"Log file: {log_file}")
logger.info("=" * 60)


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class OpenStackConfig:
    """Configuration for OpenStack SSH connection and credentials.

    All values can be set via environment variables or a .env file.
    No defaults contain real credentials — users must configure their own.
    """
    # SSH Connection
    host: str = os.getenv("OPENSTACK_HOST", "localhost")
    ssh_port: int = int(os.getenv("OPENSTACK_SSH_PORT", "22"))
    ssh_user: str = os.getenv("OPENSTACK_SSH_USER", "")
    ssh_password: str = os.getenv("OPENSTACK_SSH_PASSWORD", "")
    ssh_key_file: str = os.getenv("OPENSTACK_SSH_KEY_FILE", "")
    timeout: int = int(os.getenv("OPENSTACK_TIMEOUT", "60"))

    # OpenStack RC file (alternative to individual env vars below)
    # If set, this file will be sourced instead of exporting individual vars.
    rc_file: str = os.getenv("OPENSTACK_RC_FILE", "")

    # OpenStack Credentials (used when rc_file is not set)
    os_project_domain: str = os.getenv("OS_PROJECT_DOMAIN_NAME", "Default")
    os_user_domain: str = os.getenv("OS_USER_DOMAIN_NAME", "Default")
    os_project_name: str = os.getenv("OS_PROJECT_NAME", "admin")
    os_username: str = os.getenv("OS_USERNAME", "admin")
    os_password: str = os.getenv("OS_PASSWORD", "")
    os_auth_url: str = os.getenv("OS_AUTH_URL", "http://localhost:5000/v3")
    os_interface: str = os.getenv("OS_INTERFACE", "internal")
    os_endpoint_type: str = os.getenv("OS_ENDPOINT_TYPE", "internalURL")
    os_identity_api_version: str = os.getenv("OS_IDENTITY_API_VERSION", "3")
    os_region_name: str = os.getenv("OS_REGION_NAME", "RegionOne")
    os_auth_plugin: str = os.getenv("OS_AUTH_PLUGIN", "password")
    os_cacert: str = os.getenv("OS_CACERT", "/etc/ssl/certs/ca-certificates.crt")

    # Log retrieval: "docker" for Kolla-Ansible, "file" for DevStack/packaged
    log_source: str = os.getenv("OPENSTACK_LOG_SOURCE", "docker")
    log_base_path: str = os.getenv("OPENSTACK_LOG_PATH", "/var/log")


@dataclass
class CommandResult:
    """Result of a command execution."""
    success: bool
    output: str
    error: str = ""
    command: str = ""
    duration: float = 0.0


# ============================================================================
# SSH Client
# ============================================================================

class OpenStackClient:
    """SSH client for executing commands on an OpenStack controller node.

    Maintains a persistent SSH shell session so that OpenStack credentials
    are sourced once and reused for all subsequent commands.
    """

    def __init__(self, config: OpenStackConfig):
        self.config = config
        self._conn = None
        self._process = None
        self.connected = False
        self.authenticated = False
        logger.info(f"Initialized SSH client for {config.host}:{config.ssh_port}")

    async def connect(self) -> bool:
        """Establish SSH connection to the OpenStack controller."""
        if not SSH_AVAILABLE:
            logger.error(
                "asyncssh is not installed. Install it with: pip install asyncssh"
            )
            return False

        if not self.config.ssh_user:
            logger.error(
                "OPENSTACK_SSH_USER is not set. "
                "Configure it in your .env file or environment."
            )
            return False

        try:
            logger.info(
                f"Connecting via SSH to {self.config.host}:{self.config.ssh_port}..."
            )

            connect_kwargs = {
                "host": self.config.host,
                "port": self.config.ssh_port,
                "known_hosts": None,
                "username": self.config.ssh_user,
            }

            # Prefer key-based auth, fall back to password
            if self.config.ssh_key_file:
                connect_kwargs["client_keys"] = [self.config.ssh_key_file]
            elif self.config.ssh_password:
                connect_kwargs["password"] = self.config.ssh_password
            else:
                logger.error(
                    "No SSH credentials configured. "
                    "Set OPENSTACK_SSH_PASSWORD or OPENSTACK_SSH_KEY_FILE."
                )
                return False

            self._conn = await asyncio.wait_for(
                asyncssh.connect(**connect_kwargs),
                timeout=self.config.timeout
            )

            # Open persistent interactive shell
            self._process = await self._conn.create_process(
                term_type='xterm',
                encoding='utf-8'
            )

            self.connected = True
            logger.info("✓ SSH connection established")

            # Wait for initial shell prompt
            await self._read_until_prompt(timeout=10)
            return True

        except asyncio.TimeoutError:
            logger.error("SSH connection timed out")
            return False
        except Exception as e:
            logger.error(f"SSH connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        """Close the SSH connection."""
        try:
            if self._process:
                self._process.stdin.write("exit\n")
                await asyncio.sleep(0.5)
                self._process.close()
            if self._conn:
                self._conn.close()
                await asyncio.sleep(0.2)
            logger.info("✓ SSH connection closed")
        except Exception as e:
            logger.warning(f"Error during disconnect: {e}")
        finally:
            self._process = None
            self._conn = None
            self.connected = False
            self.authenticated = False

    async def _source_credentials(self) -> bool:
        """Source OpenStack credentials in the SSH session."""
        logger.info("Sourcing OpenStack credentials...")

        if self.config.rc_file:
            # Source the admin RC file directly
            await self._send_command(f"source {self.config.rc_file}")
            await self._read_until_prompt(timeout=5)
            logger.info(f"✓ Sourced credentials from {self.config.rc_file}")
        else:
            # Export individual environment variables
            env_vars = [
                f"export OS_PROJECT_DOMAIN_NAME={self.config.os_project_domain}",
                f"export OS_USER_DOMAIN_NAME={self.config.os_user_domain}",
                f"export OS_PROJECT_NAME={self.config.os_project_name}",
                f"export OS_USERNAME={self.config.os_username}",
                f"export OS_PASSWORD={self.config.os_password}",
                f"export OS_AUTH_URL={self.config.os_auth_url}",
                f"export OS_INTERFACE={self.config.os_interface}",
                f"export OS_ENDPOINT_TYPE={self.config.os_endpoint_type}",
                f"export OS_IDENTITY_API_VERSION={self.config.os_identity_api_version}",
                f"export OS_REGION_NAME={self.config.os_region_name}",
                f"export OS_AUTH_PLUGIN={self.config.os_auth_plugin}",
                f"export OS_CACERT={self.config.os_cacert}",
            ]
            for var in env_vars:
                await self._send_command(var)
            await self._read_until_prompt(timeout=5)
            logger.info("✓ OpenStack credentials exported")

        return True

    async def authenticate(self) -> bool:
        """Source OpenStack credentials and mark session as ready."""
        if not self.connected:
            logger.error("Cannot authenticate — not connected")
            return False

        try:
            logger.info("Setting up OpenStack environment...")
            await self._source_credentials()
            self.authenticated = True
            logger.info("✓ Ready for OpenStack commands")
            return True
        except Exception as e:
            logger.error(f"Authentication failed: {e}", exc_info=True)
            return False

    async def execute_command(
        self, command: str, timeout: Optional[int] = None
    ) -> CommandResult:
        """Execute a command on the remote host and return the result."""
        if not self.authenticated:
            return CommandResult(
                success=False, output="",
                error="Not authenticated — connection not established",
                command=command
            )

        start_time = datetime.now()
        cmd_timeout = timeout or self.config.timeout

        try:
            logger.info(f"Executing: {command}")
            await self._send_command(command)
            output = await self._read_until_prompt(timeout=cmd_timeout)
            cleaned = self._clean_output(output, command)
            duration = (datetime.now() - start_time).total_seconds()

            logger.info(f"✓ Completed in {duration:.2f}s ({len(cleaned)} chars)")
            return CommandResult(
                success=True, output=cleaned,
                command=command, duration=duration
            )

        except asyncio.TimeoutError:
            duration = (datetime.now() - start_time).total_seconds()
            error = f"Command timed out after {duration:.2f}s"
            logger.error(error)
            return CommandResult(
                success=False, output="", error=error,
                command=command, duration=duration
            )

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(f"Command error: {e}", exc_info=True)
            return CommandResult(
                success=False, output="", error=str(e),
                command=command, duration=duration
            )

    # ── Low-level I/O ──────────────────────────────────────────────────

    async def _send_command(self, command: str) -> None:
        """Send a command string followed by newline."""
        self._process.stdin.write(f"{command}\n")
        await asyncio.sleep(0.2)

    async def _read_until(self, pattern: str, timeout: int = 30) -> str:
        """Read output until a specific text pattern appears."""
        buffer = ""
        start = asyncio.get_event_loop().time()

        while True:
            if asyncio.get_event_loop().time() - start > timeout:
                raise asyncio.TimeoutError(f"Timeout waiting for: {pattern}")
            try:
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(4096), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            if not chunk:
                break
            buffer += chunk
            if pattern in buffer:
                return buffer

        return buffer

    async def _read_with_timeout(self, timeout: float) -> str:
        """Read all available data for a fixed duration."""
        buffer = ""
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout:
            try:
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(4096), timeout=1.0
                )
                if chunk:
                    buffer += chunk
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

        return buffer

    async def _read_until_prompt(self, timeout: int = 30) -> str:
        """Read until a shell prompt (ending with $ or #) is detected."""
        output = ""
        start = asyncio.get_event_loop().time()
        last_chunk_time = start
        no_data_timeout = 2.0

        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > timeout:
                raise asyncio.TimeoutError("Timeout waiting for shell prompt")

            try:
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(4096), timeout=1.0
                )
                if chunk:
                    output += chunk
                    last_chunk_time = asyncio.get_event_loop().time()

                    # Check for shell prompt at end of output
                    tail = output[-200:] if len(output) > 200 else output
                    lines = tail.strip().split('\n')
                    if lines:
                        last_line = lines[-1].rstrip()
                        if last_line.endswith('$') or last_line.endswith('# '):
                            await asyncio.sleep(0.3)
                            break

            except asyncio.TimeoutError:
                # No data for 1 second — check if we're done
                gap = asyncio.get_event_loop().time() - last_chunk_time

                if output:
                    lines = output.strip().split('\n')
                    if lines:
                        last_line = lines[-1].rstrip()
                        if last_line.endswith('$') or last_line.endswith('# '):
                            break

                if gap > no_data_timeout and output:
                    break

                continue

        return output

    def _clean_output(self, output: str, command: str) -> str:
        """Remove ANSI codes, echoed command, and shell prompts from output."""
        # Strip ANSI escape sequences
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        output = output.replace('\r', '')

        lines = output.split('\n')
        cleaned = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip the echoed command
            if command in line and stripped.startswith(command):
                continue
            # Skip bare shell prompts (but keep data lines that happen to
            # end with $ or # if they look like real content)
            if (stripped.endswith('$') or stripped.endswith('# ')) and len(stripped) < 80:
                continue
            cleaned.append(line.rstrip())

        return '\n'.join(cleaned).strip()


# ============================================================================
# Global Client Instance
# ============================================================================

config = OpenStackConfig()
client = OpenStackClient(config)
logger.info(f"Target: {config.host}:{config.ssh_port} (user: {config.ssh_user or '<not set>'})")


async def ensure_connection():
    """Ensure an active, authenticated SSH connection exists."""
    if not client.connected:
        logger.info("Establishing SSH connection...")
        safe_flush()
        try:
            if not await asyncio.wait_for(client.connect(), timeout=30):
                raise ConnectionError(
                    "Failed to connect to OpenStack controller via SSH. "
                    "Check OPENSTACK_HOST, OPENSTACK_SSH_PORT, and credentials."
                )
            if not await asyncio.wait_for(client.authenticate(), timeout=90):
                raise ConnectionError(
                    "Failed to source OpenStack credentials. "
                    "Check OPENSTACK_RC_FILE or OS_* environment variables."
                )
        except asyncio.TimeoutError:
            raise ConnectionError("SSH connection or authentication timed out")
    else:
        logger.debug("Reusing existing connection")


# ============================================================================
# Tool Schema Helpers
# ============================================================================

def _str(desc: str) -> dict:
    """String property schema."""
    return {"type": "string", "description": desc}


def _bool(desc: str) -> dict:
    """Boolean property schema with default False."""
    return {"type": "boolean", "description": desc, "default": False}


def _int(desc: str, default: int = None) -> dict:
    """Integer property schema."""
    d = {"type": "integer", "description": desc}
    if default is not None:
        d["default"] = default
    return d


def _tool(
    name: str, description: str,
    properties: dict = None, required: list = None
) -> Tool:
    """Create a Tool object."""
    schema = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema)


# ============================================================================
# Tool Definitions — Organized by OpenStack Service
# ============================================================================

def _get_all_tools() -> list[Tool]:
    """Return the full list of available tools."""
    tools = []

    # ── Compute (Nova) ─────────────────────────────────────────────────
    tools.extend([
        _tool("server_list", "List all compute instances (VMs)", {
            "all_projects": _bool("List from all projects (admin)"),
            "status": _str("Filter by status: ACTIVE, ERROR, SHUTOFF, BUILD"),
            "host": _str("Filter by compute host"),
            "name": _str("Filter by server name (pattern match)"),
        }),
        _tool("server_show", "Show details of a specific compute instance",
              {"server_id": _str("Server UUID or name")}, ["server_id"]),
        _tool("flavor_list", "List available VM flavors (CPU/RAM/Disk specs)"),
        _tool("flavor_show", "Show details of a specific flavor",
              {"flavor_id": _str("Flavor UUID or name")}, ["flavor_id"]),
        _tool("compute_service_list",
              "List Nova compute services and their status"),
        _tool("hypervisor_list", "List all hypervisors (compute nodes)"),
        _tool("hypervisor_show",
              "Show details and stats for a specific hypervisor",
              {"hypervisor_id": _str("Hypervisor UUID, hostname, or ID")},
              ["hypervisor_id"]),
        _tool("hypervisor_stats",
              "Show aggregate resource stats (total vCPUs, RAM, disk)"),
        _tool("server_group_list",
              "List server groups (anti-affinity / affinity)"),
        _tool("availability_zone_list",
              "List compute availability zones"),
        _tool("keypair_list", "List SSH key pairs"),
        _tool("aggregate_list", "List host aggregates"),
        _tool("migration_list", "List server migrations", {
            "status": _str("Filter by migration status"),
        }),
        _tool("usage_list",
              "Show resource usage summary for all projects"),
    ])

    # ── Network (Neutron) ──────────────────────────────────────────────
    tools.extend([
        _tool("network_list", "List all networks", {
            "external": _bool("List only external networks"),
        }),
        _tool("network_show", "Show details of a specific network",
              {"network_id": _str("Network UUID or name")}, ["network_id"]),
        _tool("subnet_list", "List all subnets", {
            "network": _str("Filter by network UUID or name"),
        }),
        _tool("subnet_show", "Show details of a specific subnet",
              {"subnet_id": _str("Subnet UUID or name")}, ["subnet_id"]),
        _tool("port_list", "List all ports", {
            "network": _str("Filter by network UUID or name"),
            "server": _str("Filter by server UUID"),
            "status": _str("Filter by status: ACTIVE, DOWN, BUILD"),
        }),
        _tool("port_show", "Show details of a specific port",
              {"port_id": _str("Port UUID")}, ["port_id"]),
        _tool("router_list", "List all routers"),
        _tool("router_show", "Show details of a specific router",
              {"router_id": _str("Router UUID or name")}, ["router_id"]),
        _tool("floating_ip_list", "List all floating IPs"),
        _tool("security_group_list", "List all security groups", {
            "project": _str("Filter by project UUID or name"),
        }),
        _tool("security_group_rule_list", "List security group rules", {
            "security_group": _str("Security group UUID or name to list rules for"),
        }),
        _tool("network_agent_list",
              "List all Neutron agents and their status"),
        _tool("network_trunk_list", "List network trunks"),
    ])

    # ── Identity (Keystone) ────────────────────────────────────────────
    tools.extend([
        _tool("project_list", "List all projects/tenants", {
            "domain": _str("Filter by domain UUID or name"),
        }),
        _tool("project_show", "Show details of a specific project",
              {"project_id": _str("Project UUID or name")}, ["project_id"]),
        _tool("user_list", "List all users", {
            "project": _str("Filter by project UUID or name"),
            "domain": _str("Filter by domain UUID or name"),
        }),
        _tool("user_show", "Show details of a specific user",
              {"user_id": _str("User UUID or name")}, ["user_id"]),
        _tool("role_list", "List all roles"),
        _tool("role_assignment_list", "List role assignments", {
            "project": _str("Filter by project UUID or name"),
            "user": _str("Filter by user UUID or name"),
        }),
        _tool("service_catalog_list",
              "List all registered OpenStack services"),
        _tool("endpoint_list", "List all service endpoints", {
            "service": _str("Filter by service name or UUID"),
        }),
        _tool("domain_list", "List all Keystone domains"),
        _tool("token_issue",
              "Issue a new authentication token (verifies credentials)"),
    ])

    # ── Block Storage (Cinder) ─────────────────────────────────────────
    tools.extend([
        _tool("volume_list", "List all block storage volumes", {
            "all_projects": _bool("List from all projects (admin)"),
            "status": _str("Filter by status: available, in-use, error"),
        }),
        _tool("volume_show", "Show details of a specific volume",
              {"volume_id": _str("Volume UUID or name")}, ["volume_id"]),
        _tool("volume_snapshot_list", "List all volume snapshots", {
            "all_projects": _bool("List from all projects (admin)"),
        }),
        _tool("volume_type_list", "List available volume types"),
        _tool("volume_backup_list", "List all volume backups"),
        _tool("volume_service_list",
              "List Cinder storage services and their status"),
    ])

    # ── Image (Glance) ─────────────────────────────────────────────────
    tools.extend([
        _tool("image_list", "List all available images", {
            "status": _str("Filter: active, queued, saving, deactivated"),
        }),
        _tool("image_show", "Show details of a specific image",
              {"image_id": _str("Image UUID or name")}, ["image_id"]),
    ])

    # ── Orchestration (Heat) ───────────────────────────────────────────
    tools.extend([
        _tool("stack_list", "List all Heat orchestration stacks"),
        _tool("stack_show", "Show details of a specific stack",
              {"stack_id": _str("Stack UUID or name")}, ["stack_id"]),
        _tool("stack_resource_list", "List resources in a Heat stack",
              {"stack_id": _str("Stack UUID or name")}, ["stack_id"]),
        _tool("stack_event_list", "List events for a Heat stack",
              {"stack_id": _str("Stack UUID or name")}, ["stack_id"]),
    ])

    # ── Object Storage (Swift) ─────────────────────────────────────────
    tools.extend([
        _tool("container_list",
              "List all Swift object storage containers"),
        _tool("object_list", "List objects in a Swift container",
              {"container": _str("Container name")}, ["container"]),
    ])

    # ── Load Balancer (Octavia) ────────────────────────────────────────
    tools.extend([
        _tool("loadbalancer_list", "List all load balancers"),
        _tool("loadbalancer_show",
              "Show details of a specific load balancer",
              {"lb_id": _str("Load balancer UUID or name")}, ["lb_id"]),
        _tool("lb_listener_list", "List all load balancer listeners"),
        _tool("lb_pool_list", "List all load balancer pools"),
        _tool("lb_member_list",
              "List members of a load balancer pool",
              {"pool_id": _str("Pool UUID or name")}, ["pool_id"]),
        _tool("lb_healthmonitor_list",
              "List all load balancer health monitors"),
    ])

    # ── Log Retrieval ──────────────────────────────────────────────────
    tools.extend([
        _tool("get_service_logs",
              "Retrieve recent logs for a specific OpenStack service. "
              "Supports both Docker container logs (Kolla-Ansible) and "
              "file-based logs (DevStack/packaged).", {
            "service": _str(
                "Service name, e.g.: nova-api, nova-compute, nova-scheduler, "
                "nova-conductor, neutron-server, neutron-l3-agent, "
                "neutron-dhcp-agent, keystone, glance-api, cinder-api, "
                "cinder-volume, heat-api, heat-engine, horizon, "
                "placement-api, octavia-api, rabbitmq, mariadb, "
                "memcached, haproxy"),
            "lines": _int("Number of recent log lines to retrieve", 100),
            "grep_pattern": _str(
                "Optional pattern to filter log lines (case-insensitive)"),
        }, ["service"]),
        _tool("get_system_logs",
              "Retrieve system-level logs (journald, syslog, dmesg)", {
            "source": _str(
                "Log source: journal (default), syslog, or dmesg"),
            "lines": _int("Number of log lines to retrieve", 100),
            "grep_pattern": _str("Optional filter pattern"),
            "unit": _str(
                "journald: filter by systemd unit (e.g., docker, sshd)"),
            "priority": _str(
                "journald: filter by priority (emerg, alert, crit, err, "
                "warning, notice, info, debug)"),
            "since": _str(
                "journald: show entries since (e.g., '1 hour ago')"),
        }),
        _tool("search_logs",
              "Search across multiple OpenStack service logs for a "
              "pattern. Returns matching lines from each service.", {
            "pattern": _str("Search pattern (grep regex)"),
            "services": _str(
                "Comma-separated service names to search "
                "(default: core services)"),
            "lines": _int("Max matching lines per service", 50),
        }, ["pattern"]),
    ])

    # ── Health & Diagnostics ───────────────────────────────────────────
    tools.extend([
        _tool("service_status",
              "Check the running status of OpenStack services "
              "(Docker containers or systemd units)", {
            "service": _str(
                "Specific service to check (optional — default: all)"),
        }),
        _tool("check_endpoints",
              "List all registered OpenStack service endpoints"),
        _tool("resource_usage",
              "Show compute resource usage: vCPUs, RAM, disk across "
              "all hypervisors"),
        _tool("get_quota",
              "Show quota limits and usage for a project", {
            "project": _str(
                "Project UUID or name (default: current project)"),
        }),
        _tool("network_diagnostics",
              "Run basic network diagnostics (ping, port check)", {
            "target": _str("Hostname or IP to test connectivity to"),
            "port": _int("TCP port to test (optional)"),
        }),
    ])

    # ── System ─────────────────────────────────────────────────────────
    tools.extend([
        _tool("disk_usage", "Check disk usage on the controller node"),
        _tool("process_list",
              "List running OpenStack-related processes"),
        _tool("docker_ps",
              "List running Docker containers (containerized deployments)", {
            "filter_name": _str("Filter containers by name pattern"),
        }),
    ])

    # ── General ────────────────────────────────────────────────────────
    tools.extend([
        _tool("execute_openstack_command",
              "Execute a custom read-only OpenStack CLI command. "
              "Destructive commands (delete, create, update, set, etc.) "
              "are blocked for safety.",
              {"command": _str("The CLI command to execute")},
              ["command"]),
    ])

    return tools


# ============================================================================
# Command Builders — map tool names to CLI command strings
# ============================================================================

def _cmd(base: str, args: dict, flags: dict = None, pos: str = None) -> str:
    """Build a CLI command from a base command, arguments, and flag mappings.

    Args:
        base:  Base command string (e.g. "openstack server list")
        args:  Tool call arguments dict
        flags: {arg_name: "--cli-flag"} — bool args become bare flags,
               string/int args become "--flag value"
        pos:   Argument key whose value is appended as a positional arg
    """
    parts = [base]
    if pos and pos in args:
        parts.append(str(args[pos]))
    if flags:
        for key, flag in flags.items():
            val = args.get(key)
            if val is True:
                parts.append(flag)
            elif val and val is not False:
                parts.append(f"{flag} {val}")
    return " ".join(parts)


# Tool name → function(args) → CLI command string.
# Tools NOT listed here are handled by SPECIAL_HANDLERS or inline logic.
COMMAND_BUILDERS = {
    # ── Compute (Nova) ─────────────────────────────────────────────────
    "server_list": lambda a: _cmd("openstack server list", a,
        flags={"all_projects": "--all-projects", "status": "--status",
               "host": "--host", "name": "--name"}),
    "server_show": lambda a: _cmd(
        "openstack server show", a, pos="server_id"),
    "flavor_list": lambda a: "openstack flavor list",
    "flavor_show": lambda a: _cmd(
        "openstack flavor show", a, pos="flavor_id"),
    "compute_service_list": lambda a: "openstack compute service list",
    "hypervisor_list": lambda a: "openstack hypervisor list",
    "hypervisor_show": lambda a: _cmd(
        "openstack hypervisor show", a, pos="hypervisor_id"),
    "hypervisor_stats": lambda a: "openstack hypervisor stats show",
    "server_group_list": lambda a: "openstack server group list",
    "availability_zone_list": lambda a: "openstack availability zone list",
    "keypair_list": lambda a: "openstack keypair list",
    "aggregate_list": lambda a: "openstack aggregate list",
    "migration_list": lambda a: _cmd("openstack server migration list", a,
        flags={"status": "--status"}),
    "usage_list": lambda a: "openstack usage list",

    # ── Network (Neutron) ──────────────────────────────────────────────
    "network_list": lambda a: _cmd("openstack network list", a,
        flags={"external": "--external"}),
    "network_show": lambda a: _cmd(
        "openstack network show", a, pos="network_id"),
    "subnet_list": lambda a: _cmd("openstack subnet list", a,
        flags={"network": "--network"}),
    "subnet_show": lambda a: _cmd(
        "openstack subnet show", a, pos="subnet_id"),
    "port_list": lambda a: _cmd("openstack port list", a,
        flags={"network": "--network", "server": "--server",
               "status": "--status"}),
    "port_show": lambda a: _cmd("openstack port show", a, pos="port_id"),
    "router_list": lambda a: "openstack router list",
    "router_show": lambda a: _cmd(
        "openstack router show", a, pos="router_id"),
    "floating_ip_list": lambda a: "openstack floating ip list",
    "security_group_list": lambda a: _cmd(
        "openstack security group list", a, flags={"project": "--project"}),
    "security_group_rule_list": lambda a: _cmd(
        "openstack security group rule list", a, pos="security_group"),
    "network_agent_list": lambda a: "openstack network agent list",
    "network_trunk_list": lambda a: "openstack network trunk list",

    # ── Identity (Keystone) ────────────────────────────────────────────
    "project_list": lambda a: _cmd("openstack project list", a,
        flags={"domain": "--domain"}),
    "project_show": lambda a: _cmd(
        "openstack project show", a, pos="project_id"),
    "user_list": lambda a: _cmd("openstack user list", a,
        flags={"project": "--project", "domain": "--domain"}),
    "user_show": lambda a: _cmd("openstack user show", a, pos="user_id"),
    "role_list": lambda a: "openstack role list",
    "role_assignment_list": lambda a: _cmd(
        "openstack role assignment list", a,
        flags={"project": "--project", "user": "--user"}),
    "service_catalog_list": lambda a: "openstack service list",
    "endpoint_list": lambda a: _cmd("openstack endpoint list", a,
        flags={"service": "--service"}),
    "domain_list": lambda a: "openstack domain list",
    "token_issue": lambda a: "openstack token issue",

    # ── Block Storage (Cinder) ─────────────────────────────────────────
    "volume_list": lambda a: _cmd("openstack volume list", a,
        flags={"all_projects": "--all-projects", "status": "--status"}),
    "volume_show": lambda a: _cmd(
        "openstack volume show", a, pos="volume_id"),
    "volume_snapshot_list": lambda a: _cmd(
        "openstack volume snapshot list", a,
        flags={"all_projects": "--all-projects"}),
    "volume_type_list": lambda a: "openstack volume type list",
    "volume_backup_list": lambda a: "openstack volume backup list",
    "volume_service_list": lambda a: "openstack volume service list",

    # ── Image (Glance) ─────────────────────────────────────────────────
    "image_list": lambda a: _cmd("openstack image list", a,
        flags={"status": "--status"}),
    "image_show": lambda a: _cmd(
        "openstack image show", a, pos="image_id"),

    # ── Orchestration (Heat) ───────────────────────────────────────────
    "stack_list": lambda a: "openstack stack list",
    "stack_show": lambda a: _cmd(
        "openstack stack show", a, pos="stack_id"),
    "stack_resource_list": lambda a: _cmd(
        "openstack stack resource list", a, pos="stack_id"),
    "stack_event_list": lambda a: _cmd(
        "openstack stack event list", a, pos="stack_id"),

    # ── Object Storage (Swift) ─────────────────────────────────────────
    "container_list": lambda a: "openstack container list",
    "object_list": lambda a: _cmd(
        "openstack object list", a, pos="container"),

    # ── Load Balancer (Octavia) ────────────────────────────────────────
    "loadbalancer_list": lambda a: "openstack loadbalancer list",
    "loadbalancer_show": lambda a: _cmd(
        "openstack loadbalancer show", a, pos="lb_id"),
    "lb_listener_list": lambda a: "openstack loadbalancer listener list",
    "lb_pool_list": lambda a: "openstack loadbalancer pool list",
    "lb_member_list": lambda a: _cmd(
        "openstack loadbalancer member list", a, pos="pool_id"),
    "lb_healthmonitor_list":
        lambda a: "openstack loadbalancer healthmonitor list",

    # ── Simple system commands ─────────────────────────────────────────
    "disk_usage": lambda a: "df -h",
}


# ============================================================================
# Special Handlers — tools requiring custom logic beyond a single CLI command
# ============================================================================

# Docker container names for Kolla-Ansible deployments
LOG_CONTAINERS = {
    "nova-api": "nova_api",
    "nova-compute": "nova_compute",
    "nova-scheduler": "nova_scheduler",
    "nova-conductor": "nova_conductor",
    "nova-novncproxy": "nova_novncproxy",
    "neutron-server": "neutron_server",
    "neutron-l3-agent": "neutron_l3_agent",
    "neutron-dhcp-agent": "neutron_dhcp_agent",
    "neutron-metadata-agent": "neutron_metadata_agent",
    "neutron-openvswitch-agent": "neutron_openvswitch_agent",
    "neutron-linuxbridge-agent": "neutron_linuxbridge_agent",
    "keystone": "keystone",
    "keystone-public": "keystone_public",
    "keystone-admin": "keystone_admin",
    "glance-api": "glance_api",
    "cinder-api": "cinder_api",
    "cinder-scheduler": "cinder_scheduler",
    "cinder-volume": "cinder_volume",
    "cinder-backup": "cinder_backup",
    "heat-api": "heat_api",
    "heat-engine": "heat_engine",
    "heat-api-cfn": "heat_api_cfn",
    "horizon": "horizon",
    "placement-api": "placement_api",
    "octavia-api": "octavia_api",
    "octavia-worker": "octavia_worker",
    "octavia-housekeeping": "octavia_housekeeping",
    "octavia-health-manager": "octavia_health_manager",
    "rabbitmq": "rabbitmq",
    "mariadb": "mariadb",
    "memcached": "memcached",
    "haproxy": "haproxy",
    "openvswitch-db": "openvswitch_db",
    "openvswitch-vswitchd": "openvswitch_vswitchd",
}

# Log file paths for non-containerized (DevStack/packaged) deployments
LOG_FILES = {
    "nova-api": "/var/log/nova/nova-api.log",
    "nova-compute": "/var/log/nova/nova-compute.log",
    "nova-scheduler": "/var/log/nova/nova-scheduler.log",
    "nova-conductor": "/var/log/nova/nova-conductor.log",
    "neutron-server": "/var/log/neutron/neutron-server.log",
    "neutron-l3-agent": "/var/log/neutron/l3-agent.log",
    "neutron-dhcp-agent": "/var/log/neutron/dhcp-agent.log",
    "neutron-metadata-agent": "/var/log/neutron/metadata-agent.log",
    "neutron-openvswitch-agent": "/var/log/neutron/openvswitch-agent.log",
    "keystone": "/var/log/keystone/keystone.log",
    "glance-api": "/var/log/glance/glance-api.log",
    "cinder-api": "/var/log/cinder/cinder-api.log",
    "cinder-scheduler": "/var/log/cinder/cinder-scheduler.log",
    "cinder-volume": "/var/log/cinder/cinder-volume.log",
    "heat-api": "/var/log/heat/heat-api.log",
    "heat-engine": "/var/log/heat/heat-engine.log",
    "horizon": "/var/log/horizon/horizon.log",
    "placement-api": "/var/log/placement/placement-api.log",
    "octavia-api": "/var/log/octavia/octavia-api.log",
}


async def _handle_get_service_logs(args: dict) -> CommandResult:
    """Retrieve logs for a specific OpenStack service."""
    service = args["service"]
    lines = args.get("lines", 100)
    grep_pattern = args.get("grep_pattern")

    if config.log_source == "docker":
        container = LOG_CONTAINERS.get(service, service.replace("-", "_"))
        cmd = f"docker logs {container} --tail {lines} 2>&1"
    else:
        default_path = (
            f"{config.log_base_path}/{service.split('-')[0]}/{service}.log"
        )
        log_file = LOG_FILES.get(service, default_path)
        cmd = f"tail -n {lines} {log_file} 2>&1"

    if grep_pattern:
        cmd += f" | grep -i '{grep_pattern}'"

    return await client.execute_command(cmd, timeout=30)


async def _handle_get_system_logs(args: dict) -> CommandResult:
    """Retrieve system-level logs."""
    source = args.get("source", "journal")
    lines = args.get("lines", 100)
    grep_pattern = args.get("grep_pattern")

    if source == "dmesg":
        cmd = f"dmesg | tail -n {lines}"
    elif source == "syslog":
        cmd = (
            f"tail -n {lines} /var/log/syslog 2>/dev/null || "
            f"tail -n {lines} /var/log/messages"
        )
    else:  # journal (default)
        cmd = f"journalctl -n {lines} --no-pager"
        if args.get("unit"):
            cmd += f" -u {args['unit']}"
        if args.get("priority"):
            cmd += f" -p {args['priority']}"
        if args.get("since"):
            cmd += f" --since '{args['since']}'"

    if grep_pattern:
        cmd += f" | grep -i '{grep_pattern}'"

    return await client.execute_command(cmd, timeout=30)


async def _handle_search_logs(args: dict) -> CommandResult:
    """Search across multiple OpenStack service logs for a pattern."""
    pattern = args["pattern"]
    services_str = args.get("services", "")
    max_lines = args.get("lines", 50)

    if services_str:
        services = [s.strip() for s in services_str.split(",")]
    else:
        services = [
            "nova-api", "nova-compute", "nova-scheduler",
            "neutron-server", "neutron-l3-agent",
            "keystone", "glance-api", "cinder-api",
        ]

    cmds = []
    for svc in services:
        if config.log_source == "docker":
            container = LOG_CONTAINERS.get(svc, svc.replace("-", "_"))
            cmds.append(
                f"echo '=== {svc} ===' && "
                f"docker logs {container} --tail 500 2>&1 | "
                f"grep -i '{pattern}' | tail -n {max_lines}"
            )
        else:
            default_path = (
                f"{config.log_base_path}/{svc.split('-')[0]}/{svc}.log"
            )
            log_path = LOG_FILES.get(svc, default_path)
            cmds.append(
                f"echo '=== {svc} ===' && "
                f"grep -i '{pattern}' {log_path} 2>/dev/null | "
                f"tail -n {max_lines}"
            )

    cmd = " ; ".join(cmds)
    return await client.execute_command(cmd, timeout=60)


async def _handle_service_status(args: dict) -> CommandResult:
    """Check the running status of OpenStack services."""
    specific = args.get("service")

    if config.log_source == "docker":
        if specific:
            container = LOG_CONTAINERS.get(
                specific, specific.replace("-", "_"))
            cmd = (
                f"docker ps -a --filter name={container} "
                f"--format 'table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}'"
            )
        else:
            cmd = "docker ps -a --format 'table {{.Names}}\\t{{.Status}}' | head -100"
    else:
        if specific:
            cmd = f"systemctl status {specific} --no-pager -l"
        else:
            cmd = (
                "systemctl list-units "
                "'openstack-*' 'nova-*' 'neutron-*' 'keystone*' "
                "'glance-*' 'cinder-*' 'heat-*' 'swift-*' 'octavia-*' "
                "--no-pager"
            )

    return await client.execute_command(cmd, timeout=30)


async def _handle_check_endpoints(args: dict) -> CommandResult:
    """List all registered OpenStack service endpoints."""
    cmd = "openstack endpoint list"
    return await client.execute_command(cmd, timeout=30)


async def _handle_resource_usage(args: dict) -> CommandResult:
    """Show compute resource usage across all hypervisors."""
    cmd = (
        "openstack hypervisor stats show && "
        "echo '\\n--- Per-Hypervisor ---' && "
        "openstack hypervisor list --long"
    )
    return await client.execute_command(cmd, timeout=30)


async def _handle_get_quota(args: dict) -> CommandResult:
    """Show quota limits and usage for a project."""
    project = args.get("project", "")
    cmd = f"openstack quota show {project}" if project else "openstack quota show"
    return await client.execute_command(cmd, timeout=30)


async def _handle_network_diagnostics(args: dict) -> CommandResult:
    """Run basic network diagnostics."""
    target = args.get("target", "")
    port = args.get("port")

    cmds = []
    if target:
        cmds.append(f"ping -c 3 -W 3 {target}")
        if port:
            cmds.append(
                f"timeout 5 bash -c '</dev/tcp/{target}/{port}' 2>&1 && "
                f"echo 'Port {port} is OPEN' || "
                f"echo 'Port {port} is CLOSED/FILTERED'"
            )
    else:
        cmds.append("ip addr show | grep 'inet '")
        cmds.append("ip route show | head -20")

    cmd = " ; echo '---' ; ".join(cmds)
    return await client.execute_command(cmd, timeout=30)


async def _handle_process_list(args: dict) -> CommandResult:
    """List running OpenStack-related processes."""
    cmd = (
        "ps aux | grep -E "
        "'(nova|neutron|keystone|glance|cinder|heat|swift|octavia|"
        "placement|horizon|rabbitmq|mariadb|mysql|memcached|haproxy|"
        "httpd|apache)' | grep -v grep | head -80"
    )
    return await client.execute_command(cmd, timeout=15)


async def _handle_docker_ps(args: dict) -> CommandResult:
    """List running Docker containers."""
    cmd = "docker ps"
    filter_name = args.get("filter_name")
    if filter_name:
        cmd += f" --filter name={filter_name}"
    return await client.execute_command(cmd, timeout=15)


# Maps tool names to their async handler functions.
SPECIAL_HANDLERS = {
    "get_service_logs": _handle_get_service_logs,
    "get_system_logs": _handle_get_system_logs,
    "search_logs": _handle_search_logs,
    "service_status": _handle_service_status,
    "check_endpoints": _handle_check_endpoints,
    "resource_usage": _handle_resource_usage,
    "get_quota": _handle_get_quota,
    "network_diagnostics": _handle_network_diagnostics,
    "process_list": _handle_process_list,
    "docker_ps": _handle_docker_ps,
}


# ============================================================================
# Dangerous Keywords — blocked in read-only mode
# ============================================================================

DANGEROUS_KEYWORDS = [
    "delete", "remove", "destroy", "terminate", "purge",
    "create", "add", "boot", "launch",
    "update", "set", "unset", "modify", "patch",
    "reboot", "rebuild", "resize", "migrate", "evacuate", "shelve",
    "pause", "unpause", "suspend", "resume", "stop", "start",
    "attach", "detach",
    "rm ", "rm -", "mkfs", "dd if=", "shutdown", "poweroff", "halt",
    "> /", ">> /", "tee ",
]


# ============================================================================
# MCP Server
# ============================================================================

app = Server("openstack-mcp")
logger.info("MCP Server 'openstack-mcp' initialized")

_tools_cache = None


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List all available OpenStack tools."""
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = _get_all_tools()
        logger.info(f"Registered {len(_tools_cache)} tools")
    return _tools_cache


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool invocations."""
    logger.info(f"Tool call: {name} | args: {arguments}")
    safe_flush()

    try:
        await ensure_connection()

        # ── Route to the right handler ─────────────────────────────────
        if name in COMMAND_BUILDERS:
            cmd = COMMAND_BUILDERS[name](arguments)
            result = await client.execute_command(cmd)

        elif name in SPECIAL_HANDLERS:
            result = await SPECIAL_HANDLERS[name](arguments)

        elif name == "execute_openstack_command":
            command = arguments["command"]
            # Block destructive commands (read-only mode)
            cmd_lower = command.lower()
            if any(kw in cmd_lower for kw in DANGEROUS_KEYWORDS):
                return [TextContent(
                    type="text",
                    text=(
                        "⛔ Blocked: Command contains a destructive keyword. "
                        "This server operates in read-only mode.\n"
                        f"Command: {command}"
                    )
                )]
            result = await client.execute_command(command)

        else:
            return [TextContent(
                type="text", text=f"Unknown tool: {name}"
            )]

        # ── Format response ────────────────────────────────────────────
        if result.success:
            output = result.output or "(no output)"
            response = (
                f"Command: {result.command}\n"
                f"Duration: {result.duration:.2f}s\n"
                f"\nOutput:\n{output}"
            )
            if result.error:
                response += f"\nWarning: {result.error}"
        else:
            response = (
                f"Command failed: {result.command}\n"
                f"Error: {result.error}"
            )
            if result.output:
                response += f"\n\nPartial output:\n{result.output}"

        return [TextContent(type="text", text=response)]

    except Exception as e:
        error_msg = f"Error executing '{name}': {str(e)[:500]}"
        logger.error(error_msg, exc_info=True)
        safe_flush()
        return [TextContent(type="text", text=error_msg)]


# ============================================================================
# Entry Point
# ============================================================================

async def main():
    """Run the MCP server."""
    logger.info("Starting MCP server...")
    try:
        async with stdio_server() as (read_stream, write_stream):
            logger.info("stdio transport ready — serving tools")
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
    finally:
        logger.info("Shutting down...")
        await client.disconnect()
        logger.info("Server shutdown complete")


if __name__ == "__main__":
    logger.info("OpenStack MCP Server starting")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
