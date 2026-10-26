"""
Stage 6: Prediction Engine — three predictors + conformal prediction intervals.

Predictors:
  HistoricalAveragePredictor  — mean of same (hour, dow) slot across history
  MovingAveragePredictor      — exponential moving average of recent readings
  XGBoostPredictor            — XGBoost regressor with conformal intervals

Train/val/test split: days 1-21 train, 22-25 validate, 26-30 test.
Conformal prediction: residuals on calibration set → 90% coverage interval.
"""

import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from digital_twin.prediction.features import (
    LOT_CAPACITIES, LOT_IDS, build_features, load_data
)

_PREDICTOR_SINGLETON: Optional["PredictionEngine"] = None
MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "experiments" / "models"


class HistoricalAveragePredictor:
    """Predict based on historical mean for (hour, day_of_week) slot."""

    name = "HistoricalAverage"

    def __init__(self):
        self._table: Dict[str, Dict[tuple, float]] = defaultdict(dict)
        self._global_mean: Dict[str, float] = {}

    def fit(self, df, lot_id: str, horizon_steps: int = 1):
        import pandas as pd
        lot_df = df[df["lot_id"] == lot_id].copy() if "lot_id" in df.columns else df.copy()
        lot_df = lot_df.sort_values("timestamp").reset_index(drop=True)
        if "fill_rate" not in lot_df.columns:
            cap = LOT_CAPACITIES.get(lot_id, 100)
            lot_df["fill_rate"] = lot_df.get("occupied", 0) / cap
        # Ensure time columns exist (extract from timestamp if needed)
        if "hour" not in lot_df.columns and "timestamp" in lot_df.columns:
            ts = pd.to_datetime(lot_df["timestamp"])
            lot_df["hour"] = ts.dt.hour
            lot_df["day_of_week"] = ts.dt.dayofweek
        if "hour" not in lot_df.columns:
            lot_df["hour"] = 9
            lot_df["day_of_week"] = 0
        lot_df["target"] = lot_df["fill_rate"].shift(-horizon_steps)
        lot_df = lot_df.dropna(subset=["target"])
        n_train = int(len(lot_df) * 0.70)
        train = lot_df.iloc[:n_train]
        slot_means = train.groupby(["hour", "day_of_week"])["target"].mean().to_dict()
        self._table[lot_id] = slot_means
        self._global_mean[lot_id] = float(train["target"].mean())

    def predict_slot(self, lot_id: str, hour: int, dow: int) -> float:
        key = (hour, dow)
        table = self._table.get(lot_id, {})
        return table.get(key, self._global_mean.get(lot_id, 0.5))


class MovingAveragePredictor:
    """Exponential moving average of recent fill rates."""

    name = "MovingAverage"

    def __init__(self, alpha: float = 0.3):
        self.alpha = alpha
        self._ema: Dict[str, float] = {}
        self._residuals: Dict[str, list] = defaultdict(list)

    def fit(self, df, lot_id: str, horizon_steps: int = 1):
        import pandas as pd
        lot_df = df[df["lot_id"] == lot_id].copy() if "lot_id" in df.columns else df.copy()
        if "timestamp" in lot_df.columns:
            lot_df = lot_df.sort_values("timestamp").reset_index(drop=True)
        if "fill_rate" not in lot_df.columns:
            cap = LOT_CAPACITIES.get(lot_id, 100)
            occ_col = "occupied" if "occupied" in lot_df.columns else lot_df.columns[0]
            lot_df["fill_rate"] = lot_df.get(occ_col, 0) / cap
        n_train = int(len(lot_df) * 0.70)
        train = lot_df.iloc[:n_train]["fill_rate"].values
        ema = float(train[0]) if len(train) > 0 else 0.5
        residuals = []
        for v in train:
            v = float(v)
            residuals.append(abs(v - ema))
            ema = self.alpha * v + (1 - self.alpha) * ema
        self._ema[lot_id] = float(ema)
        self._residuals[lot_id] = residuals

    def update(self, lot_id: str, current_fill: float) -> float:
        prev = self._ema.get(lot_id, current_fill)
        new_ema = float(self.alpha * current_fill + (1 - self.alpha) * prev)
        self._ema[lot_id] = new_ema
        return new_ema

    def predict(self, lot_id: str) -> float:
        return self._ema.get(lot_id, 0.5)


