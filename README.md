# Projeto de Redes de Computadores — PPGCC/UFPI 2026-1

Análise experimental comparativa entre **TCP** e **R-UDP (Go-Back-N)**
para transferência de arquivos em condições adversas de rede, utilizando
contêineres Docker e simulação de tráfego com `tc netem`.

> **Disciplina:** Projeto de Redes de Computadores  
> **Instituição:** PPGCC/UFPI — Campus Senador Helvídio Nunes de Barros  
> **Aluna:** Vandirleya — Matrícula: 20261006269  
> **Período:** 2026-1

---

## 📁 Estrutura do Repositório

```
redes-ppgcc-projeto01/
├── client.py              # Cliente TCP e R-UDP
├── server.py              # Servidor TCP e R-UDP
├── analyze.py             # Análise estatística e geração de gráficos
├── run_tests.sh           # Script de automação dos experimentos
├── tc_scenarios.sh        # Aplicação manual dos cenários tc netem
├── Dockerfile             # Imagem base dos contêineres
├── docker-compose.yml     # Configuração dos contêineres
├── logs/                  # JSONs, PCAPs e logs do servidor
│   ├── results_tcp_scenarioA.json
│   ├── results_tcp_scenarioB.json
│   ├── results_tcp_scenarioC.json
│   ├── results_rudp_scenarioA.json
│   ├── results_rudp_scenarioB.json
│   ├── results_rudp_scenarioC.json
│   ├── capture_*.pcap
│   └── server_*.log
├── plots/                 # Gráficos gerados pelo analyze.py
└── files/                 # Arquivo de teste gerado automaticamente
```

---

## 🔧 Pré-requisitos

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/)
- Python 3.10+ (para rodar o `analyze.py` localmente)

---

## 🚀 Como Executar

### 1. Clonar o repositório

```bash
git clone https://github.com/Leyazita/redes-ppgcc-projeto01.git
cd redes-ppgcc-projeto01
```

### 2. Construir e subir os contêineres

```bash
docker compose up --build -d
```

### 3. Rodar todos os testes

```bash
bash run_tests.sh
```

O script executa automaticamente:
- Geração do arquivo de teste (1 MB)
- Aplicação do `tc netem` nos dois contêineres
- 30 execuções por protocolo por cenário
- Captura de tráfego com `tcpdump`
- Salvamento dos resultados em `logs/`

### 4. Gerar os gráficos

```bash
pip install pandas numpy matplotlib seaborn plotly scipy
python3 analyze.py
```

Os gráficos são salvos na pasta `plots/`.

---

## 🌐 Cenários de Rede

| Cenário | Delay (cada lado) | Loss (cada lado) | RTT efetivo | Perda efetiva |
|---------|------------------|-----------------|-------------|---------------|
| A       | 5 ms             | 0%              | ~10 ms      | 0%            |
| B       | 25 ms            | 5%              | ~50 ms      | ~9,75%        |
| C       | 50 ms            | 10%             | ~100 ms     | ~19%          |

Para aplicar um cenário manualmente em um contêiner específico:

```bash
bash tc_scenarios.sh B redes_client redes_server
bash tc_scenarios.sh reset
```

---

## 📊 Resultados

| Protocolo | Cenário | Throughput médio (KB/s) | Tempo médio (s) | Retransmissões médias |
|-----------|---------|------------------------|-----------------|----------------------|
| TCP       | A       | 23.154 ± 860           | 0,045 ± 0,002   | 0                    |
| TCP       | B       | 619 ± 277              | 3,399 ± 0,783   | 5,1                  |
| TCP       | C       | 65 ± 13                | 18,886 ± 2,567  | 25,2                 |
| R-UDP     | A       | 5.113 ± 76             | 0,201 ± 0,003   | 0                    |
| R-UDP     | B       | 411 ± 77               | 5,252 ± 3,968   | 942,5                |
| R-UDP     | C       | 108 ± 29               | 14,827 ± 4,126  | 1.955,2              |

> Intervalos de confiança de 95% (distribuição t de Student, n=30).

### 📈 Análise completa no Google Colab

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/1LnNlTm6OhyLqyJjvYfjlDWw4uxO0lqVx?usp=sharing)

---

## 🔍 Protocolos Implementados

### TCP
Utiliza sockets `SOCK_STREAM` nativos do kernel. O cabeçalho de
autenticação `X-Custom-Auth: 20261006269-Vandirleya` é enviado antes
dos metadados do arquivo.

### R-UDP (Go-Back-N)
Protocolo confiável sobre UDP implementado em espaço de usuário com:
- Cabeçalho binário de 10 bytes (seq, ack, flags, checksum)
- Handshake SYN/SYN-ACK com porta dedicada por transferência
- Janela deslizante Go-Back-N (W=16)
- Timeout adaptativo (EWMA do RTT, teto de 1,5s)
- Fast-retransmit após 3 ACKs duplicados
- Autenticação via payload do pacote SYN

---

## 📄 Licença

Este projeto foi desenvolvido para fins acadêmicos no âmbito do
PPGCC/UFPI. Uso livre para fins educacionais.
