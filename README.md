# Background Process Daemon

A Python-based daemon tool for managing background processes through Unix sockets. Perfect for Docker containers and development environments where you need to run long-running scripts without screen/tmux.

## Features

- 🔄 **Persistent Background Processes** - Survive container disconnections
- 🏠 **Multiple Isolated Instances** - Run unlimited daemon instances without conflicts
- 📊 **Process Management** - Start, stop, monitor, and control processes
- 📝 **Automatic Logging** - Each process logs to separate files with rotation
- 🔌 **Socket Communication** - Clean API without file-based coordination
- 🛡️ **Safe Shutdown** - Graceful termination with fallback to force kill
- 🧹 **Resource Cleanup** - Automatic cleanup of finished processes

## Installation

No additional dependencies beyond Python standard library:

```bash
# Make executable
chmod +x daemon_tool.py

# Optional: Add to PATH
cp daemon_tool.py /usr/local/bin/daemon-tool
```

## Quick Start

### 1. Start a Daemon Instance

```bash
# Start default daemon instance
python daemon_tool.py daemon

# Start named instance
python daemon_tool.py --instance music daemon

# Run in foreground for debugging
python daemon_tool.py daemon --foreground
```

### 2. Run Background Processes

```bash
# Start a long-running script
python daemon_tool.py start "python my_script.py --args" --name my_job

# Start with specific working directory
python daemon_tool.py start "python process_data.py" --dir /data --name data_job

# Use named instance
python daemon_tool.py --instance music start "python lyrics_downloader.py /music" --name lyrics
```

### 3. Monitor and Control

```bash
# Check all processes
python daemon_tool.py status

# Check specific process
python daemon_tool.py status my_job

# View process logs
python daemon_tool.py log my_job --lines 100

# Follow logs in real-time
python daemon_tool.py log my_job --follow

# Stop process
python daemon_tool.py stop my_job

# Force kill if needed
python daemon_tool.py stop my_job --force
```

## Usage Examples

### Docker Container Workflow

Perfect for development in containers where you need persistent background processes:

```bash
# In your Docker container
python daemon_tool.py daemon

# Start your development processes
python daemon_tool.py start "python web_scraper.py" --name scraper
python daemon_tool.py start "python data_processor.py" --name processor
python daemon_tool.py start "python file_watcher.py /app" --name watcher

# Disconnect from container - processes keep running!
# Later, reconnect and check status:
python daemon_tool.py status
python daemon_tool.py log scraper --lines 50
```

### Multiple Isolated Instances

Organize different workflows in separate daemon instances:

```bash
# Start different instances for different purposes
python daemon_tool.py --instance media daemon
python daemon_tool.py --instance data daemon
python daemon_tool.py --instance experiments daemon

# Media processing workflow
python daemon_tool.py --instance media start "python lyrics_downloader.py /music" --name lyrics
python daemon_tool.py --instance media start "python id3_tagger.py /music" --name tagger

# Data processing workflow
python daemon_tool.py --instance data start "python etl_pipeline.py" --name etl
python daemon_tool.py --instance data start "python report_generator.py" --name reports

# Experimental scripts
python daemon_tool.py --instance experiments start "python test_algorithm.py" --name test1
python daemon_tool.py --instance experiments start "python benchmark.py" --name bench
```

### Instance Management

```bash
# List all daemon instances
python daemon_tool.py list-instances

# Output:
# Daemon instances:
#   media: RUNNING (2 processes)
#     Directory: /tmp/daemon_instances/media
#   data: RUNNING (2 processes)  
#     Directory: /tmp/daemon_instances/data
#   experiments: STOPPED
#     Directory: /tmp/daemon_instances/experiments

# Kill specific instance
python daemon_tool.py kill-instance experiments

# Cleanup finished processes
python daemon_tool.py --instance media cleanup
```

## Command Reference

### Global Options

- `--instance NAME` - Specify daemon instance name (default: "default")
- `--base-dir PATH` - Base directory for instances (default: "/tmp/daemon_instances")

### Commands

#### `daemon`
Start daemon server for the specified instance.

```bash
python daemon_tool.py [--instance NAME] daemon [--foreground]
```

Options:
- `--foreground` - Run in foreground instead of daemonizing

#### `start`
Start a new background process.

```bash
python daemon_tool.py [--instance NAME] start COMMAND [--name NAME] [--dir PATH]
```

Options:
- `--name NAME` - Custom process name (default: auto-generated)
- `--dir PATH` - Working directory for the process

#### `stop`
Stop a running process.

```bash
python daemon_tool.py [--instance NAME] stop PROCESS_ID [--force]
```

