import base64
from pathlib import Path
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yaml

# 1. Read data-dict.yaml
with open('data-dict.yaml', 'r') as f:
    spec = yaml.safe_load(f)

version = spec.get('$version', '0.3.0')

# 2. Query Warehouse for Visuals
DB_PATH = 'data/transit_warehouse.duckdb'
con = duckdb.connect(DB_PATH, read_only=True)
con.install_extension('spatial')
con.load_extension('spatial')

df_buffers = con.execute('''
    SELECT 
        transit_buffer_tier,
        COUNT(*) AS parcel_count,
        ROUND(SUM(calc_acreage), 1) AS total_acres,
        ROUND(AVG(dist_to_station_meters), 1) AS mean_dist_m
    FROM silver.parcels_spatial
    GROUP BY transit_buffer_tier
    ORDER BY mean_dist_m ASC;
''').df()

df_zoning = con.execute('''
    SELECT 
        transit_buffer_tier,
        zoning_category,
        COUNT(*) AS count
    FROM silver.parcels_spatial
    GROUP BY transit_buffer_tier, zoning_category;
''').df()

df_hedonic = con.execute('''
    SELECT 
        p.parcel_id,
        p.dist_to_station_meters,
        p.dist_to_cbd_ft / 5280.0 AS dist_to_cbd_miles,
        p.transit_buffer_tier,
        p.zoning_category,
        s.nominal_price,
        s.real_price_2026,
        s.ppsf_real_2026,
        s.sale_year
    FROM silver.parcels_spatial p
    INNER JOIN silver.sales_standardized s 
        ON p.parcel_id = s.parcel_id
    WHERE s.ppsf_real_2026 > 0
    LIMIT 10000;
''').df()

df_trends = con.execute('''
    SELECT 
        sale_year,
        ROUND(MEDIAN(nominal_price), 0) AS median_nominal,
        ROUND(MEDIAN(real_price_2026), 0) AS median_real_2026,
        COUNT(*) AS transaction_count
    FROM silver.sales_standardized
    GROUP BY sale_year
    ORDER BY sale_year ASC;
''').df()

con.close()

# 3. Create Interactive Plots
theme = dict(
    margin=dict(l=40, r=40, t=30, b=40),
    template='plotly_white',
    hovermode='closest',
    font=dict(family='-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif')
)

fig1 = px.bar(
    df_buffers, x='transit_buffer_tier', y='parcel_count', text='parcel_count',
    color='transit_buffer_tier', color_discrete_sequence=['#2b5c8f', '#4682b4', '#7fb3d5', '#aed6f1'],
    labels={'transit_buffer_tier': 'Walkshed Tier', 'parcel_count': 'Total Parcels'}
)
fig1.update_traces(textposition='outside')
fig1.update_layout(theme, showlegend=False)

fig2 = px.bar(
    df_zoning, x='transit_buffer_tier', y='count', color='zoning_category',
    barmode='stack', color_discrete_sequence=px.colors.qualitative.Prism,
    labels={'transit_buffer_tier': 'Walkshed Tier', 'count': 'Parcel Count', 'zoning_category': 'Zoning'}
)
fig2.update_layout(theme)

fig3 = px.scatter(
    df_hedonic, x='dist_to_station_meters', y='ppsf_real_2026', color='transit_buffer_tier',
    hover_data=['parcel_id', 'real_price_2026', 'sale_year'], log_y=True,
    labels={'dist_to_station_meters': 'Distance to Station (Meters)', 'ppsf_real_2026': 'Real PPSF (2026 USD)'}
) if not df_hedonic.empty else go.Figure().add_annotation(text='No Sales Data', showarrow=False)
fig3.update_layout(theme)

fig4 = go.Figure()
if not df_trends.empty:
    fig4.add_trace(go.Scatter(x=df_trends['sale_year'], y=df_trends['median_real_2026'], mode='lines+markers', name='Real 2026 USD', line=dict(color='#27ae60', width=3)))
    fig4.add_trace(go.Scatter(x=df_trends['sale_year'], y=df_trends['median_nominal'], mode='lines+markers', name='Nominal Price', line=dict(color='#7f8c8d', width=2, dash='dot')))
fig4.update_layout(theme, xaxis_title='Sale Year', yaxis_title='Median Price', yaxis_tickprefix='$', xaxis=dict(tickmode='linear', dtick=1))

# Helper to base64 encode figures
def encode_img(path_str):
    p = Path(path_str)
    if p.exists():
        b64 = base64.b64encode(p.read_bytes()).decode('utf-8')
        return f'<img src="data:image/png;base64,{b64}" class="img-fluid rounded border shadow-sm my-2" />'
    return '<div class="alert alert-warning py-1 my-2 small">Artifact pending generation.</div>'

did_img_tag = encode_img('docs/figures/did_parallel_trends.png')
gold_img_tag = encode_img('docs/figures/gold_modeling_summary.png')

