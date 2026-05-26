CONTAINER=${2:-redes_server}
IFACE="eth0"

reset_tc() {
    docker exec $CONTAINER bash -c "tc qdisc del dev $IFACE root 2>/dev/null || true"
}

apply_scenario() {
    local SCENARIO=$1
    local DELAY=$2
    local LOSS=$3

    reset_tc

    echo "[tc] $CONTAINER — Cenário $SCENARIO: delay=${DELAY}ms | loss=${LOSS}%"

    if [ "$LOSS" = "0" ]; then
        docker exec $CONTAINER bash -c "tc qdisc add dev $IFACE root netem delay ${DELAY}ms"
    else
        docker exec $CONTAINER bash -c "tc qdisc add dev $IFACE root netem delay ${DELAY}ms loss ${LOSS}%"
    fi
}

case "$1" in
    A) apply_scenario "A" 10 0 ;;
    B) apply_scenario "B" 50 10 ;;
    C) apply_scenario "C" 100 20 ;;
    reset)
        reset_tc
        echo "[tc] $CONTAINER rede restaurada."
        ;;
    *)
        echo "Uso: $0 [A|B|C|reset] [container]"
        exit 1
        ;;
esac