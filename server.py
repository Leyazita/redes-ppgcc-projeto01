import socket
import struct
import threading
import time
import json
import os
import logging
import argparse

X_CUSTOM_AUTH = "20261006269-Vandirleya"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

CHUNK_SIZE  = 4096
HEADER_FMT  = "!IIHH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
FLAG_DATA   = 0x01
FLAG_ACK    = 0x02
FLAG_SYN    = 0x04
FLAG_FIN    = 0x08

WINDOW_SIZE = 16
TIMEOUT     = 0.3


def checksum16(data):
    s = 0
    for b in data:
        s = (s + b) & 0xFFFF
    return s

def make_packet(seq, ack, flags, payload):
    cs = checksum16(payload)
    return struct.pack(HEADER_FMT, seq, ack, flags, cs) + payload

def parse_packet(data):
    if len(data) < HEADER_SIZE:
        return None, None, None, None, False
    seq, ack, flags, cs = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:]
    return seq, ack, flags, payload, (checksum16(payload) == cs)

#  SERVIDOR TCP

class TCPServer:
    def __init__(self, host, port, save_dir="/received"):
        self.host = host
        self.port = port
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(5)
            log.info(f"[TCP] Aguardando em {self.host}:{self.port}")
            while True:
                conn, addr = s.accept()
                threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _handle(self, conn, addr):
        def recv_line(c):
            line = b""
            while True:
                b = c.recv(1)
                if not b or b == b"\n":
                    break
                line += b
            return line

        try:
            with conn:
                auth = recv_line(conn).decode(errors="replace").strip()
                log.info(f"[TCP] X-Custom-Auth: {auth}")
                meta = json.loads(recv_line(conn).decode())
                filename, filesize = meta["filename"], meta["filesize"]
                log.info(f"[TCP] Recebendo '{filename}' ({filesize} bytes)")
                conn.sendall(b"OK")

                path = os.path.join(self.save_dir, filename)
                received = 0
                start = time.perf_counter()
                with open(path, "wb") as f:
                    while received < filesize:
                        chunk = conn.recv(min(CHUNK_SIZE, filesize - received))
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)

                elapsed = time.perf_counter() - start
                throughput = received / elapsed if elapsed > 0 else 0
                log.info(f"[TCP] Completo: {received} bytes | {elapsed:.3f}s | {throughput/1024:.1f} KB/s")
                conn.sendall(json.dumps({"status": "ok", "bytes": received,
                                         "elapsed": elapsed, "throughput": throughput}).encode())
        except Exception as e:
            log.error(f"[TCP] Erro: {e}")

#  SERVIDOR R-UDP 

class RUDPServer:
    def __init__(self, host, port, save_dir="/received"):
        self.host = host
        self.port = port
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((self.host, self.port))
            log.info(f"[R-UDP/GBN] Aguardando em {self.host}:{self.port}")
            while True:
                self._receive_file(s)

    def _receive_file(self, s):
        # Aguarda SYN
        while True:
            try:
                s.settimeout(None)
                data, addr = s.recvfrom(CHUNK_SIZE + HEADER_SIZE + 512)
            except Exception:
                continue
            seq, ack, flags, payload, valid = parse_packet(data)
            if valid and (flags & FLAG_SYN):
                break

        meta = json.loads(payload.decode())
        filename, filesize = meta["filename"], meta["filesize"]
        log.info(f"[R-UDP/GBN] SYN de {addr} — '{filename}' ({filesize} bytes)")
        s.sendto(make_packet(0, 0, FLAG_SYN | FLAG_ACK, b""), addr)

        expected   = 0
        total      = (filesize + CHUNK_SIZE - 1) // CHUNK_SIZE
        out_order  = 0
        start      = time.perf_counter()
        path       = os.path.join(self.save_dir, filename)

        with open(path, "wb") as f:
            while expected < total:
                try:
                    s.settimeout(10.0)
                    data, addr2 = s.recvfrom(CHUNK_SIZE + HEADER_SIZE + 32)
                except socket.timeout:
                    log.warning("[R-UDP/GBN] Timeout aguardando pacote")
                    continue

                seq, ack, flags, payload, valid = parse_packet(data)

                if flags & FLAG_FIN:
                    break

                if not valid:
                    # Envia ACK do último pacote confirmado
                    if expected > 0:
                        s.sendto(make_packet(0, expected - 1, FLAG_ACK, b""), addr)
                    continue

                if seq == expected:
                    f.write(payload)
                    expected += 1
                    # ACK cumulativo
                    s.sendto(make_packet(0, seq, FLAG_ACK, b""), addr)
                else:
                    # Fora de ordem — descarta e reenvia ACK do último confirmado
                    out_order += 1
                    if expected > 0:
                        s.sendto(make_packet(0, expected - 1, FLAG_ACK, b""), addr)

        elapsed    = time.perf_counter() - start
        file_bytes = os.path.getsize(path)
        throughput = file_bytes / elapsed if elapsed > 0 else 0
        log.info(f"[R-UDP/GBN] Completo: {file_bytes} bytes | {elapsed:.3f}s | "
                 f"{throughput/1024:.1f} KB/s | fora_ordem={out_order}")
        s.sendto(make_packet(0, 0, FLAG_FIN | FLAG_ACK, json.dumps({
            "status": "ok", "bytes": file_bytes, "elapsed": elapsed,
            "throughput": throughput, "out_of_order": out_order
        }).encode()), addr)

#  MAIN

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["tcp", "rudp"], required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    log.info(f"Servidor modo={args.mode}")
    if args.mode == "tcp":
        TCPServer(args.host, args.port).run()
    else:
        RUDPServer(args.host, args.port).run()