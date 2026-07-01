import json
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from scipy import stats
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
SCENARIO_LABELS_FLAT = {k: v.replace("\n", " ") for k, v in SCENARIO_LABELS.items()}

FILE_SIZE_MB = 1  # tamanho do arquivo de teste


# ═══════════════════════════════════════════════════════════════════════════════
#  CARGA DE DADOS
# ═══════════════════════════════════════════════════════════════════════════════

def load_results() -> pd.DataFrame:
    rows = []
    for scenario in ["A", "B", "C"]:
        for proto in ["tcp", "rudp"]:
            path = os.path.join(LOG_DIR, f"results_{proto}_scenario{scenario}.json")
            if not os.path.exists(path):
                print(f"[AVISO] Arquivo não encontrado: {path} — usando dados sintéticos")
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
    """Dados sintéticos para 1 MB / 30 runs — apenas para testar sem dados reais."""
    rng = np.random.default_rng(hash(proto + scenario) % (2**32))
    base_tp = {"tcp":  {"A": 90000, "B": 150,  "C": 80},
               "rudp": {"A": 7500,  "B": 300,  "C": 150}}[proto][scenario]
    base_rt = {"tcp":  {"A": 0,     "B": 120,  "C": 280},
               "rudp": {"A": 0,     "B": 800,  "C": 2500}}[proto][scenario]
    rows = []
    for i in range(1, 31):
        tp      = max(10, base_tp + rng.normal(0, base_tp * 0.08))
        rt      = max(0, int(base_rt + rng.normal(0, base_rt * 0.15 + 0.5)))
        elapsed = (FILE_SIZE_MB * 1024) / tp if tp > 0 else 999
        rows.append({
            "protocol":        proto.upper(),
            "scenario":        scenario,
            "run":             i,
            "bytes_sent":      FILE_SIZE_MB * 1024 * 1024,
            "elapsed":         elapsed,
            "throughput_kbps": tp,
            "retransmissions": rt,
        })
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
#  AGREGAÇÃO COM IC 95%
# ═══════════════════════════════════════════════════════════════════════════════

def ci95(series: pd.Series) -> float:
    """Margem do intervalo de confiança de 95% (t de Student)."""
    n = len(series)
    if n < 2:
        return 0.0
    return stats.t.ppf(0.975, df=n-1) * series.std(ddof=1) / np.sqrt(n)


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    agg = df.groupby(["protocol", "scenario"]).agg(
        throughput_mean =("throughput_kbps",  "mean"),
        throughput_std  =("throughput_kbps",  "std"),
        elapsed_mean    =("elapsed",          "mean"),
        elapsed_std     =("elapsed",          "std"),
        retrans_mean    =("retransmissions",  "mean"),
        retrans_std     =("retransmissions",  "std"),
        n               =("run",              "count"),
    ).reset_index()

    # IC 95% para cada métrica
    def _ci(grp, col):
        return grp[col].transform(lambda x: ci95(x))

    ci_rows = []
    for (proto, sc), grp in df.groupby(["protocol", "scenario"]):
        ci_rows.append({
            "protocol": proto, "scenario": sc,
            "throughput_ci95": ci95(grp["throughput_kbps"]),
            "elapsed_ci95":    ci95(grp["elapsed"]),
            "retrans_ci95":    ci95(grp["retransmissions"]),
        })
    ci_df = pd.DataFrame(ci_rows)
    agg = agg.merge(ci_df, on=["protocol", "scenario"])
    return agg


# ═══════════════════════════════════════════════════════════════════════════════
#  TABELA ESTATÍSTICA
# ═══════════════════════════════════════════════════════════════════════════════

