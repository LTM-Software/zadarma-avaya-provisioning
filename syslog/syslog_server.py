#!/usr/bin/env python3
import datetime
import socket


LOG_PATH = "/logs/avaya-syslog.log"


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 514))
    print("listening udp/514", flush=True)

    with open(LOG_PATH, "a", buffering=1) as log_file:
        log_file.write(f"--- syslog started {datetime.datetime.now().isoformat()} ---\n")
        while True:
            data, addr = sock.recvfrom(65535)
            now = datetime.datetime.now().isoformat(timespec="seconds")
            message = data.decode("utf-8", "replace").rstrip()
            line = f"{now} {addr[0]}:{addr[1]} {message}"
            print(line, flush=True)
            log_file.write(line + "\n")


if __name__ == "__main__":
    main()

