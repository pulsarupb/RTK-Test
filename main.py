import argparse
import base64
import socket
import sys
import threading
import time

import pynmea2
import serial
from serial import SerialException

FIX_QUALITY = {
    0: "No Fix",
    1: "GPS (SPS)",
    2: "DGPS",
    3: "PPS",
    4: "RTK Fixed",
    5: "RTK Float",
    6: "Dead Reck.",
    7: "Manual",
    8: "Simulation",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RTK GPS reader with NTRIP corrections")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=3.0,
        help="Seconds between reconnect attempts",
    )
    parser.add_argument("--ntrip-host", default="rtk2go.com", help="NTRIP caster host")
    parser.add_argument("--ntrip-port", type=int, default=2101, help="NTRIP caster port")
    parser.add_argument("--mount-point", default="SemaAgoraRobotics", help="NTRIP mount point")
    parser.add_argument("--ntrip-user", default="rtk2go@rtk2go.com", help="NTRIP username (if required)")
    parser.add_argument("--ntrip-pass", default="rtk2go", help="NTRIP password (if required)")
    parser.add_argument(
        "--no-ntrip", action="store_true", help="Disable NTRIP corrections (standalone mode)"
    )
    return parser.parse_args()


def connect_serial(port: str, baud: int, retry_delay: float) -> serial.Serial:
    while True:
        try:
            ser = serial.Serial(port, baud, timeout=1)
            print(f"Connected to {port} @ {baud} baud", flush=True)
            return ser
        except SerialException as e:
            print(
                f"Port {port} unavailable ({e}). Retrying in {retry_delay}s...",
                flush=True,
            )
            time.sleep(retry_delay)


def connect_ntrip(
    host: str,
    port: int,
    mount_point: str,
    user: str | None,
    passwd: str | None,
    retry_delay: float,
) -> tuple[socket.socket, bytes]:
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=10)
            sock.settimeout(5)

            request = f"GET /{mount_point} HTTP/1.0\r\n"
            request += f"Host: {host}\r\n"
            request += "User-Agent: NTRIP rtk-test/0.1.0\r\n"
            if user is not None and passwd is not None:
                creds = base64.b64encode(f"{user}:{passwd}".encode()).decode()
                request += f"Authorization: Basic {creds}\r\n"
            request += "\r\n"

            sock.sendall(request.encode())

            buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("NTRIP connection closed during handshake")
                buf += chunk
                if b"\r\n" not in buf:
                    continue
                status_line = buf.split(b"\r\n", 1)[0].decode(errors="replace")

                if status_line.startswith("SOURCETABLE"):
                    raise ConnectionError(
                        f"Mount point '{mount_point}' not found (got source table)"
                    )
                if "200" not in status_line:
                    print(f"NTRIP warning: {status_line}", flush=True)
                    raise ConnectionError(f"Bad NTRIP response: {status_line}")

                if status_line.startswith("ICY"):
                    _, _, remainder = buf.partition(b"\r\n")
                elif b"\r\n\r\n" in buf:
                    _, _, remainder = buf.partition(b"\r\n\r\n")
                else:
                    continue
                print(
                    f"NTRIP connected to {mount_point} @ {host}:{port}",
                    flush=True,
                )
                return sock, remainder

        except (socket.timeout, ConnectionError, OSError) as e:
            print(
                f"NTRIP connection failed ({e}). Retrying in {retry_delay}s...",
                flush=True,
            )
            time.sleep(retry_delay)


def ntrip_forwarder(
    ser_ref: list[serial.Serial],
    host: str,
    port: int,
    mount_point: str,
    user: str | None,
    passwd: str | None,
    retry_delay: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            sock, remainder = connect_ntrip(
                host, port, mount_point, user, passwd, retry_delay
            )
            if remainder:
                ser_ref[0].write(remainder)

            while not stop_event.is_set():
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("NTRIP connection closed")
                    ser_ref[0].write(chunk)
                except socket.timeout:
                    continue
        except (ConnectionError, OSError, SerialException) as e:
            if not stop_event.is_set():
                print(
                    f"\nNTRIP error ({e}). Reconnecting in {retry_delay}s...",
                    flush=True,
                )
                time.sleep(retry_delay)
        finally:
            try:
                sock.close()
            except Exception:
                pass


def format_coord(degrees: float, hemi: str) -> str:
    if hemi in ("S", "W"):
        return f"-{degrees:.6f}"
    return f"{degrees:.6f}"


def main() -> None:
    args = parse_args()
    ser = connect_serial(args.port, args.baud, args.retry_delay)
    ser_ref: list[serial.Serial] = [ser]
    stop_event = threading.Event()
    ntrip_thread: threading.Thread | None = None

    if not args.no_ntrip:
        ntrip_thread = threading.Thread(
            target=ntrip_forwarder,
            args=(
                ser_ref,
                args.ntrip_host,
                args.ntrip_port,
                args.mount_point,
                args.ntrip_user,
                args.ntrip_pass,
                args.retry_delay,
                stop_event,
            ),
            daemon=True,
        )
        ntrip_thread.start()

    print("Waiting for GPS fix... (Ctrl+C to quit)", flush=True)

    try:
        while True:
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                if not line:
                    continue

                msg = pynmea2.parse(line)

                if not isinstance(msg, pynmea2.types.GGA):
                    continue

                lat = format_coord(msg.latitude, msg.lat_dir)
                lon = format_coord(msg.longitude, msg.lon_dir)
                alt = f"{msg.altitude:.1f}" if msg.altitude else "N/A"
                fix = FIX_QUALITY.get(msg.gps_qual, f"Unknown ({msg.gps_qual})")
                sats = msg.num_sats

                print(
                    f"\r[{lat}, {lon}]  "
                    f"Alt: {alt} m  "
                    f"Fix: {fix}  "
                    f"Sats: {sats}  "
                    f"(Ctrl+C to quit)   ",
                    end="",
                    flush=True,
                )

            except pynmea2.ParseError:
                continue
            except SerialException:
                print("\nSerial connection lost. Reconnecting...", flush=True)
                ser.close()
                ser = connect_serial(args.port, args.baud, args.retry_delay)
                ser_ref[0] = ser
            except UnicodeDecodeError:
                continue

    except KeyboardInterrupt:
        print("\nExiting.", flush=True)
    finally:
        stop_event.set()
        if ntrip_thread and ntrip_thread.is_alive():
            ntrip_thread.join(timeout=3)
        if ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()
