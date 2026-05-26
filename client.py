import socket
import struct
import threading
import time
import json
import os
import logging
import argparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

X_CUSTOM_AUTH = "20261006269-Vandirleya"

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


#  CLIENTE TCP

class TCPClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def send_file(self, filepath):
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            log.info(f"[TCP] Conectado a {self.host}:{self.port}")
            s.sendall(f"X-Custom-Auth: {X_CUSTOM_AUTH}\n".encode())
            s.sendall((json.dumps({"filename": filename, "filesize": filesize}) + "\n").encode())
            if s.recv(8) != b"OK":
                raise RuntimeError("Servidor não confirmou metadados")

            start = time.perf_counter()
            sent = 0
            with open(filepath, "rb") as f:
                while chunk := f.read(CHUNK_SIZE):
                    s.sendall(chunk)
                    sent += len(chunk)

            elapsed = time.perf_counter() - start
            throughput = sent / elapsed if elapsed > 0 else 0
            s.recv(512)

        log.info(f"[TCP] Enviado: {sent} bytes em {elapsed:.3f}s — {throughput/1024:.1f} KB/s")
        return {"protocol": "TCP", "filename": filename, "bytes_sent": sent,
                "elapsed": elapsed, "throughput_kbps": throughput/1024, "retransmissions": 0}


#  CLIENTE R-UDP  

class RUDPClient:

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def _syn(self, sock, filename, filesize):
        meta = json.dumps({"filename": filename, "filesize": filesize}).encode()
        pkt  = make_packet(0, 0, FLAG_SYN, meta)
        for attempt in range(30):
            sock.sendto(pkt, (self.host, self.port))
            sock.settimeout(2.0)
            try:
                data, _ = sock.recvfrom(HEADER_SIZE + 256)
                _, _, flags, _, valid = parse_packet(data)
                if valid and (flags & FLAG_SYN) and (flags & FLAG_ACK):
                    log.info("[R-UDP] SYN-ACK recebido")
                    return
            except socket.timeout:
                log.warning(f"[R-UDP] SYN timeout (tentativa {attempt+1})")
        raise RuntimeError("SYN falhou")

    def send_file(self, filepath):
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        chunks = []
        with open(filepath, "rb") as f:
            while True:
                c = f.read(CHUNK_SIZE)
                if not c:
                    break
                chunks.append(c)
        total = len(chunks)
        log.info(f"[R-UDP/GBN] Enviando '{filename}' — {total} chunks")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(TIMEOUT)

        try:
            self._syn(sock, filename, filesize)

            base      = 0
            next_seq  = 0
            retrans   = 0
            start     = time.perf_counter()

            while base < total:
                # Envia pacotes dentro da janela
                while next_seq < min(base + WINDOW_SIZE, total):
                    pkt = make_packet(next_seq, 0, FLAG_DATA, chunks[next_seq])
                    sock.sendto(pkt, (self.host, self.port))
                    next_seq += 1

                # Aguarda ACK
                try:
                    data, _ = sock.recvfrom(HEADER_SIZE + 32)
                    _, ack, flags, _, valid = parse_packet(data)
                    if valid and (flags & FLAG_ACK):
                        # ACK cumulativo — avança base
                        if ack >= base:
                            base = ack + 1
                            next_seq = base  # permite enviar novos pacotes
                except socket.timeout:
                    # Timeout — retransmite toda a janela (Go-Back-N)
                    log.debug(f"[R-UDP/GBN] Timeout, retransmitindo janela base={base}")
                    retrans += (next_seq - base)
                    next_seq = base  # volta ao início da janela

            elapsed = time.perf_counter() - start
            throughput = filesize / elapsed if elapsed > 0 else 0

            # Envia FIN
            for _ in range(3):
                sock.sendto(make_packet(0, 0, FLAG_FIN, b""), (self.host, self.port))

            log.info(f"[R-UDP/GBN] '{filename}' enviado: {filesize} bytes em {elapsed:.3f}s "
                     f"— {throughput/1024:.1f} KB/s | retransmissões={retrans}")
            return {"protocol": "R-UDP", "filename": filename, "bytes_sent": filesize,
                    "elapsed": elapsed, "throughput_kbps": throughput/1024, "retransmissions": retrans}
        finally:
            sock.close()

#  MAIN

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",  choices=["tcp", "rudp"], required=True)
    parser.add_argument("--host",  default="server")
    parser.add_argument("--port",  type=int, default=5000)
    parser.add_argument("--file",  required=True)
    parser.add_argument("--runs",  type=int, default=1)
    parser.add_argument("--out",   default="/logs/results.json")
    args = parser.parse_args()

    results = []
    for i in range(args.runs):
        log.info(f"=== Execução {i+1}/{args.runs} ===")
        client = TCPClient(args.host, args.port) if args.mode == "tcp" else RUDPClient(args.host, args.port)
        r = client.send_file(args.file)
        r["run"] = i + 1
        results.append(r)
        time.sleep(0.5)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"Resultados salvos em {args.out}")