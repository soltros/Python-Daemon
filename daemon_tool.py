#!/usr/bin/env python3
import os
import sys
import json
import time
import signal
import socket
import subprocess
import threading
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
import tempfile
import shlex

# Configuration
DEFAULT_INSTANCE_NAME = "default"
DEFAULT_BASE_DIR = "/tmp/daemon_instances"
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB per log file
MAX_LOG_FILES = 5

def get_instance_paths(instance_name: str = DEFAULT_INSTANCE_NAME, base_dir: str = DEFAULT_BASE_DIR) -> Dict[str, str]:
    """Get paths for a specific daemon instance"""
    instance_dir = Path(base_dir) / instance_name
    instance_dir.mkdir(parents=True, exist_ok=True)

    return {
        'socket': str(instance_dir / "control.sock"),
        'pid_file': str(instance_dir / "daemon.pid"),
        'log_dir': str(instance_dir / "logs"),
        'instance_dir': str(instance_dir)
    }

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ProcessManager:
    """Manages background processes"""

    def __init__(self, log_dir: str = DEFAULT_LOG_DIR):
        self.processes: Dict[str, Dict[str, Any]] = {}
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._next_id = 1

    def start_process(self, command: str, name: str = None, working_dir: str = None) -> str:
        """Start a new background process"""
        with self._lock:
            # Generate process ID
            proc_id = name if name else f"proc_{self._next_id}"
            self._next_id += 1

            if proc_id in self.processes:
                raise ValueError(f"Process '{proc_id}' already exists")

            # Set up logging
            log_file = self.log_dir / f"{proc_id}.log"

            try:
                # Parse command
                if isinstance(command, str):
                    cmd_args = shlex.split(command)
                else:
                    cmd_args = command

                # Start process
                with open(log_file, 'w') as log_f:
                    proc = subprocess.Popen(
                        cmd_args,
                        stdout=log_f,
                        stderr=subprocess.STDOUT,
                        cwd=working_dir,
                        preexec_fn=os.setsid  # Create new process group
                    )

                # Store process info
                self.processes[proc_id] = {
                    'process': proc,
                    'command': command,
                    'started_at': datetime.now().isoformat(),
                    'log_file': str(log_file),
                    'working_dir': working_dir,
                    'status': 'running'
                }

                logger.info(f"Started process '{proc_id}' (PID: {proc.pid})")
                return proc_id

            except Exception as e:
                logger.error(f"Failed to start process '{proc_id}': {e}")
                raise

    def stop_process(self, proc_id: str, force: bool = False) -> bool:
        """Stop a process"""
        with self._lock:
            if proc_id not in self.processes:
                return False

            proc_info = self.processes[proc_id]
            proc = proc_info['process']

            if proc.poll() is not None:
                # Process already finished
                proc_info['status'] = 'finished'
                return True

            try:
                if force:
                    # Kill process group
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    # Terminate process group gracefully
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)

                    # Wait a bit for graceful shutdown
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        # Force kill if graceful shutdown failed
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

                proc_info['status'] = 'stopped'
                logger.info(f"Stopped process '{proc_id}'")
                return True

            except ProcessLookupError:
                # Process already dead
                proc_info['status'] = 'finished'
                return True
            except Exception as e:
                logger.error(f"Failed to stop process '{proc_id}': {e}")
                return False

    def get_process_status(self, proc_id: str = None) -> Dict[str, Any]:
        """Get status of one or all processes"""
        with self._lock:
            if proc_id:
                if proc_id not in self.processes:
                    return {}

                proc_info = self.processes[proc_id].copy()
                proc = proc_info['process']

                # Update status
                if proc.poll() is not None:
                    proc_info['status'] = 'finished'
                    proc_info['exit_code'] = proc.returncode

                # Remove process object from returned data
                del proc_info['process']
                return {proc_id: proc_info}
            else:
                # Return all processes
                result = {}
                for pid, proc_info in self.processes.items():
                    info = proc_info.copy()
                    proc = info['process']

                    # Update status
                    if proc.poll() is not None:
                        info['status'] = 'finished'
                        info['exit_code'] = proc.returncode

                    # Remove process object
                    del info['process']
                    result[pid] = info

                return result

    def get_process_log(self, proc_id: str, lines: int = 50) -> List[str]:
        """Get recent log lines from a process"""
        if proc_id not in self.processes:
            return []

        log_file = Path(self.processes[proc_id]['log_file'])
        if not log_file.exists():
            return []

        try:
            with open(log_file, 'r') as f:
                all_lines = f.readlines()
                return [line.rstrip() for line in all_lines[-lines:]]
        except Exception as e:
            logger.error(f"Failed to read log for '{proc_id}': {e}")
            return []

    def cleanup_finished(self) -> int:
        """Remove finished processes from tracking"""
        with self._lock:
            to_remove = []
            for proc_id, proc_info in self.processes.items():
                if proc_info['process'].poll() is not None:
                    to_remove.append(proc_id)

            for proc_id in to_remove:
                del self.processes[proc_id]

            return len(to_remove)

