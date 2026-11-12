# RLIM Econometric Output: Hedonic Baseline & Difference-in-Differences

## Model Summary Table (Station-Clustered Standard Errors)

| Model | Specification | Dependent Variable | Primary Estimator | Coef | Station-Clustered SE | p-value | Implied % Impact |
|---|---|---|---|---|---|---|---|
| **Model 1** | Intra-Corridor Hedonic OLS | $\ln(\text{Real PPSF})$ | Distance to Station (per km) | `-0.0951` | `0.0422` | `2.4130e-02` | `-9.07%` per km |
| **Model 2** | Quasi-Experimental DiD | $\ln(\text{Real PPSF})$ | Treated $\times$ Post (2018+) | `0.0060` | `0.0224` | `7.8806e-01` | `+0.60%` premium |

### Methodological Summary
* **Intra-Corridor Variation:** Corridor fixed effects (`C(nearest_line)`) prevent baseline regional differences from biasing the station distance decay parameter.
* **Macroeconomic Controls:** Calendar year fixed effects (`C(sale_year)`) absorb regional post-2018 economic fluctuations.
* **Clustered Inference:** Standard errors are clustered at the station platform level (`nearest_station_name`) to account for localized spatial correlation.
