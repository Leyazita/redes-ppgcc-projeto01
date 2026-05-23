FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    iproute2 \
    tcpdump \
    net-tools \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir scapy

WORKDIR /app
COPY server.py client.py tc_scenarios.sh ./
RUN chmod +x /app/tc_scenarios.sh

RUN mkdir -p /received /logs

EXPOSE 5000/tcp 5000/udp