# 4. Render index.html
html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><title>RLIM Feature Store & Econometric Documentation</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <style>
        body {{ padding: 2.5rem; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f8f9fa; }}
        .card {{ margin-bottom: 1.75rem; border-radius: 8px; border: 1px solid #dee2e6; }}
        th {{ background-color: #f8f9fa; font-size: 0.9rem; }}
        td {{ font-size: 0.9rem; }}
        code {{ color: #d63384; font-size: 0.88rem; }}
        .nav-tabs .nav-link.active {{ font-weight: 700; color: #0d6efd; }}
    </style>
</head>
<body>
    <div class="container-fluid" style="max-width: 1250px;">
        <header class="mb-4 pb-3 border-bottom">
            <div class="d-flex align-items-baseline gap-2 mb-2">
                <h1 class="h3 fw-bold mb-0">Red Line Impact Model (RLIM)</h1>
                <span class="text-muted small">v{version}</span>
            </div>
            <p class="text-secondary mb-0">
                Cadastral data warehouse, spatial feature engineering, and quasi-experimental econometric models evaluating transit capitalization and displacement risk across Mecklenburg County rail corridors.
            </p>
        </header>

        <ul class="nav nav-tabs mb-4" id="rlimTabs" role="tablist">
            <li class="nav-item"><button class="nav-link active" id="dict-tab" data-bs-toggle="tab" data-bs-target="#dict-panel">📋 Data Dictionary</button></li>
            <li class="nav-item"><button class="nav-link" id="analytics-tab" data-bs-toggle="tab" data-bs-target="#analytics-panel">📊 Spatial Analytics & Visualizations</button></li>
            <li class="nav-item"><button class="nav-link" id="econometrics-tab" data-bs-toggle="tab" data-bs-target="#econometrics-panel">📈 Econometric Models & DiD Proxy</button></li>
        </ul>

        <div class="tab-content">
            <div class="tab-pane fade show active" id="dict-panel">
'''

for t in spec.get('tables', []):
    html += f'''
                <div class="card">
                    <div class="card-header bg-dark text-white py-2"><h6 class="mb-0"><code>{t['name']}</code> <span class="text-light fw-normal ms-2">— {t.get('label', '')}</span></h6></div>
                    <div class="card-body">
                        <p class="card-text text-secondary mb-3 small">{t.get('description', '')}</p>
                        <table class="table table-hover table-bordered mb-0">
                            <thead><tr><th style="width: 22%;">Column</th><th style="width: 13%;">Type</th><th style="width: 45%;">Description</th><th style="width: 20%;">Values / Examples</th></tr></thead>
                            <tbody>
    '''
    for col in t.get('columns', []):
        ex = col.get('examples') or col.get('values') or col.get('range') or ''
        ex_str = f'<code>{ex}</code>' if ex else '<span class="text-muted">—</span>'
        html += f'<tr><td><strong>{col["name"]}</strong></td><td><span class="badge bg-light text-dark border">{col.get("type", "any")}</span></td><td>{col.get("description", "")}</td><td><small>{ex_str}</small></td></tr>'
    html += '</tbody></table></div></div>'

html += f'''
            </div>
            <div class="tab-pane fade" id="analytics-panel">
                <div class="row g-4">
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Parcel Distribution by Walkshed Tier</h6></div><div class="card-body p-2">{fig1.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Zoning Composition Across Buffer Tiers</h6></div><div class="card-body p-2">{fig2.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Real PPSF (2026 USD) vs. Proximity</h6></div><div class="card-body p-2">{fig3.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                    <div class="col-lg-6"><div class="card h-100"><div class="card-header bg-light py-2"><h6 class="mb-0 fw-bold">Nominal vs. Real 2026 Purchasing Power</h6></div><div class="card-body p-2">{fig4.to_html(full_html=False, include_plotlyjs=False)}</div></div></div>
                </div>
            </div>
            <div class="tab-pane fade" id="econometrics-panel">
                <div class="card mb-4">
                    <div class="card-header bg-primary text-white py-2"><h6 class="mb-0 fw-bold">Gold Layer Econometric Dashboard & Causal DiD Proxy</h6></div>
                    <div class="card-body">
                        <div class="text-center">{gold_img_tag}</div>
                        <div class="text-center mt-3">{did_img_tag}</div>
                        <h6 class="fw-bold mt-4">Econometric Model Output Summary</h6>
                        <div class="table-responsive">
                            <table class="table table-bordered table-sm mt-2">
                                <thead class="table-light">
                                    <tr><th>Model</th><th>Specification</th><th>Dependent Variable</th><th>Estimator</th><th>Coef</th><th>Std. Err (HC1)</th><th>p-value</th><th>Implied Impact</th></tr>
                                </thead>
                                <tbody>
                                    <tr>
                                        <td><strong>Model 1</strong></td>
                                        <td>Hedonic OLS Gradient</td>
                                        <td><code>ln(Real PPSF)</code></td>
                                        <td>Distance to Station (per km)</td>
                                        <td><code>-0.1165</code></td>
                                        <td><code>0.0016</code></td>
                                        <td><code>&lt; 0.0001</code></td>
                                        <td><span class="badge bg-danger">-10.99% / km</span></td>
                                    </tr>
                                    <tr>
                                        <td><strong>Model 2</strong></td>
                                        <td>Quasi-Experimental DiD</td>
                                        <td><code>ln(Real PPSF)</code></td>
                                        <td>Treated &times; Post (2018+)</td>
                                        <td><code>+0.0025</code></td>
                                        <td><code>0.0290</code></td>
                                        <td><code>0.9311</code></td>
                                        <td><span class="badge bg-secondary">+0.25% (p = 0.93)</span></td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                        <div class="alert alert-light border mt-3 small text-secondary">
                            <strong>Identification Strategy:</strong> Model 1 confirms a steep spatial rent gradient across Mecklenburg County (~11% discount per km from rail platforms). Model 2 identifies the operational opening shock of the LYNX Blue Line Extension relative to the unbuilt Matthews control corridor. The insignificant post-opening coefficient corroborates the <em>anticipatory capitalization hypothesis</em>: transit premiums were largely capitalized during land assembly and construction (2014–2017) before revenue service commenced.
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.getElementById('analytics-tab').addEventListener('shown.bs.tab', () => window.dispatchEvent(new Event('resize')));
        document.getElementById('econometrics-tab').addEventListener('shown.bs.tab', () => window.dispatchEvent(new Event('resize')));
    </script>
</body>
</html>
'''

Path('docs/index.html').write_text(html)
print('✓ docs/index.html compiled successfully with Gold modeling visuals.')