def print_stats_table(agg: pd.DataFrame):
    print("\n╔══════════════════════════════════════════════════════════════════════════╗")
    print("║            ANÁLISE ESTATÍSTICA — TCP vs R-UDP  (IC 95%)                 ║")
    print("╠══════════════════════════════════════════════════════════════════════════╣")
    print(f"{'Proto':<7} {'Cen':<5} {'Throughput KB/s':>22} {'Tempo (s)':>18} {'Retrans':>16} {'n':>4}")
    print(f"{'':7} {'':5} {'Média ± IC95%':>22} {'Média ± IC95%':>18} {'Média ± IC95%':>16} {'':4}")
    print("╠══════════════════════════════════════════════════════════════════════════╣")
    for _, r in agg.iterrows():
        tp = f"{r.throughput_mean:>8.1f} ± {r.throughput_ci95:>6.1f}"
        el = f"{r.elapsed_mean:>5.3f} ± {r.elapsed_ci95:>5.3f}"
        rt = f"{r.retrans_mean:>6.1f} ± {r.retrans_ci95:>5.1f}"
        print(f"{r.protocol:<7} {r.scenario:<5} {tp:>22} {el:>18} {rt:>16} {int(r.n):>4}")
    print("╚══════════════════════════════════════════════════════════════════════════╝\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  GRÁFICOS SEABORN
# ═══════════════════════════════════════════════════════════════════════════════

def plot_seaborn_throughput(df: pd.DataFrame, agg: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    for i, sc in enumerate(["A", "B", "C"]):
        ax = axes[i]
        sub = df[df.scenario == sc]
        sns.barplot(data=sub, x="protocol", y="throughput_kbps",
                    capsize=0.15, errwidth=2, ax=ax,
                    palette=["#4C72B0", "#DD8452"],
                    order=["TCP", "R-UDP"])
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
    """
    Gráfico de barras agrupadas em escala logarítmica.
    TCP: medido via tcpdump (valor total dividido por runs).
    R-UDP: medido na aplicação (por execução).
    """
    agg = df.groupby(["protocol", "scenario"])["retransmissions"].mean().reset_index()

    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    colors = {"TCP": "#4C72B0", "R-UDP": "#DD8452"}

    for i, sc in enumerate(["A", "B", "C"]):
        ax = axes[i]
        sub = agg[agg.scenario == sc]
        protos = ["TCP", "R-UDP"]
        vals   = [sub[sub.protocol == p]["retransmissions"].values[0] for p in protos]
        all_zero = all(v == 0 for v in vals)

        if all_zero:
            # Cenário A: escala linear, barras simbólicas
            bars = ax.bar(protos, [1, 1], color=[colors[p] for p in protos],
                          alpha=0.85, width=0.5)
            for bar in bars:
                ax.text(bar.get_x() + bar.get_width()/2, 1.1,
                        "0", ha="center", va="bottom", fontsize=12, fontweight="bold")
            ax.set_ylim(0, 3)
            ax.set_yticks([])
            ax.set_ylabel("Retransmissões" if i == 0 else "")
        else:
            bars = ax.bar(protos, [max(v, 0.3) for v in vals],
                          color=[colors[p] for p in protos], alpha=0.85, width=0.5)
            for bar, val in zip(bars, vals):
                label = "0" if val == 0 else f"{val:.1f}"
                ax.text(bar.get_x() + bar.get_width()/2,
                        bar.get_height() * 1.5,
                        label, ha="center", va="bottom", fontsize=10)
            ax.set_yscale("log")
            ax.set_ylim(bottom=0.1, top=max(max(v, 0.1) for v in vals) * 5)
            ax.set_ylabel("Retransmissões (escala log)" if i == 0 else "")

        ax.set_title(SCENARIO_LABELS[sc], fontsize=11)
        ax.set_xlabel("Protocolo")

    fig.suptitle("Retransmissões médias — TCP vs R-UDP por Cenário (escala log)\n"
                 "(TCP: tcpdump/run | R-UDP: contagem de aplicação/run)",
                 fontsize=12, y=1.02)
    plt.tight_layout()
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
                       palette=["#4C72B0", "#DD8452"],
                       order=["TCP", "R-UDP"],
                       inner="box", cut=0)
        ax.set_title(SCENARIO_LABELS[sc])
        ax.set_xlabel("Protocolo")
        ax.set_ylabel("Tempo total (s)" if i == 0 else "")
    fig.suptitle("Tempo de Transferência — TCP vs R-UDP por Cenário", fontsize=13, y=1.02)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, "seaborn_elapsed.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Salvo: {path}")


def plot_seaborn_ci95(agg: pd.DataFrame):
    """Gráfico de barras com IC 95% explícito para throughput."""
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(3)
    width = 0.35
    scenarios = ["A", "B", "C"]
    colors = {"TCP": "#4C72B0", "R-UDP": "#DD8452"}

    for j, proto in enumerate(["TCP", "R-UDP"]):
        sub = agg[agg.protocol == proto].set_index("scenario")
        means = [sub.loc[s, "throughput_mean"] for s in scenarios]
        cis   = [sub.loc[s, "throughput_ci95"] for s in scenarios]
        ax.bar(x + j*width, means, width, yerr=cis, capsize=5,
               label=proto, color=colors[proto], alpha=0.85)

    ax.set_xticks(x + width/2)
    ax.set_xticklabels([SCENARIO_LABELS_FLAT[s] for s in scenarios])
    ax.set_ylabel("Throughput médio (KB/s)")
    ax.set_title("Throughput com Intervalo de Confiança 95%")
    ax.legend()
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, "seaborn_ci95_throughput.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Salvo: {path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  GRÁFICOS PLOTLY
# ═══════════════════════════════════════════════════════════════════════════════

def plot_plotly_throughput(agg: pd.DataFrame):
    fig = go.Figure()
    colors = {"TCP": "#1f77b4", "R-UDP": "#ff7f0e"}
    for proto in ["TCP", "R-UDP"]:
        sub = agg[agg.protocol == proto]
        fig.add_trace(go.Bar(
            name=proto,
            x=[SCENARIO_LABELS_FLAT[s] for s in sub.scenario],
            y=sub.throughput_mean,
            error_y=dict(type="data", array=sub.throughput_ci95.fillna(0),
                         visible=True, color="black"),
            marker_color=colors[proto],
        ))
    fig.update_layout(
        title="Throughput Médio (± IC 95%) — TCP vs R-UDP",
        xaxis_title="Cenário", yaxis_title="Throughput (KB/s)",
        barmode="group", template="plotly_white", legend_title="Protocolo",
    )
    path = os.path.join(OUTPUT_DIR, "plotly_throughput.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")
    try:
        fig.write_image(os.path.join(OUTPUT_DIR, "plotly_throughput.png"))
    except Exception:
        pass


