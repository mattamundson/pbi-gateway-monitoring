"""
visualize_bottlenecks.py — 4-panel weekly bottleneck dashboard.

USAGE
    # Run against real telemetry
    python visualize_bottlenecks.py

    # Run against synthetic data (for demos and testing)
    python visualize_bottlenecks.py --synthetic

    # Custom telemetry dir
    python visualize_bottlenecks.py --telemetry-dir "C:\\Users\\matt\\OneDrive\\jarvis-trader-handoff\\telemetry"

OUTPUT
    bottleneck_dashboard_YYYY-MM-DD.png — 4-panel matplotlib figure:
        1. Contention heatmap: hour × weekday, cell color = RED-sample count
        2. Resource utilization time series: CPU / RAM / disk over the week
        3. Top-3 killers stacked bar: p95 RSS by category
        4. Attribution scatter: process RAM vs machine severity correlation

DESIGN NOTES
    Uses ONLY matplotlib + pandas (both stdlib for scientific Python users).
    No plotly, no seaborn — keeps dependencies minimal for cron sandbox use.
    All numbers on the plot are derived from actual data; no fabricated values.
"""

from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.colors import LinearSegmentedColormap
except ImportError as e:
    print(f"Missing dependency: {e}. Install: pip install pandas matplotlib numpy", file=sys.stderr)
    sys.exit(2)


# ── Thresholds (must stay synced with contention audit cron) ────────────────
CPU_PCT_HIGH        = 85
CPU_QUEUE_HIGH      = 2
RAM_AVAIL_LOW_GB    = 2
RAM_COMMIT_HIGH_PCT = 85
DISK_QUEUE_HIGH     = 2
DISK_PCT_HIGH       = 90


def load_machine_telemetry(dir_: Path, days: int) -> pd.DataFrame:
    cutoff = datetime.now() - timedelta(days=days)
    frames = []
    for csv in sorted(dir_.glob("telemetry_*.csv")):
        try:
            date_str = csv.stem.replace("telemetry_", "")
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if day < cutoff:
            continue
        frames.append(pd.read_csv(csv))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts_local"] = pd.to_datetime(df["ts_local"])
    return df.sort_values("ts_local").reset_index(drop=True)


def load_process_telemetry(dir_: Path, days: int) -> pd.DataFrame:
    cutoff = datetime.now() - timedelta(days=days)
    frames = []
    for csv in sorted(dir_.glob("processes_*.csv")):
        try:
            date_str = csv.stem.replace("processes_", "")
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if day < cutoff:
            continue
        frames.append(pd.read_csv(csv))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts_local"] = pd.to_datetime(df["ts_local"])
    return df.sort_values("ts_local").reset_index(drop=True)


