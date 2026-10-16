# RLIM Econometric Output: Hedonic Baseline & Difference-in-Differences

## Model Summary Table

| Model | Specification | Dependent Variable | Primary Estimator | Coef | Std. Err (HC1) | p-value | Implied % Impact |
|---|---|---|---|---|---|---|---|
| **Model 1** | Baseline Pooled Hedonic OLS | $\ln(\text{Real PPSF})$ | Distance to Station (per km) | `-0.1165` | `0.0016` | `0.0000e+00` | `-10.99%` per km |
| **Model 2** | Quasi-Experimental DiD | $\ln(\text{Real PPSF})$ | Treated $\times$ Post (2018+) | `0.0025` | `0.0290` | `9.3113e-01` | `+0.25%` premium |

### Interpretation
* **Baseline Hedonic Gradient:** Evaluates spatial distance decay across all 4 study corridors.
* **Quasi-Experimental Shock (DiD):** Isolates the operational transit premium of the LYNX Blue Line Extension relative to the unbuilt Matthews / Independence control corridor, controlling for macro trends and zoning composition.
* **Event Study (Parallel Trends):** Pre-2018 coefficients indicate whether treated and control walksheds moved along parallel trajectories prior to revenue service opening.
