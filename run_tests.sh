#!/bin/bash
# run_tests.sh — Fase 1 | PPGCC/UFPI 2026-1

set -e

SCENARIOS=("A" "B" "C")
PROTOCOLS=("tcp" "rudp")
RUNS=5
TEST_FILE="files/test_10MB.bin"
SERVER="redes_server"
CLIENT="redes_client"
TCP_PORT=5000
UDP_PORT=5001

mkdir -p logs files received

echo ">>> Gerando arquivo de teste (10 MB)..."
dd if=/dev/urandom of=$TEST_FILE bs=1M count=10 2>/dev/null
echo "    Arquivo criado: $TEST_FILE"

echo ">>> Preparando /files no container cliente..."
docker exec $CLIENT bash -c "rm -rf /files && mkdir -p /files && chmod 777 /files"
docker cp $TEST_FILE $CLIENT:/files/test_10MB.bin
echo "    Cópia OK"

for SCENARIO in "${SCENARIOS[@]}"; do
    echo ""
    echo "════════════════════════════════════════"
    echo "  CENÁRIO $SCENARIO"
    echo "════════════════════════════════════════"

    docker exec $SERVER bash /app/tc_scenarios.sh $SCENARIO

    for PROTO in "${PROTOCOLS[@]}"; do
        PORT=$TCP_PORT
        [ "$PROTO" = "rudp" ] && PORT=$UDP_PORT
        PROTO_UP="${PROTO^^}"
        OUT="/logs/results_${PROTO}_scenario${SCENARIO}.json"
        PCAP="/logs/capture_${PROTO}_scenario${SCENARIO}.pcap"
        CSV="/logs/capture_${PROTO}_scenario${SCENARIO}.csv"

        echo ""
        echo "--- Protocolo: $PROTO_UP | Cenário: $SCENARIO ---"

        # Garante que não há servidor anterior rodando
        docker exec $SERVER bash -c "pkill -f 'server.py' 2>/dev/null; sleep 1; true"

        # Sobe servidor em background via nohup para persistir
        docker exec -d $SERVER bash -c "nohup python3 /app/server.py --mode $PROTO --port $PORT > /logs/server_${PROTO}_${SCENARIO}.log 2>&1"
        sleep 3

        # Verifica se servidor subiu
        if ! docker exec $SERVER bash -c "pgrep -f 'server.py' > /dev/null 2>&1"; then
            echo "  [ERRO] Servidor não iniciou! Log:"
            docker exec $SERVER cat /logs/server_${PROTO}_${SCENARIO}.log 2>/dev/null || true
            continue
        fi

        # Inicia tcpdump
        docker exec -d $SERVER bash -c "nohup tcpdump -i eth0 port $PORT -w $PCAP 2>/dev/null &"
        sleep 1

        echo "  Executando $RUNS transferências..."
        docker exec $CLIENT python3 /app/client.py \
            --mode $PROTO \
            --host 172.28.0.10 \
            --port $PORT \
            --file /files/test_10MB.bin \
            --runs $RUNS \
            --out $OUT

        # Para servidor e tcpdump
        docker exec $SERVER bash -c "pkill -f tcpdump 2>/dev/null; pkill -f server.py 2>/dev/null; true"
        sleep 2

        # Converte pcap para CSV
        docker exec $SERVER bash -c \
            "tcpdump -r $PCAP -l -n 2>/dev/null | awk '{print NR\",\"\$0}' > $CSV" || true

        echo "  Concluído: $OUT"
    done
done

echo ""
echo ">>> Removendo regras tc..."
docker exec $SERVER bash /app/tc_scenarios.sh reset

echo ">>> Copiando logs para ./logs/ ..."
docker cp $SERVER:/logs/. ./logs/ 2>/dev/null || true

echo ""
echo "████  Todos os testes concluídos!  ████"
echo "Logs em: logs/"
echo "Próximo passo: python3 analyze.py"