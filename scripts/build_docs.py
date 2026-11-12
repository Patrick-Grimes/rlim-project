"""
scripts/build_docs.py
Compiles the standalone docs/index.html dashboard
integrating data-dict.yaml (v0.4.0), Plotly visualizations, live regression tables,
and formal methodological transparency statements.
"""

import base64
from pathlib import Path
import re
import duckdb
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yaml

# -------------------------------------------------------------------------
# 1. Read Data Dictionary
# -------------------------------------------------------------------------
dict_path = Path("data-dict.yaml")
if not dict_path.exists():
    dict_path = Path("docs/data-dict.yaml")

with open(dict_path, "r") as f:
    spec = yaml.safe_load(f)

version = spec.get("$version", "0.4.0")

# -------------------------------------------------------------------------
# 2. Query DuckDB Warehouse
# -------------------------------------------------------------------------
DB_PATH = "data/transit_warehouse.duckdb"
con = duckdb.connect(DB_PATH, read_only=True)
con.install_extension("spatial")
con.load_extension("spatial")

# A. Silver Queries
df_buffers = con.execute("""
    SELECT 
        transit_buffer_tier,
        COUNT(*) AS parcel_count,
        ROUND(SUM(calc_acreage), 1) AS total_acres,
        ROUND(AVG(dist_to_station_meters), 1) AS mean_dist_m
    FROM silver.parcels_spatial
    GROUP BY transit_buffer_tier
    ORDER BY mean_dist_m ASC;
""").df()

df_zoning = con.execute("""
    SELECT 
        transit_buffer_tier,
        zoning_category,
        COUNT(*) AS count
    FROM silver.parcels_spatial
    GROUP BY transit_buffer_tier, zoning_category;
""").df()

df_hedonic = con.execute("""
    SELECT 
        p.parcel_id,
        p.dist_to_station_meters,
        p.dist_to_cbd_ft / 5280.0 AS dist_to_cbd_miles,
        p.transit_buffer_tier,
        p.zoning_category,
        s.nominal_price,
        s.real_price_2026,
        s.ppsf_real_2026,
        LN(s.ppsf_real_2026) AS log_real_ppsf,
        s.sale_year
    FROM silver.parcels_spatial p
    INNER JOIN silver.sales_standardized s 
        ON p.parcel_id = s.parcel_id
    WHERE s.ppsf_real_2026 >= 0.50
      AND s.nominal_price >= 10000
    USING SAMPLE 10000;
""").df()

df_trends = con.execute("""
    SELECT 
        sale_year,
        ROUND(MEDIAN(nominal_price), 0) AS median_nominal,
        ROUND(MEDIAN(real_price_2026), 0) AS median_real_2026,
        COUNT(*) AS transaction_count
    FROM silver.sales_standardized
    WHERE nominal_price >= 10000
    GROUP BY sale_year
    ORDER BY sale_year ASC;
""").df()

# B. Gold Queries
df_did_trends = con.execute("""
    SELECT 
        sale_year,
        CASE WHEN nearest_line = 'BLE' THEN 'Treated: BLE Walkshed (<= 0.5 mi)'
             ELSE 'Control: Matthews Corridor (<= 0.5 mi)'
        END AS cohort,
        ROUND(MEDIAN(ppsf_real_2026), 2) AS median_real_ppsf,
        ROUND(AVG(ppsf_real_2026), 2) AS mean_real_ppsf,
        COUNT(*) AS tx_count
    FROM gold.hedonic_sample
    WHERE nearest_line IN ('BLE', 'Control_Matthews') AND in_walkshed = 1
    GROUP BY sale_year, cohort
    ORDER BY sale_year ASC, cohort;
""").df()

