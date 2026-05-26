CLIENT=${2:-redes_client}
SERVER=${3:-redes_server}
IFACE="eth0"

reset_tc() {
    docker exec $CLIENT bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
    docker exec $SERVER bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
    echo "[tc] $CLIENT + $SERVER — rede restaurada."
}

apply_scenario() {
    local SCENARIO=$1
    local DELAY=$2   
    local LOSS=$3 

    reset_tc

    echo "[tc] Cenário $SCENARIO: delay=${DELAY}ms cada lado | loss=${LOSS}% cada lado"
    echo "     → RTT total ~$((DELAY*2))ms | perda efetiva ~$(echo "scale=1; 100-(100-$LOSS)*(100-$LOSS)/100" | bc)%"

    if [ "$LOSS" = "0" ]; then
        docker exec $CLIENT bash -c "tc qdisc add dev $IFACE root netem delay ${DELAY}ms"
        docker exec $SERVER bash -c "tc qdisc add dev $IFACE root netem delay ${DELAY}ms"
    else
        docker exec $CLIENT bash -c "tc qdisc add dev $IFACE root netem delay ${DELAY}ms loss ${LOSS}%"
        docker exec $SERVER bash -c "tc qdisc add dev $IFACE root netem delay ${DELAY}ms loss ${LOSS}%"
    fi
}

case "$1" in
    A) apply_scenario "A" 5  0  ;;   
    B) apply_scenario "B" 25 5  ;;   
    C) apply_scenario "C" 50 10 ;;   
    reset)
        reset_tc
        ;;
    *)
        echo "Uso: $0 [A|B|C|reset] [client_container] [server_container]"
        echo ""
        echo "  Exemplos:"
        echo "    $0 B                          # usa redes_client e redes_server"
        echo "    $0 C meu_client meu_server    # containers customizados"
        echo "    $0 reset                      # remove tc dos dois"
        exit 1
        ;;
esac