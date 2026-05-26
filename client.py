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

WINDOW_SIZE = 32
# Timeout base — o cliente ajusta dinamicamente com base no RTT medido
TIMEOUT_BASE = 0.5


# ── checksum com memoryview (muito mais rápido que iterar bytes) ──────────────
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
#  CLIENTE TCP
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
#  CLIENTE R-UDP  (Go-Back-N com pipeline real)
#
#  Melhorias em relação à versão anterior:
#  1. Pipeline completo: thread separada drena ACKs enquanto o loop principal
#     enche a janela → CPU nunca fica parada esperando o RTT.
#  2. RTT adaptativo: timeout = max(2×RTT_médio, 0.3s) para evitar retransmissões
#     desnecessárias no cenário C (100ms delay).
#  3. Detecção de ACK duplicado: 3 ACKs iguais acionam retransmissão imediata
#     (fast-retransmit), sem esperar timeout completo.
#  4. SYN com retry de 3s cada (igual ao anterior, mas loop curto).
# ═══════════════════════════════════════════════════════════════════════════════

class RUDPClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    # ── Handshake ─────────────────────────────────────────────────────────────
    def _syn(self, sock, filename, filesize):
        meta = json.dumps({"filename": filename, "filesize": filesize}).encode()
        pkt  = make_packet(0, 0, FLAG_SYN, meta)

        for attempt in range(30):
            sock.sendto(pkt, (self.host, self.port))
            sock.settimeout(3.0)
            try:
                data, _ = sock.recvfrom(HEADER_SIZE + 256)
                seq, ack, flags, payload, valid = parse_packet(data)
                if valid and (flags & FLAG_SYN) and (flags & FLAG_ACK):
                    dedicated_port = seq
                    log.info(f"[R-UDP] SYN-ACK recebido — porta dedicada: {dedicated_port}")
                    return dedicated_port
            except socket.timeout:
                log.warning(f"[R-UDP] SYN timeout (tentativa {attempt+1})")
        raise RuntimeError("SYN falhou após 30 tentativas")

    # ── Envio principal ───────────────────────────────────────────────────────
    def send_file(self, filepath):
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        # Lê todos os chunks de uma vez (igual à versão original)
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
        # Buffer de recepção maior para não perder ACKs em rajada
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)

        try:
            dedicated_port = self._syn(sock, filename, filesize)
            server_addr    = (self.host, dedicated_port)

            # ── Estado compartilhado entre thread de envio e thread de ACK ──
            lock        = threading.Lock()
            base        = [0]          # próximo seq esperado pelo servidor
            next_seq    = [0]          # próximo seq a enviar
            retrans     = [0]
            done        = [False]
            dup_ack_cnt = [0]
            last_ack    = [-1]

            # RTT adaptativo: mantém média móvel exponencial
            rtt_avg     = [TIMEOUT_BASE]
            send_times  = {}           # seq → timestamp de envio

            # ── Thread leitora de ACKs (drena o socket continuamente) ────────
            def ack_reader():
                while not done[0]:
                    try:
                        sock.settimeout(0.05)
                        data, _ = sock.recvfrom(HEADER_SIZE + 32)
                    except socket.timeout:
                        continue
                    except OSError:
                        break

                    _, ack_num, flags, _, valid = parse_packet(data)
                    if not valid or not (flags & FLAG_ACK):
                        continue

                    with lock:
                        # Atualiza RTT com base no pacote que foi ACKado
                        if ack_num in send_times:
                            sample = time.perf_counter() - send_times.pop(ack_num)
                            # EWMA com α=0.25
                            rtt_avg[0] = 0.75 * rtt_avg[0] + 0.25 * sample

                        if ack_num > last_ack[0]:
                            # ACK novo — avança janela
                            last_ack[0]    = ack_num
                            dup_ack_cnt[0] = 0
                            if ack_num >= base[0]:
                                # Limpa send_times de seq já confirmados
                                for s in list(send_times.keys()):
                                    if s <= ack_num:
                                        send_times.pop(s, None)
                                base[0]     = ack_num + 1
                                next_seq[0] = max(next_seq[0], base[0])
                        elif ack_num == last_ack[0]:
                            # ACK duplicado
                            dup_ack_cnt[0] += 1
                            if dup_ack_cnt[0] >= 3:
                                # Fast-retransmit: força o loop principal a
                                # retransmitir a partir de base
                                log.debug(f"[R-UDP/GBN] Fast-retransmit seq={base[0]}")
                                retrans[0]    += (next_seq[0] - base[0])
                                next_seq[0]    = base[0]
                                dup_ack_cnt[0] = 0

            ack_thread = threading.Thread(target=ack_reader, daemon=True)
            ack_thread.start()

            start = time.perf_counter()

            # ── Loop de envio principal ───────────────────────────────────────
            last_send_time = time.perf_counter()

            while True:
                with lock:
                    b  = base[0]
                    ns = next_seq[0]

                if b >= total:
                    break  # tudo confirmado

                # Envia pacotes que cabem na janela
                sent_something = False
                with lock:
                    while next_seq[0] < min(base[0] + WINDOW_SIZE, total):
                        seq_to_send = next_seq[0]
                        pkt = make_packet(seq_to_send, 0, FLAG_DATA, chunks[seq_to_send])
                        sock.sendto(pkt, server_addr)
                        send_times[seq_to_send] = time.perf_counter()
                        next_seq[0] += 1
                        sent_something = True
                    last_send_time_snap = last_send_time
                    b_snap  = base[0]
                    ns_snap = next_seq[0]

                if sent_something:
                    last_send_time = time.perf_counter()

                # Verifica timeout: se a janela não avançou por muito tempo
                timeout = max(2.0 * rtt_avg[0], 0.3)
                if (time.perf_counter() - last_send_time) > timeout and b_snap < total:
                    with lock:
                        if base[0] == b_snap and next_seq[0] > base[0]:
                            log.debug(f"[R-UDP/GBN] Timeout base={base[0]}, "
                                      f"retransmitindo {next_seq[0]-base[0]} pkts "
                                      f"(rtt_avg={rtt_avg[0]*1000:.0f}ms)")
                            retrans[0]  += (next_seq[0] - base[0])
                            next_seq[0]  = base[0]
                    last_send_time = time.perf_counter()
                else:
                    # Cede CPU sem busy-wait agressivo
                    time.sleep(0.001)

            done[0] = True
            elapsed    = time.perf_counter() - start
            throughput = filesize / elapsed if elapsed > 0 else 0

            # Envia FIN
            fin = make_packet(0, 0, FLAG_FIN, b"")
            for _ in range(10):
                sock.sendto(fin, server_addr)
                time.sleep(0.05)

            log.info(f"[R-UDP/GBN] '{filename}' enviado: {filesize} bytes em {elapsed:.3f}s "
                     f"— {throughput/1024:.1f} KB/s | retransmissões={retrans[0]}")
            return {"protocol": "R-UDP", "filename": filename, "bytes_sent": filesize,
                    "elapsed": elapsed, "throughput_kbps": throughput/1024,
                    "retransmissions": retrans[0]}
        finally:
            sock.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

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
        time.sleep(0.3)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"Resultados salvos em {args.out}")