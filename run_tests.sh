SCENARIOS=("A" "B" "C")
PROTOCOLS=("tcp" "rudp")
RUNS=5
TEST_FILE="files/test_10MB.bin"
SERVER="redes_server"
CLIENT="redes_client"
TCP_PORT=5000
UDP_PORT=5001
IFACE="eth0"

mkdir -p logs files received

echo ">>> Gerando arquivo de teste (10 MB)..."
dd if=/dev/urandom of=$TEST_FILE bs=1M count=10 2>/dev/null
echo "    Arquivo criado: $TEST_FILE"

echo ">>> Preparando /files no container cliente..."
docker exec $CLIENT bash -c "rm -rf /files && mkdir -p /files && chmod 777 /files"
docker cp $TEST_FILE $CLIENT:/files/test_10MB.bin
echo "    Cópia OK"

# Aplica tc nos dois containers:
#   - Cliente: delay + loss (afeta pacotes DATA enviados → servidor vê perda → RUDP retransmite)
#   - Servidor: delay + loss (afeta ACKs voltando → TCP sofre backoff realista)
# Delay dividido em 2 para que o RTT total bata no valor do cenário.
# Perda dividida em 2 para que a perda efetiva end-to-end aproxime o valor do cenário.
apply_tc() {
    local SCENARIO=$1

    # Limpa nos dois
    docker exec $CLIENT bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
    docker exec $SERVER bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"

    case $SCENARIO in
        A)
            # 0% perda / RTT ~10ms → 5ms em cada lado
            docker exec $CLIENT bash -c "tc qdisc add dev $IFACE root netem delay 5ms"
            docker exec $SERVER bash -c "tc qdisc add dev $IFACE root netem delay 5ms"
            ;;
        B)
            # 10% perda / RTT ~50ms → 5% + 25ms em cada lado
            docker exec $CLIENT bash -c "tc qdisc add dev $IFACE root netem delay 25ms loss 5%"
            docker exec $SERVER bash -c "tc qdisc add dev $IFACE root netem delay 25ms loss 5%"
            ;;
        C)
            # 20% perda / RTT ~100ms → 10% + 50ms em cada lado
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
            --file /files/test_10MB.bin \
            --runs $RUNS \
            --out $OUT || echo "  [AVISO] Cliente retornou erro"

        docker exec $SERVER bash -c "pkill -f tcpdump || true; pkill -f server.py || true"
        sleep 2

        docker exec $SERVER bash -c \
            "tcpdump -r $PCAP -l -n 2>/dev/null | awk '{print NR\",\"\$0}' > $CSV" || true

        echo "  Concluído: $OUT"
    done
done

# Remove tc dos dois containers
docker exec $CLIENT bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
docker exec $SERVER bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
echo ""
echo ">>> tc removido do cliente e do servidor"

echo ">>> Copiando logs para ./logs/ ..."
docker cp $SERVER:/logs/. ./logs/ 2>/dev/null || true

echo ""
echo "Todos os testes concluídos!"
echo "Próximo passo: python3 analyze.py"