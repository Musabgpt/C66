"""Which local web apps are running on this phone (dev servers the agent starts), for the live Preview."""
import socket

COMMON = (3000, 3001, 4173, 4200, 5000, 5173, 5500, 8000, 8001, 8080, 8081, 8888, 9000)
OWN = (8780, 8791, 8793, 8795, 8796, 8798)      # VS Code and NewAl Code's own servers


def open_ports(extra=()):
    found = []
    for port in tuple(COMMON) + tuple(extra):
        if port in OWN or port in found:
            continue
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.15):
                found.append(int(port))
        except OSError:
            continue
    return found
