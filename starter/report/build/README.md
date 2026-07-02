# Headless `.pbit` builder

> **Label: [NET-NEW] · [Unverified]** — packs a valid template; DAX + DirectLake
> binding are proven only on one Power BI Desktop pass against a live Lakehouse.

`build_pbit.py` produces a DirectLake **semantic-model template** (`.pbit`) with **no
Power BI Desktop involvement** — it parses the repo's `measures.dax` + `measures_v2.dax`,
assembles a TMSL model (12 tables, DirectLake partitions, a hidden `_Measures` table with
all 50 measures), writes a minimal PbixProj, and packs it with `pbi-tools.core`.

## Why `.pbit` and not `.pbix`

A model-bearing **`.pbix`** embeds a *serialized Analysis Services tabular database* that
**only Power BI Desktop's engine writes**. The one headless tool that exists,
[`pbi-tools.core`](https://github.com/pbi-tools/pbi-tools), says so in its own `--help`:

> *"the PBIX output is supported only for report-only projects (thin reports), and **PBIT
> for projects containing a data model**."*

So a model `.pbix` is **categorically Desktop-only** — not a limitation of this repo. The
`.pbit` template is the correct fully-headless artifact, and is arguably the *better*
distributable: open it in Desktop, point it at your Lakehouse, refresh.

## Run it

```bash
# 1. get pbi-tools.core (win-x64) once:
#    https://github.com/pbi-tools/pbi-tools/releases  ->  pbi-tools.core.<ver>_win-x64.zip
# 2. build:
python starter/report/build/build_pbit.py --pbitools /path/to/pbi-tools.core.exe
#    -> starter/report/dist/gwmon_model.pbit   (also honors env PBITOOLS_CORE)
```

Without `--pbitools` it still writes the PbixProj folder (model + parts) and tells you where —
so you can inspect or pack later.

## What ships

| File | What |
|------|------|
| `build_pbit.py` | the generator (DAX -> TMSL -> PbixProj -> `.pbit`) |
| `validate_report_bindings.py` | headless binding validator (report queryRefs vs model) |
| `../dist/gwmon_model.pbit` | the packed template (12 tables, 50 measures, DirectLake) |

## Validate the report binds (no Desktop needed)

```bash
python starter/report/build/validate_report_bindings.py
#   cross-checks every visual queryRef in gateway_monitor_v2.report.json against
#   the packed .pbit model; exits non-zero (fail-closed) on any unresolved binding.
```

This is the one *"does the report actually bind"* proof achievable headlessly: it
can't render a visual, but it proves every field a visual references exists in the
model. Run it after editing either the report or the measures. Current status: **PASS**
(157 queryRefs, 113 distinct, 0 unresolved).

## Finalize in Desktop (the one manual pass) — RECOMMENDED: open the PBIP

The easiest path is the assembled **PBIP project** (`../pbip/gwmon.pbip`) — one double-click
loads the model **and** the 14-page report together:

1. Double-click **`starter/report/pbip/gwmon.pbip`** in Power BI Desktop (Sept 2024+ with the
   PBIP preview enabled: *Options > Preview features > Power BI Project (.pbip) save*).
2. Edit the `DirectLakeSource` expression -> your Lakehouse SQL analytics endpoint.
3. Add `dim_date` values (mark-as-date-table is pre-flagged) so time-intelligence resolves.
4. *Save As `.pbix`* — that Desktop step is the only way to get a model-bearing `.pbix`.

Rebuild the PBIP after editing the report/measures: `python build_pbip.py` (auto-discovers the
pinned `pbi-tools.core`; regenerates the TMDL model from the `.dax` files).

**Fallback** if your Desktop build predates PBIP support: open `dist/gwmon_model.pbit`, then
*File > Open* `../gateway_monitor_v2.report.json`, then *Save As `.pbix`*.
