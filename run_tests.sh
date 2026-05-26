SCENARIOS=("A" "B" "C")
PROTOCOLS=("tcp" "rudp")
RUNS=30
TEST_FILE="files/test_1MB.bin"
SERVER="redes_server"
CLIENT="redes_client"
TCP_PORT=5000
UDP_PORT=5001
IFACE="eth0"

mkdir -p logs files received

echo ">>> Gerando arquivo de teste (1 MB)..."
dd if=/dev/urandom of=$TEST_FILE bs=1M count=1 2>/dev/null
echo "    Arquivo criado: $TEST_FILE"

echo ">>> Preparando /files no container cliente..."
docker exec $CLIENT bash -c "rm -rf /files && mkdir -p /files && chmod 777 /files"
docker cp $TEST_FILE $CLIENT:/files/test_1MB.bin
echo "    Cópia OK"

apply_tc() {
    local SCENARIO=$1
    docker exec $CLIENT bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
    docker exec $SERVER bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
    case $SCENARIO in
        A)
            docker exec $CLIENT bash -c "tc qdisc add dev $IFACE root netem delay 5ms"
            docker exec $SERVER bash -c "tc qdisc add dev $IFACE root netem delay 5ms"
            ;;
        B)
            docker exec $CLIENT bash -c "tc qdisc add dev $IFACE root netem delay 25ms loss 5%"
            docker exec $SERVER bash -c "tc qdisc add dev $IFACE root netem delay 25ms loss 5%"
            ;;
        C)
            docker exec $CLIENT bash -c "tc qdisc add dev $IFACE root netem delay 50ms loss 10%"
            docker exec $SERVER bash -c "tc qdisc add dev $IFACE root netem delay 50ms loss 10%"
            ;;
    esac
    echo "  [tc] Cliente + Servidor — Cenário $SCENARIO aplicado"
}

wait_server() {
    local MODE=$1
    local PORT=$2
    local LOG=$3
    docker exec $SERVER bash -c "pkill -f 'server.py' || true; pkill -f tcpdump || true"
    sleep 2
    docker exec -d $SERVER bash -c "nohup python3 /app/server.py --mode $MODE --port $PORT > $LOG 2>&1"
    for i in $(seq 1 15); do
        sleep 1
        if docker exec $SERVER bash -c "pgrep -f 'server.py' > /dev/null 2>&1"; then
            echo "  Servidor $MODE subiu (${i}s)"
            return 0
        fi
    done
    echo "  [ERRO] Servidor não subiu"
    return 1
}

extract_tcp_retrans() {
    local PCAP=$1
    local OUT_JSON=$2
    RETRANS=0
    if docker exec $SERVER which tshark > /dev/null 2>&1; then
        RETRANS=$(docker exec $SERVER bash -c \
            "tshark -r $PCAP -Y 'tcp.analysis.retransmission' 2>/dev/null | wc -l" || echo 0)
    else
        RETRANS=$(docker exec $SERVER bash -c \
            "tcpdump -r $PCAP -nn 2>/dev/null | grep -c 'retransmit\|dup ack' || echo 0" || echo 0)
    fi
    if [ -f "$OUT_JSON" ] && [ "$RETRANS" -gt 0 ] 2>/dev/null; then
        docker exec $SERVER python3 -c "
import json
with open('$OUT_JSON') as f:
    data = json.load(f)
runs = len(data)
per_run = int($RETRANS / runs) if runs > 0 else 0
for r in data:
    r['retransmissions'] = per_run
with open('$OUT_JSON', 'w') as f:
    json.dump(data, f, indent=2)
print(f'  [tcpdump] TCP retransmissões totais=$RETRANS, por execução≈{per_run}')
" 2>/dev/null || echo "  [tcpdump] retransmissões TCP=$RETRANS (não injetado no JSON)"
    else
        echo "  [tcpdump] TCP retransmissões=$RETRANS"
    fi
}

for SCENARIO in "${SCENARIOS[@]}"; do
    echo ""
    echo "════════════════════════════════════════"
    echo "  CENÁRIO $SCENARIO"
    echo "════════════════════════════════════════"

    apply_tc $SCENARIO

    for PROTO in "${PROTOCOLS[@]}"; do
        PORT=$TCP_PORT
        [ "$PROTO" = "rudp" ] && PORT=$UDP_PORT
        PROTO_UP="${PROTO^^}"
        OUT="/logs/results_${PROTO}_scenario${SCENARIO}.json"
        PCAP="/logs/capture_${PROTO}_scenario${SCENARIO}.pcap"
        CSV="/logs/capture_${PROTO}_scenario${SCENARIO}.csv"
        SLOG="/logs/server_${PROTO}_${SCENARIO}.log"

        echo ""
        echo "--- Protocolo: $PROTO_UP | Cenário: $SCENARIO ---"

        wait_server $PROTO $PORT $SLOG || continue
        sleep 1

        docker exec -d $SERVER bash -c "nohup tcpdump -i eth0 port $PORT -w $PCAP 2>/dev/null"
        sleep 1

        echo "  Executando $RUNS transferências..."
        docker exec $CLIENT python3 /app/client.py \
            --mode $PROTO \
            --host 172.28.0.10 \
            --port $PORT \
            --file /files/test_1MB.bin \
            --runs $RUNS \
            --out $OUT || echo "  [AVISO] Cliente retornou erro"

        # Aguarda threads do servidor finalizarem antes de matar
        sleep 3
        docker exec $SERVER bash -c "pkill -f tcpdump || true; pkill -f server.py || true"
        sleep 2

        docker exec $SERVER bash -c \
            "tcpdump -r $PCAP -l -n 2>/dev/null | awk '{print NR\",\"\$0}' > $CSV" || true

        if [ "$PROTO" = "tcp" ]; then
            extract_tcp_retrans $PCAP $OUT
        fi

        echo "  Concluído: $OUT"
    done
done

docker exec $CLIENT bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
docker exec $SERVER bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
echo ""
echo ">>> tc removido do cliente e do servidor"

echo ">>> Copiando logs para ./logs/ ..."
docker cp $SERVER:/logs/. ./logs/ 2>/dev/null || true

echo ""
echo "Todos os testes concluídos!"
echo "Próximo passo: python3 analyze.py"