def plot_plotly_retrans_both(agg: pd.DataFrame):
    """Retransmissões de TCP e R-UDP vs taxa de perda configurada."""
    loss_map = {"A": 0, "B": 10, "C": 20}
    fig = go.Figure()
    colors = {"TCP": "#1f77b4", "R-UDP": "#ff7f0e"}

    for proto in ["TCP", "R-UDP"]:
        sub = agg[agg.protocol == proto].copy()
        sub["loss_pct"] = sub.scenario.map(loss_map)
        fig.add_trace(go.Scatter(
            x=sub.loss_pct, y=sub.retrans_mean,
            error_y=dict(type="data", array=sub.retrans_ci95.fillna(0), visible=True),
            mode="lines+markers",
            marker=dict(size=10, color=colors[proto]),
            line=dict(width=2),
            name=proto,
        ))
    fig.update_layout(
        title="Retransmissões vs. Taxa de Perda — TCP e R-UDP",
        xaxis_title="Perda de Pacotes (%)",
        yaxis_title="Retransmissões médias (± IC 95%)",
        template="plotly_white",
    )
    path = os.path.join(OUTPUT_DIR, "plotly_retrans_vs_loss.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")


def plot_plotly_comparison_heatmap(agg: pd.DataFrame):
    pivot = agg.pivot(index="scenario", columns="protocol", values="throughput_mean")
    fig = px.imshow(
        pivot, text_auto=".0f", color_continuous_scale="Blues",
        title="Heatmap — Throughput Médio (KB/s)",
        labels=dict(x="Protocolo", y="Cenário", color="KB/s"),
    )
    fig.update_layout(template="plotly_white")
    path = os.path.join(OUTPUT_DIR, "plotly_heatmap.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")


def plot_plotly_dashboard(df: pd.DataFrame, agg: pd.DataFrame):
    """Dashboard com 4 gráficos num único HTML."""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            "Throughput Médio ± IC 95% (KB/s)",
            "Tempo de Transferência (s)",
            "Retransmissões por Protocolo",
            "Throughput: distribuição por execução",
        ),
    )
    colors = {"TCP": "#1f77b4", "R-UDP": "#ff7f0e"}
    scenarios_x = ["A", "B", "C"]

    for proto in ["TCP", "R-UDP"]:
        sub = agg[agg.protocol == proto]
        fig.add_trace(go.Bar(
            name=proto, x=scenarios_x, y=sub.throughput_mean,
            error_y=dict(type="data", array=sub.throughput_ci95.fillna(0), visible=True),
            marker_color=colors[proto], legendgroup=proto,
        ), row=1, col=1)
        fig.add_trace(go.Bar(
            name=proto, x=scenarios_x, y=sub.elapsed_mean,
            error_y=dict(type="data", array=sub.elapsed_ci95.fillna(0), visible=True),
            marker_color=colors[proto], legendgroup=proto, showlegend=False,
        ), row=1, col=2)
        fig.add_trace(go.Bar(
            name=proto, x=scenarios_x, y=sub.retrans_mean,
            error_y=dict(type="data", array=sub.retrans_ci95.fillna(0), visible=True),
            marker_color=colors[proto], legendgroup=proto, showlegend=False,
        ), row=2, col=1)

    for proto in ["TCP", "R-UDP"]:
        sub = df[df.protocol == proto]
        fig.add_trace(go.Box(
            y=sub.throughput_kbps, x=sub.scenario,
            name=proto, marker_color=colors[proto],
            legendgroup=proto, showlegend=False,
        ), row=2, col=2)

    fig.update_layout(
        title_text="Dashboard — Análise TCP vs R-UDP | PPGCC UFPI 2026-1",
        barmode="group", template="plotly_white", height=700,
    )
    path = os.path.join(OUTPUT_DIR, "plotly_dashboard.html")
    fig.write_html(path)
    print(f"  Salvo: {path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Carregando resultados... ===")
    df  = load_results()
    agg = aggregate(df)
    print(f"    {len(df)} registros carregados ({df['run'].max()} runs por protocolo/cenário)\n")

    print_stats_table(agg)

    print("=== Gerando gráficos Seaborn... ===")
    plot_seaborn_throughput(df, agg)
    plot_seaborn_retransmissions(df)
    plot_seaborn_elapsed(df)
    plot_seaborn_ci95(agg)

    print("\n=== Gerando gráficos Plotly... ===")
    plot_plotly_throughput(agg)
    plot_plotly_retrans_both(agg)
    plot_plotly_comparison_heatmap(agg)
    plot_plotly_dashboard(df, agg)

    print(f"\n✓ Todos os gráficos em: {OUTPUT_DIR}/")
    print("  Cole os HTMLs no Colab ou abra direto no navegador.")