
"""
Run as Administrator. Requires: click, rich, pyfiglet, pymobiledevice3.
"""

import os, sys, time, subprocess
from typing import Optional, Tuple
import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from pyfiglet import figlet_format

console = Console()


def clear_terminal():
    os.system("cls" if os.name == "nt" else "clear")


def require_admin():
    if os.name == "nt":
        try:
            subprocess.check_call(
                "fsutil dirty query %systemdrive%", shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            console.print("[bold red]⛔ Please run this tool as Administrator![/bold red]")
            sys.exit(1)


def check_dev_mode_prompt():
    console.print("[yellow]Make sure Developer Mode is enabled on your iPhone (Settings → Privacy & Security).[/yellow]")
    if not click.confirm("Is Developer Mode enabled and your iPhone unlocked?"):
        console.print("[bold red]Abort: enable Developer Mode and unlock your phone.[/bold red]")
        sys.exit(1)


def launch_tunnel(timeout: int = 30) -> Tuple[subprocess.Popen, str, str, str]:
    proc = subprocess.Popen(
        ["python", "-m", "pymobiledevice3", "lockdown", "start-tunnel"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

    udid = host = port = None
    start = time.time()
    console.print("[cyan]Starting lockdown tunnel and reading output...[/cyan]\n")
    while True:
        if proc.stdout is None:
            break
        line = proc.stdout.readline()
        if not line:
            if time.time() - start > timeout:
                break
            time.sleep(0.05)
            continue
        s = line.strip()
        # print tunnel output (short)
        console.print(Text(s, style="cyan"))
        if s.startswith("Identifier:"):
            udid = s.split("Identifier:", 1)[1].strip()
        elif s.startswith("RSD Address:"):
            host = s.split("RSD Address:", 1)[1].strip()
        elif s.startswith("RSD Port:"):
            port = s.split("RSD Port:", 1)[1].strip()
        if udid and host and port:
            break
        if time.time() - start > timeout:
            break

    if not (udid and host and port):
        console.print("[bold red]Failed to parse tunnel output (UDID/host/port). Is device connected & trusted?[/bold red]")
        try:
            proc.terminate()
        except Exception:
            pass
        sys.exit(1)

    return proc, udid, host, port


def run_mount():
    cmd = ["python", "-m", "pymobiledevice3", "mounter", "auto-mount"]
    console.print(f"[magenta]→ Mounting Developer Disk Image[/magenta] [white]{' '.join(cmd)}[/white]")
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    combined = (out + "\n" + err).lower()
    if "already mounted" in combined or "developerdiskimage already mounted" in combined:
        console.print("[yellow]Developer Disk Image already mounted — continuing...[/yellow]\n")
        return p.returncode, out, err
    if out:
        console.print(Text(out, style="green"))
    if err:
        console.print(Text(err, style="red"))
    time.sleep(0.35)
    return p.returncode, out, err


def spawn_simulate_background(host: str, port: str, lat: float, lon: float) -> subprocess.Popen:
    cmd = [
        "python", "-m", "pymobiledevice3", "developer", "dvt",
        "simulate-location", "set", "--rsd", host, port, "--", str(lat), str(lon)
    ]
    console.print(f"[magenta]→ Starting simulate-location (background)[/magenta] [white]{' '.join(cmd)}[/white]")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    return proc


def watch_syslog_for_confirmation(timeout: int = 20) -> Tuple[bool, Optional[str]]:
    keywords = [
        "simulated location", "simulate-location", "simulate location", "simulatelocation",
        "simulated_location", "corelocation", "locationd", "enabling location", "simulate", "simulated"
    ]
    cmd = ["python", "-m", "pymobiledevice3", "syslog", "live", "-m", "SpringBoard"]
    console.print(f"[cyan]→ Watching device syslog for confirmation (timeout {timeout}s)...[/cyan]")
    logs = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    start = time.time()
    match_line = None
    try:
        while time.time() - start < timeout:
            if logs.stdout is None:
                break
            line = logs.stdout.readline()
            if not line:
                time.sleep(0.08)
                continue
            low = line.strip().lower()
            # minimal output: don't print every line
            for k in keywords:
                if k in low:
                    match_line = line.strip()
                    raise StopIteration
    except StopIteration:
        pass
    except Exception:
        pass
    finally:
        try:
            logs.terminate()
        except Exception:
            pass

    return (match_line is not None), match_line


def clear_spoof(host: str, port: str) -> bool:
    cmd = [
        "python", "-m", "pymobiledevice3", "developer", "dvt",
        "simulate-location", "clear", "--rsd", host, port
    ]
    console.print(f"[magenta]→ Clearing spoofed location[/magenta] [white]{' '.join(cmd)}[/white]")
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if out:
        console.print(Text(out, style="green"))
    if err:
        console.print(Text(err, style="red"))
    return p.returncode == 0


@click.command()
@click.option("--timeout", "-t", default=20, help="Seconds to watch syslog for confirmation", type=int)
def main(timeout):
    clear_terminal()
    console.print(Panel(Text(figlet_format("iOSSpoofer-X", font="doom"), justify="center"), style="white on black"))
    require_admin()
    check_dev_mode_prompt()

    tunnel_proc, udid, host, port = launch_tunnel()
    console.print(f"\n[bold green]UDID:[/bold green] {udid}")
    console.print(f"[bold green]Tunnel:[/bold green] {host}:{port}\n")

    time.sleep(0.3)
    run_mount()
    time.sleep(0.3)

    lat = click.prompt("Enter latitude", type=float, default=0.0)
    lon = click.prompt("Enter longitude", type=float, default=0.0)
    console.print()

    sim_proc = spawn_simulate_background(host, port, lat, lon)
    console.print(f"[dim]simulate-location process PID: {sim_proc.pid} — kept running to preserve spoof.[/dim]\n", style="dim")

    confirmed, match = watch_syslog_for_confirmation(timeout=timeout)

    # do NOT kill the simulate process here to avoid stopping spoof on some setups

    if confirmed:
        console.print(Panel(f"[bold green]✅ Verified — Spoof confirmed via syslog![/bold green]\n\n[dim]Matched log line:[/dim]\n{match}", style="green"))
    else:
        console.print(Panel("[bold yellow]⚠ Could not verify spoof via syslog within timeout. The spoof may still be active.[/bold yellow]", style="yellow"))
        console.print(f"[dim]If unsure, open Maps on device to confirm location or increase the --timeout value (current: {timeout}s).[/dim]")

    if click.confirm("\nDisconnect & clear spoof and close tunnel now?"):
        try:
            ok = clear_spoof(host, port)
            if ok:
                console.print(Panel("[bold blue]✅ Spoof cleared successfully.[/bold blue]"))
            else:
                console.print(Panel("[yellow]⚠ Clear command returned non-zero; spoof may still be active.[/yellow]"))
        except Exception as e:
            console.print(Text(f"[red]Error clearing spoof: {e}[/red]"))

        # terminate simulate process if still running
        try:
            if sim_proc.poll() is None:
                sim_proc.terminate()
                time.sleep(0.6)
                if sim_proc.poll() is None:
                    sim_proc.kill()
        except Exception:
            pass

        # terminate tunnel
        try:
            tunnel_proc.terminate()
        except Exception:
            pass

        console.print(Panel("[bold blue]Tunnel closed and cleaned up.[/bold blue]"))
    else:
        # fixed instruction message and f-string interpolation so it prints correctly
        console.print(Panel(f"[white]Leaving spoof active and tunnel open. To clear later run:[/white]\n\n[dim]python -m pymobiledevice3 developer dvt simulate-location clear --rsd {host} {port}[/dim]\n\n[dim]Then terminate simulate process (PID shown above) or reboot device.[/dim]"))

    console.print("\n[bold]Done.[/bold]")


if __name__ == "__main__":
    main()