class DaemonServer:
    """Socket server for daemon control"""

    def __init__(self, socket_path: str, log_dir: str):
        self.socket_path = socket_path
        self.process_manager = ProcessManager(log_dir)
        self.running = False
        self.server_socket = None

    def start(self):
        """Start the daemon server"""
        # Remove existing socket
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        # Create socket
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(5)

        # Set permissions
        os.chmod(self.socket_path, 0o666)

        self.running = True
        logger.info(f"Daemon server started on {self.socket_path}")

        try:
            while self.running:
                try:
                    client_socket, _ = self.server_socket.accept()
                    threading.Thread(
                        target=self._handle_client,
                        args=(client_socket,),
                        daemon=True
                    ).start()
                except OSError:
                    if self.running:
                        logger.error("Socket error occurred")
                    break
        finally:
            self.cleanup()

    def _handle_client(self, client_socket):
        """Handle client connection"""
        try:
            # Receive request
            data = client_socket.recv(4096).decode('utf-8')
            if not data:
                return

            try:
                request = json.loads(data)
            except json.JSONDecodeError:
                self._send_response(client_socket, {'error': 'Invalid JSON'})
                return

            # Process request
            response = self._process_request(request)
            self._send_response(client_socket, response)

        except Exception as e:
            logger.error(f"Error handling client: {e}")
            self._send_response(client_socket, {'error': str(e)})
        finally:
            client_socket.close()

    def _send_response(self, client_socket, response):
        """Send JSON response to client"""
        try:
            response_data = json.dumps(response).encode('utf-8')
            client_socket.send(response_data)
        except Exception as e:
            logger.error(f"Failed to send response: {e}")

    def _process_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process client request"""
        action = request.get('action')

        try:
            if action == 'start':
                command = request.get('command')
                name = request.get('name')
                working_dir = request.get('working_dir')

                if not command:
                    return {'error': 'Command required'}

                proc_id = self.process_manager.start_process(command, name, working_dir)
                return {'success': True, 'process_id': proc_id}

            elif action == 'stop':
                proc_id = request.get('process_id')
                force = request.get('force', False)

                if not proc_id:
                    return {'error': 'Process ID required'}

                success = self.process_manager.stop_process(proc_id, force)
                return {'success': success}

            elif action == 'status':
                proc_id = request.get('process_id')
                status = self.process_manager.get_process_status(proc_id)
                return {'success': True, 'processes': status}

            elif action == 'log':
                proc_id = request.get('process_id')
                lines = request.get('lines', 50)

                if not proc_id:
                    return {'error': 'Process ID required'}

                log_lines = self.process_manager.get_process_log(proc_id, lines)
                return {'success': True, 'log': log_lines}

            elif action == 'cleanup':
                removed = self.process_manager.cleanup_finished()
                return {'success': True, 'removed': removed}

            elif action == 'ping':
                return {'success': True, 'message': 'pong'}

            else:
                return {'error': f'Unknown action: {action}'}

        except Exception as e:
            logger.error(f"Error processing request: {e}")
            return {'error': str(e)}

    def stop(self):
        """Stop the daemon server"""
        self.running = False
        if self.server_socket:
            self.server_socket.close()

    def cleanup(self):
        """Cleanup resources"""
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

class DaemonClient:
    """Client for communicating with daemon"""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def _send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to daemon"""
        try:
            client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_socket.connect(self.socket_path)

            # Send request
            request_data = json.dumps(request).encode('utf-8')
            client_socket.send(request_data)

            # Receive response
            response_data = client_socket.recv(8192).decode('utf-8')
            response = json.loads(response_data)

            client_socket.close()
            return response

        except FileNotFoundError:
            return {'error': 'Daemon not running'}
        except Exception as e:
            return {'error': str(e)}

    def start_process(self, command: str, name: str = None, working_dir: str = None) -> Dict[str, Any]:
        """Start a new process"""
        request = {
            'action': 'start',
            'command': command,
            'name': name,
            'working_dir': working_dir
        }
        return self._send_request(request)

    def stop_process(self, process_id: str, force: bool = False) -> Dict[str, Any]:
        """Stop a process"""
        request = {
            'action': 'stop',
            'process_id': process_id,
            'force': force
        }
        return self._send_request(request)

    def get_status(self, process_id: str = None) -> Dict[str, Any]:
        """Get process status"""
        request = {
            'action': 'status',
            'process_id': process_id
        }
        return self._send_request(request)

    def get_log(self, process_id: str, lines: int = 50) -> Dict[str, Any]:
        """Get process log"""
        request = {
            'action': 'log',
            'process_id': process_id,
            'lines': lines
        }
        return self._send_request(request)

    def cleanup(self) -> Dict[str, Any]:
        """Cleanup finished processes"""
        request = {'action': 'cleanup'}
        return self._send_request(request)

    def ping(self) -> Dict[str, Any]:
        """Ping daemon"""
        request = {'action': 'ping'}
        return self._send_request(request)