class XGBoostPredictor:
    """XGBoost occupancy predictor with conformal prediction intervals."""

    name = "XGBoost"

    def __init__(self):
        self._models: Dict[str, object] = {}
        self._conformal_width: Dict[str, float] = {}

    def fit(self, df, lot_id: str, horizon_steps: int = 1):
        try:
            from xgboost import XGBRegressor
        except ImportError:
            return  # silently skip if xgboost not installed

        X, y, ts = build_features(df, lot_id, horizon_steps)
        if len(X) < 50:
            return

        # Train/val/test split: 70% train, 15% val (conformal calibration), 15% test
        n = len(X)
        n_train = int(n * 0.70)
        n_cal = int(n * 0.85)

        X_train, y_train = X[:n_train], y[:n_train]
        X_cal, y_cal = X[n_train:n_cal], y[n_train:n_cal]

        model = XGBRegressor(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
        model.fit(X_train, y_train, eval_set=[(X_cal, y_cal)], verbose=False)
        self._models[lot_id] = model

        # Conformal calibration: compute residuals on calibration set
        if len(X_cal) > 0:
            y_pred_cal = model.predict(X_cal)
            residuals = np.abs(y_cal - y_pred_cal)
            # 90% conformal quantile
            q90 = float(np.quantile(residuals, 0.90))
            self._conformal_width[lot_id] = q90
        else:
            self._conformal_width[lot_id] = 0.1

    def predict(self, lot_id: str, features: np.ndarray) -> Dict:
        model = self._models.get(lot_id)
        if model is None:
            return {"fill_rate": 0.5, "lower_90": 0.4, "upper_90": 0.6}
        pred = float(model.predict(features.reshape(1, -1))[0])
        pred = max(0.0, min(1.0, pred))
        w = self._conformal_width.get(lot_id, 0.1)
        return {
            "fill_rate": pred,
            "lower_90": max(0.0, pred - w),
            "upper_90": min(1.0, pred + w),
        }


class PredictionEngine:
    """
    Unified prediction engine — wraps all three predictors.
    Falls back gracefully: XGBoost → MovingAverage → HistoricalAverage.
    """

    def __init__(self):
        self._ha = HistoricalAveragePredictor()
        self._ma = MovingAveragePredictor()
        self._xgb = XGBoostPredictor()
        self._fitted = False
        self._current_fills: Dict[str, float] = {lid: 0.3 for lid in LOT_IDS}

    def fit_all(self, horizon_steps: int = 1):
        """Train all predictors on synthetic_multi_year_demand.csv."""
        df = load_data()
        for lot_id in LOT_IDS:
            self._ha.fit(df, lot_id, horizon_steps)
            self._ma.fit(df, lot_id, horizon_steps)
            self._xgb.fit(df, lot_id, horizon_steps)
        self._fitted = True

    def update_current(self, lot_id: str, fill_rate: float):
        """Update live fill rate (called by simulation or API)."""
        self._current_fills[lot_id] = max(0.0, min(1.0, fill_rate))
        self._ma.update(lot_id, fill_rate)

    def predict(self, lot_id: str, horizon_minutes: int = 15) -> Dict:
        """
        Predict fill rate at horizon_minutes from now.
        Returns: {fill_rate, lower_90, upper_90, model}
        """
        if not self._fitted:
            self.fit_all(horizon_steps=max(1, horizon_minutes // 15))

        # Try XGBoost first
        if lot_id in self._xgb._models:
            df = load_data()
            lot_df = df[df["lot_id"] == lot_id] if "lot_id" in df.columns else df
            if len(lot_df) > 0:
                X, y, ts = build_features(lot_df, lot_id, max(1, horizon_minutes // 15))
                if len(X) > 0:
                    result = self._xgb.predict(lot_id, X[-1])
                    result["model"] = "XGBoost"
                    return result

        # Fallback: Moving Average
        ma_pred = float(self._ma.predict(lot_id))
        ma_pred = max(0.0, min(1.0, ma_pred))
        return {
            "fill_rate": ma_pred,
            "lower_90": max(0.0, ma_pred - 0.10),
            "upper_90": min(1.0, ma_pred + 0.10),
            "model": "MovingAverage",
        }

    def predict_all(self, horizon_minutes: int = 15) -> Dict[str, Dict]:
        """Predict all lots at once."""
        return {lid: self.predict(lid, horizon_minutes) for lid in LOT_IDS}

    def get_forecast_snapshot(self, horizon_minutes: int = 15) -> Dict:
        """Return a forecast dict compatible with ParkingNavXFullStrategy.update_policy."""
        preds = self.predict_all(horizon_minutes)
        return {
            "lot_fill_rates": {lid: preds[lid]["fill_rate"] for lid in LOT_IDS},
            # Upper confidence bound — used by use_uncertainty=True path in P strategy
            "lot_fill_rates_upper": {lid: preds[lid].get("upper_90", preds[lid]["fill_rate"]) for lid in LOT_IDS},
            "horizon_minutes": horizon_minutes,
            "timestamp": time.time(),
        }


def get_predictor() -> PredictionEngine:
    """Singleton accessor — fits on first call."""
    global _PREDICTOR_SINGLETON
    if _PREDICTOR_SINGLETON is None:
        _PREDICTOR_SINGLETON = PredictionEngine()
        _PREDICTOR_SINGLETON.fit_all()
    return _PREDICTOR_SINGLETON
