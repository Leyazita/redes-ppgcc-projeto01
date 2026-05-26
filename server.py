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

TIMEOUT     = 10.0


def checksum16(data: bytes) -> int:
    view = memoryview(data).cast("B")
    s = 0
    for b in view:
        s += b
    return s & 0xFFFF


def make_packet(seq: int, ack: int, flags: int, payload: bytes) -> bytes:
    cs = checksum16(payload)
    return struct.pack(HEADER_FMT, seq, ack, flags, cs) + payload


def parse_packet(data: bytes):
    if len(data) < HEADER_SIZE:
        return None, None, None, None, False
    seq, ack, flags, cs = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:]
    return seq, ack, flags, payload, (checksum16(payload) == cs)


# ═══════════════════════════════════════════════════════════════════════════════
#  SERVIDOR TCP
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
#  SERVIDOR R-UDP  (Go-Back-N)
# ═══════════════════════════════════════════════════════════════════════════════

class RUDPServer:
    def __init__(self, host, port, save_dir="/received"):
        self.host = host
        self.port = port
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        log.info(f"[R-UDP/GBN] Aguardando em {self.host}:{self.port}")

        # Rastreia transferências ativas por addr do cliente
        active      = {}
        active_lock = threading.Lock()

        while True:
            sock.settimeout(None)
            try:
                data, addr = sock.recvfrom(CHUNK_SIZE + HEADER_SIZE + 512)
            except Exception:
                continue

            seq, ack, flags, payload, valid = parse_packet(data)
            if not valid:
                continue

            if not (flags & FLAG_SYN):
                continue

            # Ignora SYN se transferência anterior do mesmo cliente ainda ativa
            with active_lock:
                if active.get(addr, False):
                    log.debug(f"[R-UDP/GBN] SYN ignorado de {addr} — transferência anterior ainda ativa")
                    continue
                active[addr] = True

            log.info(f"[R-UDP/GBN] SYN de {addr}")

            worker_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            worker_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            worker_sock.bind(("0.0.0.0", 0))
            worker_port = worker_sock.getsockname()[1]

            syn_ack = make_packet(worker_port, seq, FLAG_SYN | FLAG_ACK, b"")
            meta    = json.loads(payload.decode())

            first_data_received = threading.Event()

            t = threading.Thread(
                target=self._handle_transfer,
                args=(worker_sock, addr, meta, first_data_received, active, active_lock),
                daemon=True
            )
            t.start()

            # Reenvia SYN-ACK até 1º DATA chegar (máx 20 × 0.5s = 10s)
            def syn_ack_loop(main_sock, dest, pkt, event):
                for _ in range(20):
                    main_sock.sendto(pkt, dest)
                    if event.wait(timeout=0.5):
                        break

            threading.Thread(
                target=syn_ack_loop,
                args=(sock, addr, syn_ack, first_data_received),
                daemon=True
            ).start()

    def _handle_transfer(self, sock, addr, meta,
                         first_data_received: threading.Event,
                         active: dict, active_lock: threading.Lock):
        filename  = meta["filename"]
        filesize  = meta["filesize"]
        total     = (filesize + CHUNK_SIZE - 1) // CHUNK_SIZE
        expected  = 0
        out_order = 0
        start     = time.perf_counter()
        path      = os.path.join(self.save_dir, filename)

        log.info(f"[R-UDP/GBN] Recebendo '{filename}' ({filesize} bytes) de {addr}")

        try:
            with open(path, "wb") as f:
                while expected < total:
                    try:
                        sock.settimeout(TIMEOUT)
                        data, _ = sock.recvfrom(CHUNK_SIZE + HEADER_SIZE + 32)
                    except socket.timeout:
                        log.warning(f"[R-UDP/GBN] Timeout aguardando seq={expected}")
                        continue

                    seq, ack, flags, payload, valid = parse_packet(data)

                    if flags & FLAG_FIN:
                        break

                    # Sinaliza 1º DATA para parar loop de SYN-ACK
                    if not first_data_received.is_set():
                        first_data_received.set()

                    if not valid:
                        if expected > 0:
                            sock.sendto(make_packet(0, expected - 1, FLAG_ACK, b""), addr)
                        continue

                    if seq == expected:
                        f.write(payload)
                        expected += 1
                        sock.sendto(make_packet(0, seq, FLAG_ACK, b""), addr)
                    elif seq > expected:
                        out_order += 1
                        if expected > 0:
                            sock.sendto(make_packet(0, expected - 1, FLAG_ACK, b""), addr)
                    # seq < expected → duplicata, ignora silenciosamente

            elapsed    = time.perf_counter() - start
            file_bytes = os.path.getsize(path)
            throughput = file_bytes / elapsed if elapsed > 0 else 0
            log.info(f"[R-UDP/GBN] Completo '{filename}': {file_bytes} bytes | "
                     f"{elapsed:.3f}s | {throughput/1024:.1f} KB/s | fora_ordem={out_order}")

            # Manda ACK do último chunk recebido antes do FIN-ACK
            # para garantir que o cliente saia do loop de envio
            if expected > 0:
                for _ in range(5):
                    sock.sendto(make_packet(0, expected - 1, FLAG_ACK, b""), addr)
                    time.sleep(0.02)

            result_pkt = make_packet(0, 0, FLAG_FIN | FLAG_ACK, json.dumps({
                "status": "ok", "bytes": file_bytes, "elapsed": elapsed,
                "throughput": throughput, "out_of_order": out_order
            }).encode())
            for _ in range(5):
                sock.sendto(result_pkt, addr)
                time.sleep(0.05)

        finally:
            sock.close()
            # Libera addr para próxima transferência
            with active_lock:
                active.pop(addr, None)
            log.debug(f"[R-UDP/GBN] addr {addr} liberado")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

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