df_corridor_box = con.execute("""
    SELECT 
        CASE 
            WHEN nearest_line = 'BL' THEN 'Blue Line (South)'
            WHEN nearest_line = 'BLE' THEN 'BLE (Northeast)'
            WHEN nearest_line = 'Red Line' THEN 'Red Line (25-Mile Corridor)'
            ELSE 'Matthews (Control)'
        END AS corridor_label,
        ppsf_real_2026,
        transit_buffer_tier
    FROM gold.hedonic_sample
    WHERE ppsf_real_2026 BETWEEN 5.0 AND 120.0
    USING SAMPLE 20000;
""").df()

df_gradient = con.execute("""
    SELECT 
        dist_to_station_meters,
        ppsf_real_2026,
        LN(ppsf_real_2026) AS log_real_ppsf,
        CASE WHEN era_revenue_service = 1 THEN 'Revenue Service (2018-2026)'
             ELSE 'Pre-Opening / Construction (2012-2017)'
        END AS transit_era
    FROM gold.hedonic_sample
    WHERE nearest_line IN ('BL', 'BLE') 
      AND dist_to_station_meters <= 3000
      AND ppsf_real_2026 BETWEEN 2.0 AND 150.0
    USING SAMPLE 10000;
""").df()

df_cbd_zoning = con.execute("""
    SELECT 
        ROUND(dist_to_cbd_miles, 2) AS dist_to_cbd_miles,
        ppsf_real_2026,
        LN(ppsf_real_2026) AS log_real_ppsf,
        zoning_category
    FROM gold.hedonic_sample
    WHERE dist_to_cbd_miles <= 25.0
      AND ppsf_real_2026 BETWEEN 2.0 AND 150.0
    USING SAMPLE 10000;
""").df()

df_liquidity_trends = con.execute("""
    SELECT 
        sale_year,
        CASE 
            WHEN nearest_line = 'BLE' THEN 'Treated: BLE Walkshed'
            WHEN nearest_line = 'Control_Matthews' THEN 'Control: Matthews Corridor'
            WHEN nearest_line = 'Red Line' THEN 'Red Line (Full Corridor)'
            ELSE nearest_line
        END AS corridor_label,
        ROUND(SUM(tx_count)::DOUBLE / NULLIF(SUM(total_parcels), 0) * 100.0, 2) AS turnover_rate_pct
    FROM gold.transaction_velocity
    WHERE nearest_line IN ('BLE', 'Control_Matthews', 'Red Line') AND in_walkshed = 1
    GROUP BY sale_year, corridor_label
    ORDER BY sale_year ASC;
""").df()

con.close()

