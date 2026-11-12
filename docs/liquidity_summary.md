# RLIM Econometric Output: Market Liquidity & Velocity Shocks

## Poisson Rate Models of Transaction Turnover

Dependent Variable: Transaction Count $Q_{c,z,t}$ with exposure offset $\ln(\text{Total Parcels}_{c,z})$.
Sample: Pedestrian walkshed ($\le 0.5\text{ mi}$) across BLE and Matthews Control.
Dispersion scale parameter: $\hat{\phi} = 0.658$.

| Estimator / Parameter | Description | Coef ($\beta$) | Clustered SE | p-value | Relative Rate Change ($e^\beta - 1$) |
|---|---|---|---|---|---|
| **Treated $\times$ Anticipation** (Model L1) | Walkshed turnover surge (2014–2017) | `-0.0008` | `0.0127` | `9.4751e-01` | `-0.08%` |
| **Treated $\times$ Revenue Service** (Model L1) | Walkshed turnover surge (2018+) | `-0.0016` | `0.0083` | `8.4805e-01` | `-0.16%` |
| **Treated $\times$ Anticipation $\times$ Comm** (Model L2 DDD) | Differential commercial churn acceleration | `0.0067` | `0.0146` | `6.4737e-01` | `+0.67%` |

### Methodological Summary
1. **Balanced Panel Preservation:** Strata with zero transactions in a calendar year are explicitly modeled with $Q=0$ rather than omitted.
2. **Stratum-Clustered Standard Errors:** Clustered standard errors account for within-unit serial correlation across the 15-year panel.
3. **Turnover Velocity Dynamics:** Sales turnover rate ($Q/N$) captures parcel assembly activity and market liquidity shifts that occur before price equilibrium clears.
