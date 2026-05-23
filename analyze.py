import json
import os
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import warnings
warnings.filterwarnings("ignore")

LOG_DIR    = "logs"
OUTPUT_DIR = "plots"
os.makedirs(OUTPUT_DIR, exist_ok=True)

sns.set_theme(style="whitegrid", palette="muted", font_scale=1.1)

SCENARIO_LABELS = {
    "A": "Cenário A\n(0% perda / 10ms)",
    "B": "Cenário B\n(10% perda / 50ms)",
    "C": "Cenário C\n(20% perda / 100ms)",
}

# Carrega resultados

def load_results() -> pd.DataFrame:
    rows = []
    for scenario in ["A", "B", "C"]:
        for proto in ["tcp", "rudp"]:
            path = os.path.join(LOG_DIR, f"results_{proto}_scenario{scenario}.json")
            if not os.path.exists(path):
                print(f"[AVISO] Arquivo não encontrado: {path} (usando dados sintéticos)")
                # Dados sintéticos para demonstração (remova quando tiver dados reais)
                rows += _synthetic(proto, scenario)
                continue
            with open(path) as f:
                runs = json.load(f)
            for r in runs:
                rows.append({
                    "protocol":        r.get("protocol", proto.upper()),
                    "scenario":        scenario,
                    "run":             r.get("run", 1),
                    "bytes_sent":      r.get("bytes_sent", 0),
                    "elapsed":         r.get("elapsed", 0),
                    "throughput_kbps": r.get("throughput_kbps", 0),
                    "retransmissions": r.get("retransmissions", 0),
                })
    return pd.DataFrame(rows)


def _synthetic(proto: str, scenario: str) -> list:
    """Dados sintéticos apenas para testar o script sem rodar o experimento."""
    rng = np.random.default_rng(hash(proto + scenario) % (2**32))
    base_tp  = {"tcp": {"A": 9000, "B": 5000, "C": 2500},
                 "rudp": {"A": 7500, "B": 4200, "C": 1800}}[proto][scenario]
    base_rt  = {"tcp": 0, "rudp": {"A": 3, "B": 20, "C": 55}}
    if proto == "tcp":
        base_rt = 0
    else:
        base_rt = base_rt["rudp"][scenario]
    rows = []
    for i in range(1, 6):
        tp = max(100, base_tp + rng.normal(0, base_tp * 0.08))
        rt = max(0, int(base_rt + rng.normal(0, base_rt * 0.15 + 0.5)))
        elapsed = (10 * 1024) / tp if tp > 0 else 999
        rows.append({
            "protocol":        proto.upper(),
            "scenario":        scenario,
            "run":             i,
            "bytes_sent":      10 * 1024 * 1024,
            "elapsed":         elapsed,
            "throughput_kbps": tp,
            "retransmissions": rt,
        })
    return rows


# Métricas agregadas

def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    agg = df.groupby(["protocol", "scenario"]).agg(
        throughput_mean=("throughput_kbps",  "mean"),
        throughput_std =("throughput_kbps",  "std"),
        elapsed_mean   =("elapsed",          "mean"),
        elapsed_std    =("elapsed",          "std"),
        retrans_mean   =("retransmissions",  "mean"),
        retrans_std    =("retransmissions",  "std"),
        n              =("run",              "count"),
    ).reset_index()
    return agg


# Gráficos Seaborn

def plot_seaborn_throughput(df: pd.DataFrame, agg: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    for i, sc in enumerate(["A", "B", "C"]):
        ax = axes[i]
        sub = df[df.scenario == sc]
        sns.barplot(data=sub, x="protocol", y="throughput_kbps",
                    capsize=0.15, errwidth=2, ax=ax, palette=["#4C72B0", "#DD8452"])
        ax.set_title(SCENARIO_LABELS[sc], fontsize=11)
        ax.set_xlabel("Protocolo")
        ax.set_ylabel("Throughput (KB/s)" if i == 0 else "")
    fig.suptitle("Vazão (Throughput) — TCP vs R-UDP por Cenário", fontsize=13, y=1.02)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, "seaborn_throughput.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Salvo: {path}")