Options:
- `--force` - Force kill instead of graceful termination

#### `status`
Show process status.

```bash
python daemon_tool.py [--instance NAME] status [PROCESS_ID]
```

#### `log`
View process logs.

```bash
python daemon_tool.py [--instance NAME] log PROCESS_ID [--lines N] [--follow]
```

Options:
- `--lines N` - Number of lines to show (default: 50)
- `--follow` - Follow log output in real-time

#### `cleanup`
Remove finished processes from tracking.

```bash
python daemon_tool.py [--instance NAME] cleanup
```

#### `list-instances`
List all daemon instances and their status.

```bash
python daemon_tool.py list-instances
```

#### `kill-instance`
Kill a daemon instance and all its processes.

```bash
python daemon_tool.py kill-instance [INSTANCE_NAME]
```

## File Structure

Each daemon instance creates its own isolated directory:

```
/tmp/daemon_instances/
├── default/
│   ├── control.sock      # Unix socket for communication
│   ├── daemon.pid        # Daemon process ID
│   └── logs/             # Process log files
│       ├── my_job.log
│       └── other_job.log
├── music/
│   ├── control.sock
│   ├── daemon.pid
│   └── logs/
│       ├── lyrics.log
│       └── tagger.log
└── data/
    ├── control.sock
    ├── daemon.pid
    └── logs/
        └── etl.log
```

## Process Management Details

### Process Lifecycle

1. **Start** - Process is spawned with its own process group
2. **Monitor** - Daemon tracks PID, status, and logs output
3. **Stop** - Graceful SIGTERM, fallback to SIGKILL after 5 seconds
4. **Cleanup** - Remove finished processes from tracking

### Logging

- Each process gets its own log file: `{instance}/logs/{process_name}.log`
- Logs capture both stdout and stderr
- Automatic log rotation when files exceed 10MB
- Logs persist after process completion

### Error Handling

- Processes that fail to start are reported immediately
- Crashed processes are marked with exit codes
- Daemon continues running even if individual processes fail
- Socket communication errors are handled gracefully

## Advanced Usage

### Custom Base Directory

Use a custom location for daemon instances:

```bash
python daemon_tool.py --base-dir /var/lib/mydaemons --instance worker daemon
python daemon_tool.py --base-dir /var/lib/mydaemons --instance worker start "worker.py"
```

### Integration with Systemd

Create a systemd service for persistent daemon instances:

```ini
# /etc/systemd/system/daemon-worker.service
[Unit]
Description=Background Process Daemon - Worker Instance
After=network.target

[Service]
Type=forking
User=myuser
WorkingDirectory=/app
ExecStart=/usr/local/bin/daemon-tool --instance worker daemon
ExecStop=/usr/local/bin/daemon-tool kill-instance worker
Restart=always

[Install]
WantedBy=multi-user.target
```

### Monitoring Script

Simple monitoring script:

```bash
#!/bin/bash
# monitor_daemons.sh

echo "=== Daemon Status ==="
python daemon_tool.py list-instances

echo -e "\n=== Process Details ==="
for instance in $(python daemon_tool.py list-instances | grep "RUNNING" | cut -d: -f1); do
    echo "Instance: $instance"
    python daemon_tool.py --instance "$instance" status
    echo
done
```

## Troubleshooting

### Daemon Won't Start

```bash
# Check if already running
python daemon_tool.py list-instances

# Kill stale instance
python daemon_tool.py kill-instance default

# Check permissions
ls -la /tmp/daemon_instances/
```

### Process Not Starting

```bash
# Check daemon is running
python daemon_tool.py status

# Check recent logs
python daemon_tool.py log failed_process --lines 100

# Try running command manually
python your_script.py  # Test outside daemon
```

### Socket Connection Issues

```bash
# Check socket file exists
ls -la /tmp/daemon_instances/default/control.sock

# Verify daemon process
ps aux | grep daemon_tool.py

# Restart daemon
python daemon_tool.py kill-instance default
python daemon_tool.py daemon
```

## Limitations

- Unix/Linux only (uses Unix sockets and process groups)
- No built-in process resource limits (use systemd/cgroups for that)
- Log files grow until manual cleanup (10MB rotation per file)
- No authentication (relies on filesystem permissions)

## Contributing

Feel free to submit issues and pull requests. Areas for improvement:

- Windows support using named pipes
- Built-in resource monitoring and limits
- Web UI for process management
- Integration with container orchestrators
- Enhanced logging features

## License

MIT License - see LICENSE file for details.# Python Daemon
 a simple Python Daemon tool for managing scripts.
