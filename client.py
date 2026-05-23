import socket
import struct
import threading
import time
import json
import os
import logging
import argparse
import queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

X_CUSTOM_AUTH = "20261006269-Vandirleya"   

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


def checksum16(data: bytes) -> int:
    s = 0
    for b in data:
        s = (s + b) & 0xFFFF
    return s


def make_packet(seq: int, ack: int, flags: int, payload: bytes) -> bytes:
    cs = checksum16(payload)
    header = struct.pack(HEADER_FMT, seq, ack, flags, cs)
    return header + payload


def parse_packet(data: bytes):
    if len(data) < HEADER_SIZE:
        return None, None, None, None, False
    seq, ack, flags, cs = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:]
    valid = (checksum16(payload) == cs)
    return seq, ack, flags, payload, valid

#  CLIENTE TCP

class TCPClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def send_file(self, filepath: str) -> dict:
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            log.info(f"[TCP] Conectado a {self.host}:{self.port}")

            s.sendall(f"X-Custom-Auth: {X_CUSTOM_AUTH}\n".encode())

            meta = (json.dumps({"filename": filename, "filesize": filesize}) + "\n").encode()
            s.sendall(meta)

            ack = s.recv(8)
            if ack != b"OK":
                raise RuntimeError("Servidor não confirmou metadados")

            start = time.perf_counter()
            sent = 0

            with open(filepath, "rb") as f:
                while chunk := f.read(CHUNK_SIZE):
                    s.sendall(chunk)
                    sent += len(chunk)

            elapsed = time.perf_counter() - start
            throughput = sent / elapsed if elapsed > 0 else 0

            resp_raw = s.recv(512)
            resp = json.loads(resp_raw.decode())

        result = {
            "protocol": "TCP",
            "filename": filename,
            "bytes_sent": sent,
            "elapsed": elapsed,
            "throughput_kbps": throughput / 1024,
            "retransmissions": 0
        }
        log.info(f"[TCP] Enviado: {sent} bytes em {elapsed:.3f}s — {throughput/1024:.1f} KB/s")
        return result

#  CLIENTE R-UDP 