def plot_seaborn_retransmissions(df: pd.DataFrame):
    rudp = df[df.protocol == "R-UDP"].copy()
    if rudp.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.boxplot(data=rudp, x="scenario", y="retransmissions", ax=ax,
                palette=["#55A868", "#C44E52", "#8172B2"])
    ax.set_title("Retransmissões R-UDP por Cenário")
    ax.set_xlabel("Cenário")
    ax.set_ylabel("Retransmissões")
    path = os.path.join(OUTPUT_DIR, "seaborn_retransmissions.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Salvo: {path}")


def plot_seaborn_elapsed(df: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    for i, sc in enumerate(["A", "B", "C"]):
        ax = axes[i]
        sub = df[df.scenario == sc]
        sns.violinplot(data=sub, x="protocol", y="elapsed", ax=ax,
                       palette=["#4C72B0", "#DD8452"], inner="box", cut=0)
        ax.set_title(SCENARIO_LABELS[sc])
        ax.set_xlabel("Protocolo")
        ax.set_ylabel("Tempo total (s)" if i == 0 else "")
    fig.suptitle("Tempo de Transferência — TCP vs R-UDP por Cenário", fontsize=13, y=1.02)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, "seaborn_elapsed.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Salvo: {path}")


# Gráficos Plotly

def plot_plotly_throughput(agg: pd.DataFrame):
    fig = go.Figure()
    colors = {"TCP": "#1f77b4", "R-UDP": "#ff7f0e"}
    for proto in ["TCP", "R-UDP"]:
        sub = agg[agg.protocol == proto]
        fig.add_trace(go.Bar(
            name=proto,
            x=[SCENARIO_LABELS[s].replace("\n", " ") for s in sub.scenario],
            y=sub.throughput_mean,
            error_y=dict(type="data", array=sub.throughput_std.fillna(0), visible=True),
            marker_color=colors[proto],
        ))
    fig.update_layout(
        title="Throughput Médio (± Desvio Padrão) — TCP vs R-UDP",
        xaxis_title="Cenário",
        yaxis_title="Throughput (KB/s)",
        barmode="group",
        template="plotly_white",
        legend_title="Protocolo",
    )
    path = os.path.join(OUTPUT_DIR, "plotly_throughput.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")
    try:
        fig.write_image(os.path.join(OUTPUT_DIR, "plotly_throughput.png"))
    except Exception:
        pass


def plot_plotly_comparison_heatmap(agg: pd.DataFrame):
    pivot = agg.pivot(index="scenario", columns="protocol", values="throughput_mean")
    fig = px.imshow(
        pivot,
        text_auto=".0f",
        color_continuous_scale="Blues",
        title="Heatmap — Throughput Médio (KB/s)",
        labels=dict(x="Protocolo", y="Cenário", color="KB/s"),
    )
    fig.update_layout(template="plotly_white")
    path = os.path.join(OUTPUT_DIR, "plotly_heatmap.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")


def plot_plotly_retrans_vs_loss(agg: pd.DataFrame):
    rudp = agg[agg.protocol == "R-UDP"].copy()
    if rudp.empty:
        return
    loss_map = {"A": 0, "B": 10, "C": 20}
    rudp["loss_pct"] = rudp.scenario.map(loss_map)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=rudp.loss_pct, y=rudp.retrans_mean,
        error_y=dict(type="data", array=rudp.retrans_std.fillna(0), visible=True),
        mode="lines+markers",
        marker=dict(size=10, color="#ff7f0e"),
        line=dict(width=2),
        name="R-UDP retransmissões",
    ))
    fig.update_layout(
        title="Retransmissões R-UDP vs. Taxa de Perda Configurada",
        xaxis_title="Perda de Pacotes (%)",
        yaxis_title="Retransmissões (média)",
        template="plotly_white",
    )
    path = os.path.join(OUTPUT_DIR, "plotly_retrans_vs_loss.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")


