#!/usr/bin/env python3
"""One-time: log in on the QNX QEMU serial console (quickstart default test account)
and install an SSH public key so the host can use key-based ssh from then on.

usage: install_key.py <serial.sock> <pubkey file> [user]
"""
import socket, sys, time

sock_path, pub_path = sys.argv[1], sys.argv[2]
user = sys.argv[3] if len(sys.argv) > 3 else "qnxuser"
pub = open(pub_path).read().strip()

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sock_path)
s.settimeout(0.5)
buf = b""

def read_for(sec):
    global buf
    end = time.time() + sec
    while time.time() < end:
        try:
            d = s.recv(4096)
            if d:
                buf += d
        except socket.timeout:
            pass

def wait_for(token, sec=30):
    end = time.time() + sec
    while time.time() < end:
        read_for(0.5)
        if token.encode() in buf[-2000:]:
            return True
    return False

def send(line):
    s.sendall(line.encode() + b"\r")

send("")
if not wait_for("login:", 30):
    sys.exit("no login prompt; console tail:\n" + buf[-500:].decode(errors="replace"))
send(user)
if not wait_for("assword", 10):
    sys.exit("no password prompt")
send(user)  # quickstart default: password == username (test VM only)
read_for(3)
marker = "KEY-INSTALLED-OK"
send(f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{pub}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo {marker}")
ok = wait_for(marker + "\r", 10) or wait_for(marker, 2)
send("exit")
read_for(1)
tail = buf[-800:].decode(errors="replace")
print(tail)
sys.exit(0 if ok and tail.count(marker) >= 2 else "key install not confirmed")