class RUDPClient:
    """
    Transmissor Selective Repeat.
    Mantém janela deslizante de tamanho WINDOW_SIZE.
    Retransmite individualmente pacotes com NACK ou timeout.
    Thread separada recebe ACKs/NACKs continuamente.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock = None

        # Estado da janela
        self._lock          = threading.Lock()
        self._ack_event     = threading.Event()
        self._acked         = set()          # seqs confirmados
        self._retransmit_q  = queue.Queue()  # seqs para retransmitir
        self._base          = 0
        self._retransmissions = 0
        self._running       = False

    # Handshake
    def _syn(self, filename: str, filesize: int):
        meta = json.dumps({"filename": filename, "filesize": filesize}).encode()
        pkt  = make_packet(0, 0, FLAG_SYN, meta)
        for _ in range(MAX_RETRIES):
            self.sock.sendto(pkt, (self.host, self.port))
            self.sock.settimeout(TIMEOUT)
            try:
                data, _ = self.sock.recvfrom(HEADER_SIZE + 256)
                seq, ack, flags, payload, valid = parse_packet(data)
                if valid and (flags & FLAG_SYN) and (flags & FLAG_ACK):
                    log.info("[R-UDP] SYN-ACK recebido — iniciando transferência")
                    return
            except socket.timeout:
                log.warning("[R-UDP] SYN timeout, retentando...")
        raise RuntimeError("SYN não confirmado após retentativas")

    # Thread de recepção de ACKs
    def _ack_receiver(self, total_chunks: int):
        while self._running:
            try:
                self.sock.settimeout(0.5)
                data, _ = self.sock.recvfrom(HEADER_SIZE + 512)
            except socket.timeout:
                continue
            except Exception:
                break

            seq, ack, flags, payload, valid = parse_packet(data)
            if not valid:
                continue

            if flags & FLAG_ACK:
                with self._lock:
                    self._acked.add(ack)
                    # Avança base da janela
                    while self._base in self._acked:
                        self._base += 1
                self._ack_event.set()

            elif flags & FLAG_NACK:
                log.debug(f"[R-UDP] NACK para seq={ack} — agendando retransmissão")
                self._retransmit_q.put(ack)

            if flags & FLAG_FIN:
                self._running = False
                break

    # Envio principal
    def send_file(self, filepath: str) -> dict:
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        # Divide arquivo em chunks
        chunks = []
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                chunks.append(chunk)

        total = len(chunks)
        log.info(f"[R-UDP] Enviando '{filename}' — {total} chunks de {CHUNK_SIZE} bytes")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(TIMEOUT)

        try:
            self._syn(filename, filesize)

            self._running      = True
            self._base         = 0
            self._acked        = set()
            self._retransmissions = 0
            next_seq           = 0

            # Timer por pacote: seq → (timestamp, retries)
            timers = {}

            # Inicia thread de ACKs
            t_ack = threading.Thread(target=self._ack_receiver, args=(total,), daemon=True)
            t_ack.start()

            start = time.perf_counter()

            while self._base < total:
                # Retransmissões solicitadas por NACK
                while not self._retransmit_q.empty():
                    rseq = self._retransmit_q.get_nowait()
                    if rseq not in self._acked and rseq < total:
                        pkt = make_packet(rseq, 0, FLAG_DATA, chunks[rseq])
                        self.sock.sendto(pkt, (self.host, self.port))
                        timers[rseq] = (time.perf_counter(), timers.get(rseq, (0, 0))[1] + 1)
                        self._retransmissions += 1

                # Envia novos pacotes dentro da janela
                with self._lock:
                    base_snap = self._base
                while next_seq < min(base_snap + WINDOW_SIZE, total):
                    if next_seq not in self._acked:
                        pkt = make_packet(next_seq, 0, FLAG_DATA, chunks[next_seq])
                        self.sock.sendto(pkt, (self.host, self.port))
                        timers[next_seq] = (time.perf_counter(), 0)
                    next_seq += 1

                # Verifica timeouts individuais
                now = time.perf_counter()
                for seq in range(base_snap, min(base_snap + WINDOW_SIZE, total)):
                    if seq in self._acked:
                        continue
                    if seq in timers:
                        ts, retries = timers[seq]
                        if now - ts > TIMEOUT:
                            if retries >= MAX_RETRIES:
                                raise RuntimeError(f"Seq {seq} excedeu MAX_RETRIES")
                            log.debug(f"[R-UDP] Timeout seq={seq} retry={retries+1}")
                            pkt = make_packet(seq, 0, FLAG_DATA, chunks[seq])
                            self.sock.sendto(pkt, (self.host, self.port))
                            timers[seq] = (now, retries + 1)
                            self._retransmissions += 1

                self._ack_event.wait(timeout=0.05)
                self._ack_event.clear()

            elapsed = time.perf_counter() - start
            self._running = False

            # FIN
            fin_pkt = make_packet(0, 0, FLAG_FIN, b"")
            self.sock.sendto(fin_pkt, (self.host, self.port))

            throughput = filesize / elapsed if elapsed > 0 else 0
            log.info(
                f"[R-UDP] '{filename}' enviado: {filesize} bytes em {elapsed:.3f}s "
                f"— {throughput/1024:.1f} KB/s | retransmissões={self._retransmissions}"
            )
            return {
                "protocol": "R-UDP",
                "filename": filename,
                "bytes_sent": filesize,
                "elapsed": elapsed,
                "throughput_kbps": throughput / 1024,
                "retransmissions": self._retransmissions
            }
        finally:
            self.sock.close()

#  MAIN

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cliente TCP / R-UDP")
    parser.add_argument("--mode",     choices=["tcp", "rudp"], required=True)
    parser.add_argument("--host",     default="server")
    parser.add_argument("--port",     type=int, default=5000)
    parser.add_argument("--file",     required=True, help="Arquivo a enviar")
    parser.add_argument("--runs",     type=int, default=1, help="Número de execuções")
    parser.add_argument("--out",      default="/logs/results.json")
    args = parser.parse_args()

    results = []
    for i in range(args.runs):
        log.info(f"=== Execução {i+1}/{args.runs} ===")
        if args.mode == "tcp":
            client = TCPClient(args.host, args.port)
        else:
            client = RUDPClient(args.host, args.port)
        r = client.send_file(args.file)
        r["run"] = i + 1
        results.append(r)
        time.sleep(0.5)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"Resultados salvos em {args.out}")