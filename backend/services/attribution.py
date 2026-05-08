"""Factor style exposure attribution service."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class AttributionResult:
    """Result of style factor exposure decomposition.

    exposures: dict mapping style name -> Series of betas over time
    r_squared: daily R^2 of the cross-sectional regression
    residual: style-neutralized residual (pure alpha)
    """
    exposures: dict[str, pd.Series]
    r_squared: pd.Series
    residual: pd.DataFrame


class AttributionService:
    """Decompose alpha factor into style factor exposures via daily
    cross-sectional regression: alpha ~ sum(beta_i * style_i) + epsilon.
    """

    def decompose(
        self,
        factor_panel: pd.DataFrame,
        style_panels: dict[str, pd.DataFrame],
    ) -> AttributionResult:
        common_dates = factor_panel.index
        common_symbols = factor_panel.columns

        exposures: dict[str, list[float]] = {name: [] for name in style_panels}
        r2_list: list[float] = []
        residual = factor_panel.copy()

        style_names = list(style_panels.keys())

        # Pre-align all style panels once (not per-date) to avoid repeated reindex.
        aligned_panels: dict[str, pd.DataFrame] = {}
        for name in style_names:
            aligned = style_panels[name].reindex(
                index=common_dates, columns=common_symbols
            )
            aligned_panels[name] = aligned

        for d in common_dates:
            y = factor_panel.loc[d]

            X_cols = []
            active_names = []
            for name in style_names:
                aligned = aligned_panels[name]
                if d in aligned.index:
                    row = aligned.loc[d]
                    # Only include this style if it has at least some non-NaN values
                    if row.notna().sum() > 0:
                        X_cols.append(row.values)
                        active_names.append(name)

            # Fill NaN exposures for inactive styles
            for name in style_names:
                if name not in active_names:
                    exposures[name].append(np.nan)

            if len(X_cols) < 2:
                # Need at least 2 style factors for meaningful regression
                for name in active_names:
                    exposures[name].append(np.nan)
                r2_list.append(np.nan)
                residual.loc[d] = np.nan
                continue

            X = np.column_stack(X_cols)
            valid = ~y.isna() & ~np.isnan(X).any(axis=1)

            if valid.sum() < len(X_cols) + 2:
                for name in active_names:
                    exposures[name].append(np.nan)
                r2_list.append(np.nan)
                residual.loc[d] = np.nan
                continue

            y_valid = y[valid].values.astype(float)
            X_valid = X[valid]

            try:
                beta, residuals_ss, rank, _ = np.linalg.lstsq(
                    X_valid, y_valid, rcond=None
                )
            except np.linalg.LinAlgError:
                for name in active_names:
                    exposures[name].append(np.nan)
                r2_list.append(np.nan)
                residual.loc[d] = np.nan
                continue

            # Map betas back to the full style_names list
            beta_idx = 0
            for name in style_names:
                if name in active_names:
                    exposures[name].append(float(beta[beta_idx]))
                    beta_idx += 1
                # else already appended NaN above

            y_hat = X_valid @ beta
            ss_res = float(np.sum((y_valid - y_hat) ** 2))
            ss_tot = float(np.sum((y_valid - y_valid.mean()) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
            r2_list.append(r2)

            res = np.full(len(y), np.nan)
            res[valid.values] = y_valid - y_hat
            residual.loc[d] = res

        return AttributionResult(
            exposures={name: pd.Series(vals, index=common_dates, name=name)
                       for name, vals in exposures.items()},
            r_squared=pd.Series(r2_list, index=common_dates, name="r2"),
            residual=residual,
        )