def classify_severity(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["cpu_pressure"]   = df["cpu_pct"] > CPU_PCT_HIGH
    df["cpu_q_pressure"] = df["cpu_queue"] > CPU_QUEUE_HIGH
    df["ram_pressure"]   = (df["ram_avail_gb"] < RAM_AVAIL_LOW_GB) | (df["ram_committed_pct"] > RAM_COMMIT_HIGH_PCT)
    df["disk_pressure"]  = (df["disk_queue"] > DISK_QUEUE_HIGH) | (df["disk_pct"] > DISK_PCT_HIGH)
    df["pressure_count"] = df[["cpu_pressure","cpu_q_pressure","ram_pressure","disk_pressure"]].sum(axis=1)
    df["severity"] = df["pressure_count"].apply(lambda n: "GREEN" if n == 0 else ("YELLOW" if n == 1 else "RED"))
    return df


def make_synthetic_data(days: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generate synthetic telemetry that mimics realistic patterns:
      - Baseline low load overnight
      - Ramps up during trading hours (8am-3pm CT)
      - Occasional Phase 3 spikes (RAM + CPU together for 1-2 hour bursts)
      - Chrome/Comet grows through the day, tabs pile up
    """
    rng = np.random.default_rng(42)  # deterministic
    end = datetime.now().replace(second=0, microsecond=0)
    start = end - timedelta(days=days)

    # Machine-level: one row per minute
    minutes = pd.date_range(start, end, freq="1min", inclusive="left")
    n = len(minutes)
    df_machine = pd.DataFrame({"ts_local": minutes})
    df_machine["ts_utc"] = df_machine["ts_local"].dt.tz_localize(None)
    df_machine["hostname"] = "MATT-DEV-SYN"

    hour = df_machine["ts_local"].dt.hour
    dow = df_machine["ts_local"].dt.dayofweek  # 0=Mon
    is_trading = (hour >= 8) & (hour < 15) & (dow < 5)  # weekdays 8am-3pm CT
    is_evening = (hour >= 18) & (hour < 23)             # your late-night sessions

    # Baseline + trading ramp + evening ramp + noise
    cpu_base = 15 + is_trading * 25 + is_evening * 15 + rng.normal(0, 8, n)

    # Phase 3 spikes: 3 random 90-min windows during the week
    phase3_windows = rng.choice(n - 90, size=3, replace=False)
    cpu_spike = np.zeros(n)
    for start_idx in phase3_windows:
        cpu_spike[start_idx:start_idx + 90] = rng.normal(60, 10, 90)
    cpu_pct = np.clip(cpu_base + cpu_spike, 2, 100)

    df_machine["cpu_pct"] = np.round(cpu_pct, 1)
    df_machine["cpu_queue"] = np.round(np.clip((cpu_pct - 60) / 15 + rng.normal(0, 0.5, n), 0, 20), 1)

    total_ram = 32.0
    ram_used_baseline = 8 + is_trading * 4 + is_evening * 3
    ram_used_spike = np.zeros(n)
    for start_idx in phase3_windows:
        ram_used_spike[start_idx:start_idx + 90] = rng.normal(10, 2, 90)
    ram_used = np.clip(ram_used_baseline + ram_used_spike + rng.normal(0, 1, n), 3, total_ram - 0.5)
    df_machine["ram_total_gb"] = total_ram
    df_machine["ram_used_gb"] = np.round(ram_used, 2)
    df_machine["ram_avail_gb"] = np.round(total_ram - ram_used, 2)
    df_machine["ram_committed_pct"] = np.round(np.clip(ram_used / total_ram * 100 + 10, 20, 99), 1)

    df_machine["disk_pct"] = np.round(np.clip(rng.normal(15, 10, n) + (cpu_spike > 0) * 40, 0, 100), 1)
    df_machine["disk_queue"] = np.round(np.clip(df_machine["disk_pct"] / 30 + rng.normal(0, 0.3, n), 0, 10), 2)
    df_machine["net_kbps_in"]  = np.round(np.clip(rng.exponential(500, n) + is_trading * 3000, 10, 100000), 1)
    df_machine["net_kbps_out"] = np.round(np.clip(rng.exponential(200, n) + is_trading * 500, 5, 50000), 1)

    # Chromium footprint grows through the day, resets each morning
    chrome_grow = 1 + (hour * 0.15) * (dow < 5)  # weekday hours accumulate tabs
    df_machine["chrome_ram_gb"] = np.round(np.clip(chrome_grow + rng.normal(0, 0.3, n), 0.5, 12), 2)

    # Python RAM tracks Phase 3 spikes closely
    python_ram = np.full(n, 0.3)
    for start_idx in phase3_windows:
        python_ram[start_idx:start_idx + 90] = rng.normal(9, 1.5, 90)
    df_machine["python_ram_gb"] = np.round(np.clip(python_ram + rng.normal(0, 0.2, n), 0.1, 20), 2)

    df_machine["top3_procs"] = "python:8.2;chrome:3.5;tws:2.1"

    # Process-level: one row per 5min per top process
    proc_ts = pd.date_range(start, end, freq="5min", inclusive="left")
    proc_rows = []
    categories_baseline = ["browser_chrome", "browser_comet", "trading_tws", "vscode", "onedrive", "other"]
    for ts in proc_ts:
        h = ts.hour; d = ts.dayofweek
        is_trading_now = (h >= 8) and (h < 15) and (d < 5)
        # Chrome always present, sized by time-of-day
        proc_rows.append({"ts_local": ts, "hostname": "MATT-DEV-SYN", "process_name": "chrome", "pid": 1000,
                          "rss_gb": round(1 + h * 0.15 + rng.normal(0, 0.3), 3), "cpu_seconds": h * 20,
                          "category": "browser_chrome", "cmdline_hint": "chrome.exe --type=renderer"})
        proc_rows.append({"ts_local": ts, "hostname": "MATT-DEV-SYN", "process_name": "comet", "pid": 1100,
                          "rss_gb": round(0.8 + h * 0.05 + rng.normal(0, 0.2), 3), "cpu_seconds": h * 15,
                          "category": "browser_comet", "cmdline_hint": "comet.exe"})
        if is_trading_now:
            proc_rows.append({"ts_local": ts, "hostname": "MATT-DEV-SYN", "process_name": "tws", "pid": 2000,
                              "rss_gb": round(2 + (h - 8) * 0.4 + rng.normal(0, 0.3), 3), "cpu_seconds": h * 10,
                              "category": "trading_tws", "cmdline_hint": "javaw jts.ini"})
        # Phase 3 python during spike windows only
        for start_idx in phase3_windows:
            spike_start = minutes[start_idx]
            spike_end = spike_start + timedelta(minutes=90)
            if spike_start <= ts <= spike_end:
                proc_rows.append({"ts_local": ts, "hostname": "MATT-DEV-SYN", "process_name": "python", "pid": 3000,
                                  "rss_gb": round(8 + rng.normal(0, 1.5), 3), "cpu_seconds": 300,
                                  "category": "python_jarvis", "cmdline_hint": "python -m jarvis_funnel.run --phase 3"})

    df_procs = pd.DataFrame(proc_rows)
    df_procs["ts_utc"] = df_procs["ts_local"]

    return df_machine, df_procs


def plot_dashboard(df_machine: pd.DataFrame, df_procs: pd.DataFrame, out_path: Path, title_suffix: str = ""):
    df_machine = classify_severity(df_machine)

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle(f"Jarvis Bottleneck Dashboard{title_suffix}", fontsize=14, fontweight="bold")

    # ── Panel 1: Contention heatmap (weekday × hour) ────────────────────────
    ax = axes[0, 0]
    df_machine["hour"] = df_machine["ts_local"].dt.hour
    df_machine["weekday"] = df_machine["ts_local"].dt.day_name()
    heatmap = df_machine.pivot_table(
        index="weekday", columns="hour", values="pressure_count", aggfunc="mean"
    )
    weekday_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    heatmap = heatmap.reindex([d for d in weekday_order if d in heatmap.index])
    im = ax.imshow(heatmap.fillna(0), aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=3)
    ax.set_xticks(range(len(heatmap.columns)))
    ax.set_xticklabels(heatmap.columns)
    ax.set_yticks(range(len(heatmap.index)))
    ax.set_yticklabels(heatmap.index)
    ax.set_xlabel("Hour of day (local)")
    ax.set_title("Contention heatmap · avg pressure score (0=green, 3=red)")
    plt.colorbar(im, ax=ax, shrink=0.7)

    # ── Panel 2: Resource utilization time series ───────────────────────────
    ax = axes[0, 1]
    df_hourly = df_machine.set_index("ts_local").resample("h").agg({
        "cpu_pct": "mean", "ram_used_gb": "mean", "disk_pct": "mean"
    })
    ax.plot(df_hourly.index, df_hourly["cpu_pct"], label="CPU %", color="tab:blue", linewidth=1.5)
    ax.plot(df_hourly.index, df_hourly["ram_used_gb"] / df_machine["ram_total_gb"].iloc[0] * 100,
            label="RAM % of total", color="tab:orange", linewidth=1.5)
    ax.plot(df_hourly.index, df_hourly["disk_pct"], label="Disk %", color="tab:green", linewidth=1.5, alpha=0.6)
    ax.axhline(85, color="red", linestyle="--", alpha=0.4, label="Pressure threshold")
    ax.set_ylim(0, 100)
    ax.set_ylabel("% utilization (hourly avg)")
    ax.set_title("Resource utilization over the week")
    ax.legend(loc="upper right", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %m-%d"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

    # ── Panel 3: Top-3 killers bar chart ────────────────────────────────────
    ax = axes[1, 0]
    if not df_procs.empty:
        cat_stats = df_procs.groupby("category").agg(
            rss_p95=("rss_gb", lambda s: s.quantile(0.95)),
            hours_active=("ts_local", lambda s: len(s) * 5 / 60),
        ).reset_index()
        cat_stats["killer_score"] = cat_stats["rss_p95"] * cat_stats["hours_active"]
        cat_stats = cat_stats.sort_values("killer_score", ascending=False).head(5)

        colors = ["#d62728", "#ff7f0e", "#ffbb78", "#98df8a", "#c5b0d5"]
        bars = ax.barh(cat_stats["category"][::-1], cat_stats["rss_p95"][::-1],
                       color=colors[:len(cat_stats)][::-1])
        ax.set_xlabel("p95 RSS (GB)")
        ax.set_title("Top resource-holding categories · p95 RAM")
        for bar, hours in zip(bars, cat_stats["hours_active"][::-1]):
            ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                    f" {hours:.0f}h active", va="center", fontsize=9, color="gray")
    else:
        ax.text(0.5, 0.5, "No process telemetry", ha="center", va="center", transform=ax.transAxes, fontsize=12, color="gray")
        ax.set_title("Top resource-holding categories · p95 RAM")

    # ── Panel 4: Severity mix pie ───────────────────────────────────────────
    ax = axes[1, 1]
    sev_counts = df_machine["severity"].value_counts().reindex(["GREEN", "YELLOW", "RED"], fill_value=0)
    colors = {"GREEN": "#2ca02c", "YELLOW": "#ff7f0e", "RED": "#d62728"}
    ax.pie(sev_counts.values, labels=[f"{k}\n{v} ({v/sev_counts.sum()*100:.1f}%)" for k, v in sev_counts.items()],
           colors=[colors[k] for k in sev_counts.index], startangle=90,
           wedgeprops={"edgecolor": "white", "linewidth": 2})
    ax.set_title(f"Severity mix · {len(df_machine)} samples")

    plt.tight_layout()
    plt.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close()
    return out_path


def summarize(df_machine: pd.DataFrame, df_procs: pd.DataFrame) -> str:
    """Text summary that accompanies the chart."""
    df_machine = classify_severity(df_machine)
    n = len(df_machine)
    if n == 0:
        return "No telemetry data available."
    sev = df_machine["severity"].value_counts(normalize=True) * 100

    lines = [
        f"Samples: {n} machine + {len(df_procs)} process rows",
        f"Severity mix: {sev.get('GREEN', 0):.1f}% GREEN · {sev.get('YELLOW', 0):.1f}% YELLOW · {sev.get('RED', 0):.1f}% RED",
    ]
    # Worst hour
    df_machine["hour_bucket"] = df_machine["ts_local"].dt.floor("h")
    worst = df_machine.groupby("hour_bucket")["pressure_count"].mean().idxmax()
    worst_val = df_machine.groupby("hour_bucket")["pressure_count"].mean().max()
    lines.append(f"Worst hour: {worst:%a %m-%d %H:00} (avg pressure {worst_val:.1f}/4)")

    if not df_procs.empty:
        cat_stats = df_procs.groupby("category").agg(
            rss_p95=("rss_gb", lambda s: s.quantile(0.95)),
            hours_active=("ts_local", lambda s: len(s) * 5 / 60),
        )
        cat_stats["killer_score"] = cat_stats["rss_p95"] * cat_stats["hours_active"]
        top = cat_stats.nlargest(3, "killer_score")
        lines.append("Top-3 killers:")
        for cat, row in top.iterrows():
            lines.append(f"  {cat}: p95={row['rss_p95']:.1f}GB, active {row['hours_active']:.0f}h")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry-dir", type=Path,
        default=Path(os.environ.get("OneDrive", ".")) / "jarvis-trader-handoff" / "telemetry")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic data instead of reading OneDrive")
    args = parser.parse_args()

    if args.synthetic:
        print("=== SYNTHETIC DATA MODE — this is NOT your real data ===")
        df_m, df_p = make_synthetic_data(args.days)
        suffix = " · SYNTHETIC DEMO DATA · NOT REAL"
    else:
        if not args.telemetry_dir.exists():
            print(f"Telemetry dir not found: {args.telemetry_dir}", file=sys.stderr)
            sys.exit(2)
        df_m = load_machine_telemetry(args.telemetry_dir, args.days)
        df_p = load_process_telemetry(args.telemetry_dir, args.days)
        if df_m.empty:
            print("No machine telemetry found. Run the sampler for at least a few hours first.")
            sys.exit(1)
        suffix = f" · {args.days}-day window"

    out_png = args.out_dir / f"bottleneck_dashboard_{datetime.now():%Y-%m-%d}.png"
    plot_dashboard(df_m, df_p, out_png, suffix)
    summary = summarize(df_m, df_p)

    print(f"\nWrote: {out_png}\n")
    print(summary)


if __name__ == "__main__":
    main()
