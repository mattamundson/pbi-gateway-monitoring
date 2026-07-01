"""
analyze_contention.py — surface real resource contention from telemetry samples.

USAGE
    python analyze_contention.py
    python analyze_contention.py --days 14
    python analyze_contention.py --telemetry-dir "C:\\Users\\matt\\OneDrive\\jarvis-trader-handoff\\telemetry"

WHAT IT DOES
    1. Loads every telemetry_YYYY-MM-DD.csv from the OneDrive folder.
    2. Classifies each sample as one of:
         GREEN  — no contention
         YELLOW — at least one resource pressured
         RED    — multi-resource contention OR sustained pressure
    3. Identifies peak hours: the worst hour (by RED-sample count) per day.
    4. Cross-tabs Chrome/Comet RAM and Python RAM against contention events to
       answer "is it the agent eating my machine or my backtests?"
    5. Prints a markdown report + saves the most-contention hour's process top-3
       to a CSV for forensic review.

CONTENTION DEFINITIONS (these are the actual thresholds — read and adjust)
    cpu_pressure        cpu_pct > 85 for 60s rolling avg
    cpu_queue_pressure  cpu_queue > 2 (threads waiting for CPU)
    ram_pressure        ram_avail_gb < 2 OR ram_committed_pct > 85
    disk_pressure       disk_queue > 2 OR disk_pct > 90 sustained
    net_pressure        sustained net > 80% of typical link capacity
                        (heuristic — set NET_CAP_MBPS below)
"""

from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("pandas required. Install: pip install pandas", file=sys.stderr)
    sys.exit(2)


# ── Thresholds (tune these to your machine) ─────────────────────────────────
CPU_PCT_HIGH         = 85
CPU_QUEUE_HIGH       = 2
RAM_AVAIL_LOW_GB     = 2
RAM_COMMIT_HIGH_PCT  = 85
DISK_QUEUE_HIGH      = 2
DISK_PCT_HIGH        = 90
NET_CAP_MBPS         = 100        # adjust to your real link speed
NET_PRESSURE_RATIO   = 0.80


def load_telemetry(telemetry_dir: Path, days: int) -> pd.DataFrame:
    cutoff = datetime.now() - timedelta(days=days)
    frames = []
    for csv in sorted(telemetry_dir.glob("telemetry_*.csv")):
        # filename: telemetry_2026-06-30.csv
        try:
            date_str = csv.stem.replace("telemetry_", "")
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if day < cutoff:
            continue
        df = pd.read_csv(csv)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts_local"] = pd.to_datetime(df["ts_local"])
    df = df.sort_values("ts_local").reset_index(drop=True)
    return df


def classify(df: pd.DataFrame) -> pd.DataFrame:
    """Add boolean pressure flags + composite severity."""
    df = df.copy()
    df["cpu_pressure"]   = df["cpu_pct"] > CPU_PCT_HIGH
    df["cpu_q_pressure"] = df["cpu_queue"] > CPU_QUEUE_HIGH
    df["ram_pressure"]   = (df["ram_avail_gb"] < RAM_AVAIL_LOW_GB) | \
                           (df["ram_committed_pct"] > RAM_COMMIT_HIGH_PCT)
    df["disk_pressure"]  = (df["disk_queue"] > DISK_QUEUE_HIGH) | \
                           (df["disk_pct"] > DISK_PCT_HIGH)
    net_kbps_high        = NET_CAP_MBPS * 1024 * NET_PRESSURE_RATIO / 8  # mbps→KBps
    df["net_pressure"]   = (df["net_kbps_in"] + df["net_kbps_out"]) > net_kbps_high

    flag_cols = ["cpu_pressure","cpu_q_pressure","ram_pressure","disk_pressure","net_pressure"]
    df["pressure_count"] = df[flag_cols].sum(axis=1)

    def sev(n):
        if n == 0: return "GREEN"
        if n == 1: return "YELLOW"
        return "RED"
    df["severity"] = df["pressure_count"].apply(sev)

    return df


