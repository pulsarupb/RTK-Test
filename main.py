import argparse
import sys
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
    parser = argparse.ArgumentParser(description="RTK GPS reader")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=3.0,
        help="Seconds between reconnect attempts",
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


def format_coord(degrees: float, hemi: str) -> str:
    if hemi in ("S", "W"):
        return f"-{degrees:.6f}"
    return f"{degrees:.6f}"


def main() -> None:
    args = parse_args()
    ser = connect_serial(args.port, args.baud, args.retry_delay)

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
            except UnicodeDecodeError:
                continue

    except KeyboardInterrupt:
        print("\nExiting.", flush=True)
    finally:
        if ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()