def write_pid_file(pid_file: str):
    """Write PID to file"""
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))

def remove_pid_file(pid_file: str):
    """Remove PID file"""
    try:
        os.unlink(pid_file)
    except OSError:
        pass

def is_daemon_running(pid_file: str) -> bool:
    """Check if daemon is already running"""
    if not os.path.exists(pid_file):
        return False

    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())

        # Check if process exists
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        # PID file is stale
        remove_pid_file(pid_file)
        return False

def main():
    parser = argparse.ArgumentParser(description='Background Process Daemon')
    parser.add_argument('--instance', default=DEFAULT_INSTANCE_NAME,
                       help='Daemon instance name (allows multiple isolated daemons)')
    parser.add_argument('--base-dir', default=DEFAULT_BASE_DIR,
                       help='Base directory for daemon instances')

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Daemon command
    daemon_parser = subparsers.add_parser('daemon', help='Start daemon server')
    daemon_parser.add_argument('--foreground', action='store_true',
                              help='Run in foreground (don\'t daemonize)')

    # Start command
    start_parser = subparsers.add_parser('start', help='Start a process')
    start_parser.add_argument('command_arg', help='Command to execute')
    start_parser.add_argument('--name', help='Process name')
    start_parser.add_argument('--dir', help='Working directory')

    # Stop command
    stop_parser = subparsers.add_parser('stop', help='Stop a process')
    stop_parser.add_argument('process_id', help='Process ID to stop')
    stop_parser.add_argument('--force', action='store_true',
                            help='Force kill process')

    # Status command
    status_parser = subparsers.add_parser('status', help='Show process status')
    status_parser.add_argument('process_id', nargs='?', help='Specific process ID')

    # Log command
    log_parser = subparsers.add_parser('log', help='Show process log')
    log_parser.add_argument('process_id', help='Process ID')
    log_parser.add_argument('--lines', type=int, default=50,
                           help='Number of lines to show')
    log_parser.add_argument('--follow', action='store_true',
                           help='Follow log output')

    # Cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Cleanup finished processes')

    # List instances command
    list_parser = subparsers.add_parser('list-instances', help='List all daemon instances')

    # Kill instance command
    kill_parser = subparsers.add_parser('kill-instance', help='Kill daemon instance')
    kill_parser.add_argument('instance_name', nargs='?', help='Instance to kill (default: current)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Get paths for this instance
    paths = get_instance_paths(args.instance, args.base_dir)

    if args.command == 'daemon':
        # Check if already running
        if is_daemon_running(paths['pid_file']):
            print(f"Daemon instance '{args.instance}' is already running")
            return

        print(f"Starting daemon instance: {args.instance}")
        print(f"Instance directory: {paths['instance_dir']}")

        # Create daemon server
        server = DaemonServer(paths['socket'], paths['log_dir'])

        def signal_handler(signum, frame):
            logger.info("Received signal, shutting down...")
            server.stop()
            remove_pid_file(paths['pid_file'])
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        if not args.foreground:
            # Daemonize
            if os.fork() > 0:
                sys.exit(0)

            os.setsid()

            if os.fork() > 0:
                sys.exit(0)

            # Redirect standard streams
            with open('/dev/null', 'r') as dev_null:
                os.dup2(dev_null.fileno(), sys.stdin.fileno())
            with open('/dev/null', 'w') as dev_null:
                os.dup2(dev_null.fileno(), sys.stdout.fileno())
                os.dup2(dev_null.fileno(), sys.stderr.fileno())

        # Write PID file
        write_pid_file(paths['pid_file'])

        try:
            server.start()
        finally:
            remove_pid_file(paths['pid_file'])

    elif args.command == 'list-instances':
        # List all instances
        base_path = Path(args.base_dir)
        if not base_path.exists():
            print("No daemon instances found")
            return

        print("Daemon instances:")
        for instance_dir in base_path.iterdir():
            if instance_dir.is_dir():
                instance_name = instance_dir.name
                instance_paths = get_instance_paths(instance_name, args.base_dir)

                if is_daemon_running(instance_paths['pid_file']):
                    status = "RUNNING"
                    # Try to get process count
                    try:
                        client = DaemonClient(instance_paths['socket'])
                        response = client.ping()
                        if response.get('success'):
                            process_response = client.get_status()
                            if process_response.get('success'):
                                process_count = len(process_response['processes'])
                                status += f" ({process_count} processes)"
                    except:
                        pass
                else:
                    status = "STOPPED"

                print(f"  {instance_name}: {status}")
                print(f"    Directory: {instance_paths['instance_dir']}")

    elif args.command == 'kill-instance':
        instance_to_kill = args.instance_name or args.instance
        kill_paths = get_instance_paths(instance_to_kill, args.base_dir)

        if not is_daemon_running(kill_paths['pid_file']):
            print(f"Daemon instance '{instance_to_kill}' is not running")
            return

        try:
            with open(kill_paths['pid_file'], 'r') as f:
                pid = int(f.read().strip())

            print(f"Killing daemon instance '{instance_to_kill}' (PID: {pid})")
            os.kill(pid, signal.SIGTERM)

            # Wait a bit for graceful shutdown
            time.sleep(2)

            # Force kill if still running
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # Process already dead

            remove_pid_file(kill_paths['pid_file'])
            print("Daemon killed")

        except Exception as e:
            print(f"Failed to kill daemon: {e}")

    else:
        # Client commands - check if daemon is running
        if not is_daemon_running(paths['pid_file']):
            print(f"Daemon instance '{args.instance}' is not running")
            print(f"Start it with: python {sys.argv[0]} --instance {args.instance} daemon")
            return

        client = DaemonClient(paths['socket'])

        if args.command == 'start':
            # Use the renamed argument to avoid conflict
            response = client.start_process(args.command_arg, args.name, args.dir)
            if response.get('success'):
                print(f"Started process: {response['process_id']} (instance: {args.instance})")
            else:
                print(f"Error: {response.get('error', 'Unknown error')}")

        elif args.command == 'stop':
            response = client.stop_process(args.process_id, args.force)
            if response.get('success'):
                print(f"Stopped process: {args.process_id} (instance: {args.instance})")
            else:
                print(f"Error: {response.get('error', 'Unknown error')}")

        elif args.command == 'status':
            response = client.get_status(args.process_id)
            if response.get('success'):
                processes = response['processes']
                if not processes:
                    print(f"No processes found in instance '{args.instance}'")
                else:
                    print(f"Processes in instance '{args.instance}':")
                    for proc_id, info in processes.items():
                        print(f"\nProcess: {proc_id}")
                        print(f"  Command: {info['command']}")
                        print(f"  Status: {info['status']}")
                        print(f"  Started: {info['started_at']}")
                        if 'exit_code' in info:
                            print(f"  Exit code: {info['exit_code']}")
                        print(f"  Log: {info['log_file']}")
            else:
                print(f"Error: {response.get('error', 'Unknown error')}")

        elif args.command == 'log':
            if args.follow:
                # Follow log (simple implementation)
                print(f"Following log for {args.process_id} in instance '{args.instance}' (Ctrl+C to stop)")
                try:
                    last_lines = []
                    while True:
                        response = client.get_log(args.process_id, args.lines)
                        if response.get('success'):
                            lines = response['log']
                            # Only show new lines
                            if lines != last_lines:
                                for line in lines:
                                    print(line)
                                last_lines = lines[:]
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nStopped following log")
            else:
                response = client.get_log(args.process_id, args.lines)
                if response.get('success'):
                    lines = response['log']
                    for line in lines:
                        print(line)
                else:
                    print(f"Error: {response.get('error', 'Unknown error')}")

        elif args.command == 'cleanup':
            response = client.cleanup()
            if response.get('success'):
                print(f"Cleaned up {response['removed']} finished processes in instance '{args.instance}'")
            else:
                print(f"Error: {response.get('error', 'Unknown error')}")

if __name__ == "__main__":
    main()