def plot_plotly_dashboard(df: pd.DataFrame, agg: pd.DataFrame):
    """Dashboard com 4 gráficos num único HTML."""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            "Throughput Médio (KB/s)",
            "Tempo de Transferência (s)",
            "Retransmissões R-UDP",
            "Throughput: distribuição por execução",
        ),
    )
    colors = {"TCP": "#1f77b4", "R-UDP": "#ff7f0e"}
    scenarios_x = ["A", "B", "C"]

    for proto in ["TCP", "R-UDP"]:
        sub = agg[agg.protocol == proto]
        # Throughput
        fig.add_trace(go.Bar(
            name=proto, x=scenarios_x, y=sub.throughput_mean,
            error_y=dict(type="data", array=sub.throughput_std.fillna(0), visible=True),
            marker_color=colors[proto], legendgroup=proto,
        ), row=1, col=1)
        # Elapsed
        fig.add_trace(go.Bar(
            name=proto, x=scenarios_x, y=sub.elapsed_mean,
            error_y=dict(type="data", array=sub.elapsed_std.fillna(0), visible=True),
            marker_color=colors[proto], legendgroup=proto, showlegend=False,
        ), row=1, col=2)

    # Retransmissões (apenas R-UDP)
    rudp_agg = agg[agg.protocol == "R-UDP"]
    fig.add_trace(go.Bar(
        name="R-UDP", x=scenarios_x, y=rudp_agg.retrans_mean,
        error_y=dict(type="data", array=rudp_agg.retrans_std.fillna(0), visible=True),
        marker_color="#ff7f0e", legendgroup="R-UDP", showlegend=False,
    ), row=2, col=1)

    # Box por execução
    for proto in ["TCP", "R-UDP"]:
        sub = df[df.protocol == proto]
        fig.add_trace(go.Box(
            y=sub.throughput_kbps, x=sub.scenario,
            name=proto, marker_color=colors[proto],
            legendgroup=proto, showlegend=False,
        ), row=2, col=2)

    fig.update_layout(
        title_text="Dashboard — Análise TCP vs R-UDP | PPGCC UFPI 2026-1",
        barmode="group",
        template="plotly_white",
        height=700,
    )
    path = os.path.join(OUTPUT_DIR, "plotly_dashboard.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")


# Tabela estatística

def print_stats_table(agg: pd.DataFrame):
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║          ANÁLISE ESTATÍSTICA — TCP vs R-UDP                  ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"{'Protocolo':<8} {'Cenário':<10} {'Throughput KB/s':>18} {'Tempo (s)':>12} {'Retrans':>10}")
    print(f"{'':8} {'':10} {'Média ± DP':>18} {'Média ± DP':>12} {'Média ± DP':>10}")
    print("╠══════════════════════════════════════════════════════════════╣")
    for _, r in agg.iterrows():
        tp  = f"{r.throughput_mean:.1f} ± {r.throughput_std:.1f}"
        el  = f"{r.elapsed_mean:.2f} ± {r.elapsed_std:.2f}"
        rt  = f"{r.retrans_mean:.1f} ± {r.retrans_std:.1f}"
        print(f"{r.protocol:<8} {r.scenario:<10} {tp:>18} {el:>12} {rt:>10}")
    print("╚══════════════════════════════════════════════════════════════╝\n")


# Main

if __name__ == "__main__":
    print("=== Carregando resultados... ===")
    df  = load_results()
    agg = aggregate(df)
    print(f"    {len(df)} registros carregados\n")

    print_stats_table(agg)

    print("=== Gerando gráficos Seaborn... ===")
    plot_seaborn_throughput(df, agg)
    plot_seaborn_retransmissions(df)
    plot_seaborn_elapsed(df)

    print("\n=== Gerando gráficos Plotly... ===")
    plot_plotly_throughput(agg)
    plot_plotly_comparison_heatmap(agg)
    plot_plotly_retrans_vs_loss(agg)
    plot_plotly_dashboard(df, agg)

    print(f"\n✓ Todos os gráficos em: {OUTPUT_DIR}/")
    print("  Cole os HTMLs no Colab ou abra direto no navegador.")