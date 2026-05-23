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

CHUNK_SIZE   = 4096
HEADER_FMT   = "!IIHH"
HEADER_SIZE  = struct.calcsize(HEADER_FMT)
FLAG_DATA    = 0x01
FLAG_ACK     = 0x02
FLAG_SYN     = 0x04
FLAG_FIN     = 0x08
FLAG_NACK    = 0x10
WINDOW_SIZE  = 8
TIMEOUT      = 2.0
MAX_RETRIES  = 20


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
        log.info(f"[TCP] Conexão de {addr}")

        def recv_line(connection):
            line = b""
            while True:
                b = connection.recv(1)
                if not b or b == b"\n":
                    break
                line += b
            return line

        try:
            with conn:
                auth_raw = recv_line(conn)
                log.info(f"[TCP] X-Custom-Auth: {auth_raw.decode(errors='replace').strip()}")

                meta_raw = recv_line(conn)
                meta = json.loads(meta_raw.decode())
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

                conn.sendall(json.dumps({
                    "status": "ok", "bytes": received,
                    "elapsed": elapsed, "throughput": throughput
                }).encode())
        except Exception as e:
            log.error(f"[TCP] Erro: {e}")


class RUDPServer:
    def __init__(self, host, port, save_dir="/received"):
        self.host = host
        self.port = port
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((self.host, self.port))
            log.info(f"[R-UDP] Aguardando em {self.host}:{self.port}")
            while True:
                self._receive_file(s)

    def _receive_file(self, s):
        while True:
            data, addr = s.recvfrom(CHUNK_SIZE + HEADER_SIZE + 512)
            seq, ack, flags, payload, valid = parse_packet(data)
            if valid and (flags & FLAG_SYN):
                break

        meta = json.loads(payload.decode())
        filename, filesize = meta["filename"], meta["filesize"]
        log.info(f"[R-UDP] SYN de {addr} — '{filename}' ({filesize} bytes)")
        s.sendto(make_packet(0, seq, FLAG_SYN | FLAG_ACK, b""), addr)

        expected_seq = 0
        buffer = {}
        total_chunks = (filesize + CHUNK_SIZE - 1) // CHUNK_SIZE
        corrupted = 0
        out_of_order = 0
        start = time.perf_counter()

        path = os.path.join(self.save_dir, filename)
        with open(path, "wb") as f:
            while expected_seq < total_chunks:
                try:
                    s.settimeout(TIMEOUT * 3)
                    data, addr2 = s.recvfrom(CHUNK_SIZE + HEADER_SIZE + 32)
                except socket.timeout:
                    log.warning("[R-UDP] Timeout aguardando pacote")
                    continue

                seq, ack, flags, payload, valid = parse_packet(data)

                if flags & FLAG_FIN:
                    break

                if not valid:
                    corrupted += 1
                    s.sendto(make_packet(0, seq, FLAG_NACK, b""), addr)
                    continue

                if seq == expected_seq:
                    f.write(payload)
                    expected_seq += 1
                    while expected_seq in buffer:
                        f.write(buffer.pop(expected_seq))
                        expected_seq += 1
                elif seq > expected_seq:
                    if seq not in buffer:
                        buffer[seq] = payload
                        out_of_order += 1

                s.sendto(make_packet(0, seq, FLAG_ACK, b""), addr)

        elapsed = time.perf_counter() - start
        file_bytes = os.path.getsize(path)
        throughput = file_bytes / elapsed if elapsed > 0 else 0
        log.info(
            f"[R-UDP] Completo: {file_bytes} bytes | {elapsed:.3f}s | "
            f"{throughput/1024:.1f} KB/s | corrompidos={corrupted} fora_ordem={out_of_order}"
        )
        s.sendto(make_packet(0, 0, FLAG_FIN | FLAG_ACK, json.dumps({
            "status": "ok", "bytes": file_bytes, "elapsed": elapsed,
            "throughput": throughput, "corrupted": corrupted, "out_of_order": out_of_order
        }).encode()), addr)


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