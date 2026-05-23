IFACE="eth0"

reset_tc() {
    echo "[tc] Removendo regras anteriores..."
    tc qdisc del dev $IFACE root 2>/dev/null || true
}

apply_scenario() {
    local SCENARIO=$1
    local DELAY=$2
    local LOSS=$3

    reset_tc

    echo "[tc] Cenário $SCENARIO: delay=${DELAY}ms | loss=${LOSS}%"

    if [ "$LOSS" = "0" ]; then
        tc qdisc add dev $IFACE root netem delay ${DELAY}ms
    else
        tc qdisc add dev $IFACE root netem delay ${DELAY}ms loss ${LOSS}%
    fi

    echo "[tc] Regras aplicadas:"
    tc qdisc show dev $IFACE
}

case "$1" in
    A)
        apply_scenario "A" 10 0
        ;;
    B)
        apply_scenario "B" 50 10
        ;;
    C)
        apply_scenario "C" 100 20
        ;;
    reset)
        reset_tc
        echo "[tc] Rede restaurada."
        ;;
    *)
        echo "Uso: $0 [A|B|C|reset]"
        echo "  A = 0% perda / 10ms delay"
        echo "  B = 10% perda / 50ms delay"
        echo "  C = 20% perda / 100ms delay"
        exit 1
        ;;
esac