# -------------------------------------------------------------------------
# 3. Dynamic Markdown Table Parser
# -------------------------------------------------------------------------
def clean_cell_text(text: str) -> str:
    """Formats markdown and math expressions into clean HTML elements."""
    text = text.strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.*?)`", r"<code>\1</code>", text)
    text = text.replace(r"$\ln(\text{Real PPSF})$", "<code>ln(ppsf_real_2026)</code>")
    text = text.replace(r"$\beta$", "&beta;")
    text = text.replace(r"\times", "&times;")
    text = text.replace(r"\le", "&le;")
    text = text.replace(r"\hat{\phi}", "&phi;")
    text = text.replace("$", "")
    return text

def load_markdown_table(file_path: Path) -> str:
    """Extracts the first markdown table from a file and returns an HTML table."""
    if not file_path.exists():
        return f'<div class="alert alert-warning py-1 my-2 small">Artifact {file_path.name} pending generation. Run estimation script first.</div>'

    lines = file_path.read_text(encoding="utf-8").splitlines()
    table_lines = [l.strip() for l in lines if l.strip().startswith("|") and l.strip().endswith("|")]

    if len(table_lines) < 2:
        return f'<div class="alert alert-warning py-1 my-2 small">No table found in {file_path.name}.</div>'

    headers = [clean_cell_text(c) for c in table_lines[0].strip("|").split("|")]
    
    rows = []
    # Skip separator row (table_lines[1])
    for line in table_lines[2:]:
        cols = [clean_cell_text(c) for c in line.strip("|").split("|")]
        rows.append(cols)

    th_html = "".join(f"<th>{h}</th>" for h in headers)
    tbody_html = ""
    for r in rows:
        td_html = "".join(f"<td>{c}</td>" for c in r)
        tbody_html += f"<tr>{td_html}</tr>\n"

    return f"""
    <div class="table-responsive">
        <table class="table table-bordered table-sm align-middle mt-2">
            <thead class="table-light"><tr>{th_html}</tr></thead>
            <tbody>{tbody_html}</tbody>
        </table>
    </div>
    """

regression_table_html = load_markdown_table(Path("docs/regression_summary.md"))
liquidity_table_html = load_markdown_table(Path("docs/liquidity_summary.md"))

# -------------------------------------------------------------------------
# 4. Interactive Plotly Figures
# -------------------------------------------------------------------------
theme = dict(
    margin=dict(l=50, r=40, t=35, b=45),
    template="plotly_white",
    hovermode="closest",
    font=dict(family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif")
)

fig1 = px.bar(
    df_buffers, x="transit_buffer_tier", y="parcel_count", text="parcel_count",
    color="transit_buffer_tier", color_discrete_sequence=["#2b5c8f", "#4682b4", "#7fb3d5", "#aed6f1"],
    labels={"transit_buffer_tier": "Walkshed Tier", "parcel_count": "Total Parcels"}
)
fig1.update_traces(textposition="outside")
fig1.update_layout(theme, showlegend=False)

fig2 = px.bar(
    df_zoning, x="transit_buffer_tier", y="count", color="zoning_category",
    barmode="stack", color_discrete_sequence=px.colors.qualitative.Prism,
    labels={"transit_buffer_tier": "Walkshed Tier", "count": "Parcel Count", "zoning_category": "Zoning"}
)
fig2.update_layout(theme)

fig3 = px.scatter(
    df_hedonic, x="dist_to_station_meters", y="log_real_ppsf", color="transit_buffer_tier",
    hover_data=["parcel_id", "real_price_2026", "sale_year"], opacity=0.5,
    labels={"dist_to_station_meters": "Distance to Station (Meters)", "log_real_ppsf": "ln(Real PPSF 2026 USD)"}
) if not df_hedonic.empty else go.Figure().add_annotation(text="No Sales Data", showarrow=False)
fig3.update_layout(theme)

fig4 = go.Figure()
if not df_trends.empty:
    fig4.add_trace(go.Scatter(x=df_trends["sale_year"], y=df_trends["median_real_2026"], mode="lines+markers", name="Real 2026 USD (BLS CPI-U)", line=dict(color="#27ae60", width=3)))
    fig4.add_trace(go.Scatter(x=df_trends["sale_year"], y=df_trends["median_nominal"], mode="lines+markers", name="Nominal Deed Price", line=dict(color="#7f8c8d", width=2, dash="dot")))
fig4.update_layout(theme, xaxis_title="Sale Year", yaxis_title="Median Price", yaxis_tickprefix="$", xaxis=dict(tickmode="linear", dtick=1))

gold_fig1 = px.line(
    df_did_trends, x="sale_year", y="median_real_ppsf", color="cohort",
    markers=True,
    color_discrete_map={
        "Treated: BLE Walkshed (<= 0.5 mi)": "#1f77b4",
        "Control: Matthews Corridor (<= 0.5 mi)": "#7f8c8d"
    },
    labels={"sale_year": "Transaction Year", "median_real_ppsf": "Median Real PPSF ($/sqft)", "cohort": "Cohort"}
)
gold_fig1.add_vline(x=2017.5, line_dash="dash", line_color="#d9534f", annotation_text="BLE Revenue Opening (2018)")
gold_fig1.add_vrect(x0=2014, x1=2017.5, fillcolor="#f0ad4e", opacity=0.12, layer="below", line_width=0, annotation_text="Construction Window")
gold_fig1.update_layout(theme, yaxis_tickprefix="$", xaxis=dict(tickmode="linear", dtick=1))

gold_fig2 = px.box(
    df_corridor_box, x="corridor_label", y="ppsf_real_2026", color="corridor_label",
    color_discrete_sequence=["#2b5c8f", "#4682b4", "#e67e22", "#95a5a6"],
    category_orders={"corridor_label": ["Blue Line (South)", "BLE (Northeast)", "Red Line (25-Mile Corridor)", "Matthews (Control)"]},
    labels={"corridor_label": "Corridor Alignment", "ppsf_real_2026": "Real PPSF (2026 USD)"}
)
gold_fig2.update_layout(theme, showlegend=False, yaxis_tickprefix="$")

gold_fig3 = px.scatter(
    df_gradient, x="dist_to_station_meters", y="log_real_ppsf", color="transit_era",
    opacity=0.35, trendline="ols",
    color_discrete_map={"Revenue Service (2018-2026)": "#27ae60", "Pre-Opening / Construction (2012-2017)": "#7f8c8d"},
    labels={"dist_to_station_meters": "Distance to Station (Meters)", "log_real_ppsf": "ln(Real PPSF)", "transit_era": "Policy Era"}
)
gold_fig3.update_layout(theme, legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99))

gold_fig4 = px.scatter(
    df_cbd_zoning, x="dist_to_cbd_miles", y="log_real_ppsf", color="zoning_category",
    opacity=0.35, trendline="ols",
    color_discrete_map={"TOD": "#e74c3c", "MFR": "#3498db", "OTHER_COMMERCIAL": "#f39c12", "SFR": "#2ecc71"},
    labels={"dist_to_cbd_miles": "Distance to Charlotte CBD (Miles)", "log_real_ppsf": "ln(Real PPSF)", "zoning_category": "Zoning"}
)
gold_fig4.update_layout(theme, legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99))

fig_liq = px.line(
    df_liquidity_trends, x="sale_year", y="turnover_rate_pct", color="corridor_label",
    markers=True,
    color_discrete_map={
        "Treated: BLE Walkshed": "#1f77b4", 
        "Control: Matthews Corridor": "#7f8c8d", 
        "Red Line (Full Corridor)": "#e67e22"
    },
    labels={"sale_year": "Closing Year", "turnover_rate_pct": "Sales per 100 Parcels (%)", "corridor_label": "Walkshed Cohort"}
)
fig_liq.add_vline(x=2012.8, line_dash="dot", line_color="#2ca02c", annotation_text="FTA FFGA Grant (2012)")
fig_liq.add_vrect(x0=2014, x1=2017.5, fillcolor="#f0ad4e", opacity=0.12, layer="below", line_width=0, annotation_text="Active Construction")
fig_liq.add_vline(x=2017.5, line_dash="dash", line_color="#d9534f", annotation_text="Revenue Service (2018)")
fig_liq.update_layout(theme, yaxis_ticksuffix="%", xaxis=dict(tickmode="linear", dtick=1))

def encode_img(path_str: str) -> str:
    p = Path(path_str)
    if p.exists():
        b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
        return f'<img src="data:image/png;base64,{b64}" class="img-fluid rounded border shadow-sm my-2" />'
    return '<div class="alert alert-warning py-1 my-2 small">Diagnostic artifact pending generation.</div>'

did_event_img = encode_img("docs/figures/did_parallel_trends.png")
liq_event_img = encode_img("docs/figures/liquidity_velocity_shocks.png")

# -------------------------------------------------------------------------
# 5. Render Standalone HTML Dashboard
# -------------------------------------------------------------------------
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RLIM Econometric & Spatial Documentation</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <style>
        body {{ padding: 2rem 2.5rem; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f8f9fa; color: #212529; }}
        .card {{ margin-bottom: 1.75rem; border-radius: 8px; border: 1px solid #dee2e6; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
        th {{ background-color: #f1f3f5; font-size: 0.88rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; }}
        td {{ font-size: 0.9rem; }}
        code {{ color: #d63384; font-size: 0.88rem; background-color: #f8f9fa; padding: 0.15rem 0.35rem; border-radius: 4px; border: 1px solid #e9ecef; }}
        .nav-tabs .nav-link {{ color: #495057; font-weight: 500; border: none; padding: 0.75rem 1.25rem; }}
        .nav-tabs .nav-link.active {{ font-weight: 700; color: #0d6efd; border-bottom: 3px solid #0d6efd; background: transparent; }}
        .badge-soft {{ background-color: #e7f1ff; color: #0c63e4; border: 1px solid #b6d4fe; }}
        .callout-transparency {{ border-left: 4px solid #0d6efd; background-color: #ffffff; padding: 1.25rem; border-radius: 0 8px 8px 0; margin-bottom: 1.5rem; }}
    </style>
</head>
<body>
    <div class="container-fluid" style="max-width: 1400px;">
        <header class="mb-4 pb-3 border-bottom d-flex justify-content-between align-items-end flex-wrap gap-2">
            <div>
                <div class="d-flex align-items-center gap-2 mb-1">
                    <h1 class="h3 fw-bold mb-0">Red Line Impact Model (RLIM)</h1>
                    <span class="badge badge-soft">Warehouse & Econometric Docs v{version}</span>
                </div>
                <p class="text-secondary mb-0 small" style="max-width: 950px;">
                    Transit Capitalization, Market Liquidity, and Displacement Risk across Mecklenburg County Rail Corridors. Evaluating the entire 25-mile CATS Red Line alignment (Uptown Charlotte to Mooresville) using a quasi-experimental LYNX Blue Line Extension benchmark.
                </p>
            </div>
            <div class="text-end small text-muted">
                Mecklenburg County Cadastral & Deed Pipeline<br>DuckDB Analytical Engine
            </div>
        </header>

        <ul class="nav nav-tabs mb-4" id="rlimTabs" role="tablist">
            <li class="nav-item"><button class="nav-link active" id="dict-tab" data-bs-toggle="tab" data-bs-target="#dict-panel">📋 Data Dictionary (v{version})</button></li>
            <li class="nav-item"><button class="nav-link" id="analytics-tab" data-bs-toggle="tab" data-bs-target="#analytics-panel">📊 Silver Spatial Features</button></li>
            <li class="nav-item"><button class="nav-link" id="econometrics-tab" data-bs-toggle="tab" data-bs-target="#econometrics-panel">📈 Gold Price Capitalization & DiD</button></li>
            <li class="nav-item"><button class="nav-link" id="liquidity-tab" data-bs-toggle="tab" data-bs-target="#liquidity-panel">⚡ Market Liquidity & Churn</button></li>
        </ul>

        <div class="tab-content">
            <!-- TAB 1: Data Dictionary -->
            <div class="tab-pane fade show active" id="dict-panel">
                <div class="alert alert-light border shadow-sm mb-4">
                    <h6 class="fw-bold mb-1">Architecture Overview</h6>
                    <p class="text-secondary small mb-0">
                        The pipeline operates on an idempotent DuckDB warehouse structured into raw ingestion (<strong>Bronze</strong>), standardized spatial features and deflated sales (<strong>Silver</strong>), and causal/econometric modeling views (<strong>Gold</strong>).
                    </p>
                </div>
"""

for t in spec.get("tables", []):
    html += f"""
                <div class="card">
                    <div class="card-header bg-dark text-white py-2 d-flex justify-content-between align-items-center">
                        <h6 class="mb-0"><code>{t['name']}</code> <span class="text-light fw-normal ms-2">— {t.get('label', '')}</span></h6>
                        <span class="badge bg-secondary">{len(t.get('columns', []))} Columns</span>
                    </div>
                    <div class="card-body">
                        <p class="card-text text-secondary mb-3 small">{t.get('description', '')}</p>
                        <div class="table-responsive">
                            <table class="table table-hover table-bordered mb-0 align-middle">
                                <thead>
                                    <tr>
                                        <th style="width: 22%;">Column Name</th>
                                        <th style="width: 14%;">Type</th>
                                        <th style="width: 44%;">Description & Econometric Specification</th>
                                        <th style="width: 20%;">Values / Range</th>
                                    </tr>
                                </thead>
                                <tbody>
    """
    for col in t.get("columns", []):
        ex = col.get("examples") or col.get("values") or col.get("range") or ""
        ex_str = f"<code>{ex}</code>" if ex else '<span class="text-muted">—</span>'
        html += f"""<tr>
            <td><strong>{col["name"]}</strong></td>
            <td><span class="badge bg-light text-dark border">{col.get("type", "any")}</span></td>
            <td>{col.get("description", "")}</td>
            <td><small>{ex_str}</small></td>
        </tr>"""
    html += """</tbody></table></div></div></div>"""

html += f"""
            </div>

            <!-- TAB 2: Silver Analytics -->
            <div class="tab-pane fade" id="analytics-panel">
                <div class="row g-4 mb-4">
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Parcel Stock by Transit Walkshed Tier</h6></div><div class="card-body p-2">{fig1.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Zoning Composition Across Buffer Tiers</h6></div><div class="card-body p-2">{fig2.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Semi-Log Real Land Value vs. Station Proximity</h6></div><div class="card-body p-2">{fig3.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Macro Deflation: Nominal vs. Real 2026 Dollars (BLS CPI-U)</h6></div><div class="card-body p-2">{fig4.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                </div>
            </div>

            <!-- TAB 3: Gold Econometrics (Price) -->
            <div class="tab-pane fade" id="econometrics-panel">
                
                <!-- Methodological Transparency Statement -->
                <div class="card border-primary callout-transparency">
                    <h5 class="fw-bold text-primary mb-2">Methodological Transparency Statement</h5>
                    <div class="row g-3 small text-secondary">
                        <div class="col-md-4">
                            <strong>1. Land Footprint vs. Structural Capital:</strong>
                            <p class="mt-1 mb-0">
                                Normalizing deed price by parcel footprint (<code>ppsf_real_2026</code>) isolates raw site assembly and redevelopment option value. For owner-occupied single-family residential properties, physical building characteristics (living area, structural condition) significantly influence transaction price. The model absorbs baseline structural scale through categorical zoning fixed effects (<code>C(zoning_category)</code>) and cross-corridor differencing.
                            </p>
                        </div>
                        <div class="col-md-4">
                            <strong>2. Assessed Value Allocation Limits:</strong>
                            <p class="mt-1 mb-0">
                                Municipal tax assessor splits frequently apply statutory allocation ratios (e.g., standardizing land to fixed percentages) that do not reflect rapid localized redevelopment pressure in urban transit walksheds. To avoid administrative appraisal measurement error, the dependent variable relies on verified arms-length market transactions.
                            </p>
                        </div>
                        <div class="col-md-4">
                            <strong>3. Spatial Heterogeneity Across Corridor Typologies:</strong>
                            <p class="mt-1 mb-0">
                                Evaluating the 25-mile corridor captures two distinct urban environments: high-density urban core infill and adaptive reuse (Center City to North Charlotte) versus suburban town centers (Huntersville, Cornelius, Davidson). Station-clustered standard errors and distance-to-CBD controls isolate localized platform accessibility from the regional monocentric wage gradient.
                            </p>
                        </div>
                    </div>
                </div>

                <div class="row g-4 mb-4">
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">1. DiD Parallel Trends: BLE Walkshed vs. Matthews Control</h6></div><div class="card-body p-2">{gold_fig1.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">2. Real Land Value Distributions by Regional Corridor</h6></div><div class="card-body p-2">{gold_fig2.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">3. Semi-Log Distance Gradient: Pre-Opening vs. Operational</h6></div><div class="card-body p-2">{gold_fig3.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">4. Monocentric Rent Gradient: Distance to CBD (Trade & Tryon)</h6></div><div class="card-body p-2">{gold_fig4.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                </div>

                <div class="card mb-4">
                    <div class="card-header bg-dark text-white py-2 d-flex justify-content-between align-items-center">
                        <h6 class="mb-0 fw-bold">Event-Study Dynamics & Regression Estimates</h6>
                        <span class="badge bg-secondary">Station-Clustered SEs</span>
                    </div>
                    <div class="card-body">
                        <div class="text-center">{did_event_img}</div>
                        
                        <h6 class="fw-bold mt-4">Econometric Model Output Summary (Live Artifact: <code>docs/regression_summary.md</code>)</h6>
                        {regression_table_html}
                        <div class="alert alert-light border small text-secondary mt-2 mb-0">
                            <strong>Event Study Takeaway:</strong> The statistically negligible post-opening interaction parameter indicates that transit capitalization occurred during the <strong>2014–2017 FTA grant and construction window</strong> prior to revenue operations.
                        </div>
                    </div>
                </div>
            </div>

            <!-- TAB 4: Market Liquidity & Churn -->
            <div class="tab-pane fade" id="liquidity-panel">
                <div class="row g-4 mb-4">
                    <div class="col-lg-12">
                        <div class="card">
                            <div class="card-header bg-light py-2 d-flex justify-content-between align-items-center">
                                <h6 class="mb-0 fw-bold">Annual Transaction Turnover Rate (Sales per 100 Parcels) Across Walksheds</h6>
                                <span class="badge bg-primary">Dynamic Plotly</span>
                            </div>
                            <div class="card-body p-2">
                                {fig_liq.to_html(full_html=False, include_plotlyjs=False)}
                            </div>
                        </div>
                    </div>
                </div>

                <div class="card mb-4">
                    <div class="card-header bg-dark text-white py-2 d-flex justify-content-between align-items-center">
                        <h6 class="mb-0 fw-bold">Market Liquidity & Commercial Churn Divergence</h6>
                        <span class="badge bg-secondary">GLM Poisson Panel</span>
                    </div>
                    <div class="card-body">
                        <div class="text-center">{liq_event_img}</div>
                        
                        <div class="alert alert-light border mt-4 mb-3 small">
                            <h6 class="fw-bold mb-1">Land Assembly & Planned Residential Development</h6>
                            <p class="mb-0 text-secondary">
                                Developers of planned residential subdivisions acquire land parcels primarily for aggregate development footprint rather than existing structural utility. Model L2 isolates this mechanism by testing triple-difference turnover divergence across commercial/TOD parcels and consumer housing stock.
                            </p>
                        </div>

                        <h6 class="fw-bold mt-3">Poisson GLM Rate Model Output (Live Artifact: <code>docs/liquidity_summary.md</code>)</h6>
                        {liquidity_table_html}
                        <div class="alert alert-light border small text-secondary mt-2 mb-0">
                            <strong>Theoretical Takeaway:</strong> Commercial and retail parcels in the treated Blue Line walkshed exhibited accelerated turnover. Institutional land assembly concentrates along commercial corridors ahead of revenue service, indicating that market liquidity adjusts before price levels reach equilibrium.
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(tab => {{
            tab.addEventListener('shown.bs.tab', () => window.dispatchEvent(new Event('resize')));
        }});
    </script>
</body>
</html>
"""

OUTPUT_FILE = Path("docs/index.html")
OUTPUT_FILE.write_text(html, encoding="utf-8")
print(f"✓ docs/index.html compiled successfully (v{version}) with live markdown table binding.")

if __name__ == "__main__":
    pass
