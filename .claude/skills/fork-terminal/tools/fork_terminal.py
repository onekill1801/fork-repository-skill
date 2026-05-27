#!/usr/bin/env -S uv run
"""Fork a new terminal window with a command."""

import os
import platform
import subprocess


def fork_terminal(command: str) -> str:
    """Open a new Terminal window and run the specified command."""
    system = platform.system()
    cwd = os.getcwd()

    if system == "Darwin":  # macOS
        # Build shell command - use single quotes for cd to avoid escaping issues
        # Then escape everything for AppleScript
        shell_command = f"cd '{cwd}' && {command}"
        # Escape for AppleScript: backslashes first, then quotes
        escaped_shell_command = shell_command.replace("\\", "\\\\").replace('"', '\\"')

        try:
            result = subprocess.run(
                ["osascript", "-e", f'tell application "Terminal" to do script "{escaped_shell_command}"'],
                capture_output=True,
                text=True,
            )
            output = f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}\nreturn_code: {result.returncode}"
            return output
        except Exception as e:
            return f"Error: {str(e)}"

    elif system == "Windows":
        # Use /d flag to change drives if necessary
        full_command = f'cd /d "{cwd}" && {command}'
        subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", full_command], shell=True)
        return "Windows terminal launched"

    else:  # Linux and others
        import shutil
        shell_command = f"cd '{cwd}' && {command}; exec bash"
        if shutil.which("gnome-terminal"):
            if shutil.which("dbus-run-session"):
                subprocess.Popen(["dbus-run-session", "gnome-terminal", "--", "bash", "-c", shell_command])
                return "Linux gnome-terminal (dbus-run-session) launched"
            else:
                subprocess.Popen(["gnome-terminal", "--", "bash", "-c", shell_command])
                return "Linux gnome-terminal launched"
        elif shutil.which("x-terminal-emulator"):
            if shutil.which("dbus-run-session") and "gnome-terminal" in os.path.realpath(shutil.which("x-terminal-emulator")):
                subprocess.Popen(["dbus-run-session", "x-terminal-emulator", "-e", f"bash -c \"{shell_command}\""])
                return "Linux x-terminal-emulator (dbus-run-session) launched"
            else:
                subprocess.Popen(["x-terminal-emulator", "-e", f"bash -c \"{shell_command}\""])
                return "Linux x-terminal-emulator launched"
        elif shutil.which("xterm"):
            subprocess.Popen(["xterm", "-e", f"bash -c \"{shell_command}\""])
            return "Linux xterm launched"
        else:
            raise NotImplementedError(f"Platform {system} not supported (no compatible terminal emulator found)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        output = fork_terminal(" ".join(sys.argv[1:]))
        print(output)