def peak_hours(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """Worst hours by RED-sample count."""
    df = df.copy()
    df["hour_bucket"] = df["ts_local"].dt.floor("h")
    grouped = df.groupby("hour_bucket").agg(
        n_samples=("severity", "size"),
        n_red=("severity", lambda s: (s == "RED").sum()),
        n_yellow=("severity", lambda s: (s == "YELLOW").sum()),
        cpu_max=("cpu_pct", "max"),
        cpu_p95=("cpu_pct", lambda s: s.quantile(0.95)),
        ram_used_max=("ram_used_gb", "max"),
        ram_avail_min=("ram_avail_gb", "min"),
        disk_queue_max=("disk_queue", "max"),
        chrome_ram_max=("chrome_ram_gb", "max"),
        python_ram_max=("python_ram_gb", "max"),
    ).reset_index()
    grouped["red_pct"] = (grouped["n_red"] / grouped["n_samples"] * 100).round(1)
    return grouped.sort_values("n_red", ascending=False).head(top_n)


def attribution(df: pd.DataFrame) -> dict:
    """Is contention correlated with Chrome/Comet, Python, or neither?"""
    red = df[df["severity"] == "RED"]
    green = df[df["severity"] == "GREEN"]
    if red.empty:
        return {"verdict": "no_red_events", "detail": "Nothing to attribute."}
    out = {
        "red_samples": int(len(red)),
        "green_samples": int(len(green)),
        "chrome_ram_red_avg_gb":  round(red["chrome_ram_gb"].mean(), 2),
        "chrome_ram_green_avg_gb": round(green["chrome_ram_gb"].mean(), 2) if not green.empty else None,
        "python_ram_red_avg_gb":  round(red["python_ram_gb"].mean(), 2),
        "python_ram_green_avg_gb": round(green["python_ram_gb"].mean(), 2) if not green.empty else None,
    }
    # Heuristic verdict
    chrome_delta = (out["chrome_ram_red_avg_gb"] or 0) - (out["chrome_ram_green_avg_gb"] or 0)
    python_delta = (out["python_ram_red_avg_gb"] or 0) - (out["python_ram_green_avg_gb"] or 0)
    if python_delta > chrome_delta and python_delta > 1:
        verdict = "python_dominant"
    elif chrome_delta > python_delta and chrome_delta > 1:
        verdict = "chromium_dominant"
    elif python_delta > 1 and chrome_delta > 1:
        verdict = "both_significant"
    else:
        verdict = "neither_dominant_check_top3_procs"
    out["verdict"] = verdict
    out["chrome_delta_gb"] = round(chrome_delta, 2)
    out["python_delta_gb"] = round(python_delta, 2)
    return out


def format_report(df: pd.DataFrame, peaks: pd.DataFrame, attr: dict, days: int) -> str:
    n_total = len(df)
    if n_total == 0:
        return "# Contention Report\n\nNo telemetry samples found.\n"

    counts = df["severity"].value_counts().to_dict()
    pct = {k: round(v / n_total * 100, 1) for k, v in counts.items()}

    lines = []
    lines.append(f"# Resource Contention Report — last {days} days")
    lines.append(f"_Generated: {datetime.now():%Y-%m-%d %H:%M %Z}_  ·  _{n_total} samples_")
    lines.append("")
    lines.append("## Severity mix")
    lines.append(f"- 🟢 GREEN  : {counts.get('GREEN', 0):>6} ({pct.get('GREEN', 0):.1f}%)")
    lines.append(f"- 🟡 YELLOW : {counts.get('YELLOW', 0):>6} ({pct.get('YELLOW', 0):.1f}%)")
    lines.append(f"- 🔴 RED    : {counts.get('RED', 0):>6} ({pct.get('RED', 0):.1f}%)")
    lines.append("")

    lines.append("## Worst 5 hours by RED samples")
    if peaks.empty or peaks["n_red"].sum() == 0:
        lines.append("_No RED hours detected._")
    else:
        lines.append("| hour | red% | cpu_p95 | ram_avail_min_gb | disk_q_max | chrome_max_gb | python_max_gb |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for _, r in peaks.iterrows():
            lines.append(
                f"| {r['hour_bucket']:%a %m-%d %H:00} | {r['red_pct']:.0f}% | "
                f"{r['cpu_p95']:.0f} | {r['ram_avail_min']:.1f} | "
                f"{r['disk_queue_max']:.1f} | {r['chrome_ram_max']:.1f} | {r['python_ram_max']:.1f} |"
            )
    lines.append("")

    lines.append("## Attribution: what's driving the RED events?")
    lines.append(f"- Verdict: **{attr.get('verdict', 'unknown')}**")
    if "chrome_delta_gb" in attr:
        lines.append(f"- Chromium RAM during RED vs GREEN: +{attr['chrome_delta_gb']} GB")
    if "python_delta_gb" in attr:
        lines.append(f"- Python RAM during RED vs GREEN: +{attr['python_delta_gb']} GB")
    lines.append("")
    lines.append("Reading the verdict:")
    lines.append("- `chromium_dominant` → Comet/Chrome agent work is your contention source. Consider routing more agent tasks to the web-app cloud surface.")
    lines.append("- `python_dominant` → jarvis-trader backtests are your contention source. Surface choice is moot; you need either more RAM, better parallelism in your backtest engine, or to throttle Phase 3 concurrency.")
    lines.append("- `both_significant` → Don't run Phase 3 + Comet agent tasks simultaneously. Or buy RAM.")
    lines.append("- `neither_dominant_check_top3_procs` → Something else (Databricks, Docker, IBKR TWS) is the culprit. Open the per-hour CSV to find it.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry-dir", type=Path,
        default=Path(os.environ.get("OneDrive", "")) / "jarvis-trader-handoff" / "telemetry")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    args = parser.parse_args()

    if not args.telemetry_dir.exists():
        print(f"Telemetry dir not found: {args.telemetry_dir}", file=sys.stderr)
        sys.exit(2)

    df = load_telemetry(args.telemetry_dir, args.days)
    if df.empty:
        print("No telemetry samples in the requested window.")
        return

    df = classify(df)
    peaks = peak_hours(df)
    attr = attribution(df)

    report = format_report(df, peaks, attr, args.days)
    out_md = args.out_dir / f"contention_report_{datetime.now():%Y-%m-%d}.md"
    out_md.write_text(report, encoding="utf-8")

    # Dump the worst hour's full sample set for forensic review
    if not peaks.empty and peaks["n_red"].iloc[0] > 0:
        worst_hour = peaks["hour_bucket"].iloc[0]
        mask = df["ts_local"].dt.floor("h") == worst_hour
        forensic = df.loc[mask].copy()
        out_csv = args.out_dir / f"worst_hour_{worst_hour:%Y-%m-%d_%H}.csv"
        forensic.to_csv(out_csv, index=False)
        print(f"Wrote {out_md} and {out_csv}")
    else:
        print(f"Wrote {out_md}")

    print()
    print(report)


if __name__ == "__main__":
    main()
