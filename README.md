# Projeto de Redes de Computadores — PPGCC/UFPI 2026-1

Análise experimental comparativa entre **TCP** e **R-UDP (Go-Back-N)**
para transferência de arquivos em condições adversas de rede, utilizando
contêineres Docker e simulação de tráfego com `tc netem`. O projeto é
dividido em duas fases: implementação experimental (Fase 1) e modelagem
estocástica com SimPy (Fase 2).

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
├── Fase 2/                # Modelagem estocástica com SimPy
│   ├── Fase_2_redes_de_computadores.ipynb
```

---

## 🔧 Pré-requisitos

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/)
- Python 3.10+ (para rodar o `analyze.py` localmente)

---

---

## 🔧 Pré-requisitos

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/)
- Python 3.10+ (para rodar o `analyze.py` localmente)

---

## 🚀 Fase 1 — Implementação Real

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

## 🧪 Fase 2 — Modelagem Estocástica com SimPy

Simulador de eventos discretos do protocolo R-UDP Go-Back-N, calibrado
com os dados experimentais da Fase 1, com 10 tarefas de validação estocástica.

### Como executar

**Opção 1 — Google Colab (recomendado):**

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/1LnNlTm6OhyLqyJjvYfjlDWw4uxO0lqVx?usp=sharing)

O notebook baixa automaticamente os dados da Fase 1 do Google Drive —
não é necessário configurar nada.

**Opção 2 — Execução local:**

```bash
pip install simpy scipy numpy pandas matplotlib seaborn plotly gdown
jupyter notebook "Fase 2/Fase_2_redes_de_computadores.ipynb"
```

### Parâmetros calibrados

| Cenário | Delay médio | Jitter (σ) | Perda end-to-end | RTT efetivo |
|---------|------------|-----------|-----------------|-------------|
| A | 5 ms | 0,5 ms | 0% | ~10 ms |
| B | 25 ms | 3 ms | 9,75% | ~50 ms |
| C | 50 ms | 6 ms | 19% | ~100 ms |

### 10 Tarefas de Validação

| Tarefa | Descrição | Resultado principal |
|--------|-----------|-------------------|
| T1 | Modelagem de Atraso | RTT simulado bate tc com erro < 0,1 ms |
| T2 | Perda de Bernoulli | Overhead GBN amplifica perda configurada |
| T3 | Timeout e Retransmissões | Tendência qualitativa correta; subestima em 81--91% |
| T4 | Curva de Vazão (1--3 MB) | Throughput estável; erro < 4% nos cenários B e C |
| T5 | Sensibilidade da Janela | Saturação a partir de W=16 sob perda |
| T6 | Validação de RTT | Erro absoluto < 0,1 ms em todos os cenários |
| T7 | Impacto do Jitter | Cenário A degrada 25% com jitter 4×; B e C < 15% |
| T8 | Cenário de Estresse (25%) | Prevê 62 KB/s — degradação de 40% vs Cenário C |
| T9 | Análise de Eficiência | Eficiência simulada < teórica em todos os cenários |
| T10 | Convergência Estatística | IC 95% estabiliza a partir de ~15 execuções |

### Comparação Real vs Simulado

| Cenário | Throughput Real | Throughput Simulado | Δ% |
|---------|----------------|--------------------|----|
| A | 5.113 KB/s | 5.762 KB/s | 12,7% ✅|
| B | 411 KB/s | 401 KB/s | 2,6% ✅ |
| C | 108 KB/s | 105 KB/s | 3,6% ✅ |

---

## 🌐 Cenários de Rede (Fase 1)

| Cenário | Delay (cada lado) | Loss (cada lado) | RTT efetivo | Perda efetiva |
|---------|------------------|-----------------|-------------|---------------|
| A | 5 ms | 0% | ~10 ms | 0% |
| B | 25 ms | 5% | ~50 ms | ~9,75% |
| C | 50 ms | 10% | ~100 ms | ~19% |

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

## 🔗 Links

- 📓 [Notebook Fase 2 no Google Colab](https://colab.research.google.com/drive/1UEoJrTlenGWPUSu4iatlw78MVVQR11jA?usp=sharing)
- 📁 [Dados da Fase 1 no Google Drive](https://colab.research.google.com/drive/1LnNlTm6OhyLqyJjvYfjlDWw4uxO0lqVx?usp=sharing)
- 🐙 [Repositório GitHub](https://github.com/Leyazita/redes-ppgcc-projeto01)

---

## 📄 Licença

Este projeto foi desenvolvido para fins acadêmicos no âmbito do
PPGCC/UFPI. Uso livre para fins educacionais.
