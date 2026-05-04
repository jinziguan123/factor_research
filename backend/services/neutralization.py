"""Cross-sectional industry + market cap neutralization service."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class NeutralizationService:
    """Cross-sectional OLS regression neutralization.

    For each date, regresses factor values on log(market_cap) and industry
    dummies, then returns the residuals. Small industries (< min_industry_size
    stocks) are merged into a single "other" category.
    """

    def neutralize(
        self,
        factor_panel: pd.DataFrame,
        market_cap: pd.DataFrame,
        industry: pd.Series,
        min_industry_size: int = 3,
    ) -> pd.DataFrame:
        """Industry + market cap neutralization. Returns residual panel."""
        return self._neutralize_core(
            factor_panel, market_cap, industry, min_industry_size,
            use_industry=True, use_mktcap=True,
        )

    def neutralize_with_industry_only(
        self,
        factor_panel: pd.DataFrame,
        industry: pd.Series,
        min_industry_size: int = 3,
    ) -> pd.DataFrame:
        """Industry-only neutralization."""
        return self._neutralize_core(
            factor_panel, None, industry, min_industry_size,
            use_industry=True, use_mktcap=False,
        )

    def neutralize_with_market_cap_only(
        self,
        factor_panel: pd.DataFrame,
        market_cap: pd.DataFrame,
    ) -> pd.DataFrame:
        """Market-cap-only neutralization."""
        return self._neutralize_core(
            factor_panel, market_cap, None, 3,
            use_industry=False, use_mktcap=True,
        )

    # ------------------------------------------------------------------
    def _neutralize_core(
        self,
        factor_panel: pd.DataFrame,
        market_cap: pd.DataFrame | None,
        industry: pd.Series | None,
        min_industry_size: int,
        use_industry: bool,
        use_mktcap: bool,
    ) -> pd.DataFrame:
        result = factor_panel.copy()

        for d in factor_panel.index:
            y = factor_panel.loc[d]
            valid = ~y.isna()

            # Use DataFrames for X parts so index alignment is automatic
            X_frames: list[pd.DataFrame] = []

            if use_mktcap and market_cap is not None:
                mc = market_cap.reindex(
                    index=factor_panel.index, columns=factor_panel.columns
                )
                if d in mc.index:
                    mc_row = mc.loc[d]
                    log_mc = np.log(mc_row.replace(0, np.nan))
                    valid = valid & log_mc.notna()
                    X_frames.append(log_mc.to_frame("log_mktcap"))

            if use_industry and industry is not None:
                ind = industry.reindex(factor_panel.columns)
                valid = valid & ind.notna()

                # Merge small industries into "other"
                counts = ind[valid].value_counts()
                small = counts[counts < min_industry_size].index.tolist()
                ind_merged = ind.where(~ind.isin(small), "其他")

                dummies = pd.get_dummies(ind_merged, dtype=float)
                if dummies.shape[1] > 1:
                    dummies = dummies.iloc[:, 1:]  # drop first to avoid collinearity
                X_frames.append(dummies)

            if not X_frames:
                result.loc[d] = np.nan
                continue

            # Combine all X parts — index alignment happens here
            X_all = pd.concat(X_frames, axis=1)

            # Filter to valid stocks only
            X_valid = X_all.loc[valid]
            y_valid = y[valid].values.astype(float)

            # Need strictly more observations than features
            if X_valid.shape[0] <= X_valid.shape[1]:
                result.loc[d] = np.nan
                continue

            X = X_valid.values

            try:
                beta, _, _, _ = np.linalg.lstsq(X, y_valid, rcond=None)
            except np.linalg.LinAlgError:
                result.loc[d] = np.nan
                continue

            residual = np.full(len(y), np.nan)
            residual[valid.values] = y_valid - X @ beta
            result.loc[d] = residual

        return result
