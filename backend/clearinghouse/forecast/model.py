"""Gradient-boosted day-ahead models (one per target) and their on-disk artifacts.

Model: scikit-learn ``HistGradientBoostingRegressor`` (histogram gradient boosting, the same
algorithm family as LightGBM). LightGBM is a declared dependency, but its macOS wheel needs a
system ``libomp`` (``brew install libomp``) that is not installed on the build machine, so
importing it fails; scikit-learn ships its own OpenMP runtime and needs no system change.

Each target predicts a *residual relative to a base feature* (``mode="diff"``: y - base, or
``mode="ratio"``: y / base). The base is itself a leakage-free feature (``features.py``), so a
tree model can follow level drift (e.g. solar capacity growth) that it could not extrapolate
from raw levels. Calendar and scale-free features are fed raw, every other data feature
relative to the base, plus the base itself.

Configurations below were chosen ONLY by blocked monthly cross-validation inside the training
period (each fold holds out one calendar month of Dec 2023 - Sep 2024 with a 14-day buffer); the
test period (2024-10-01..2024-12-31) was not used for any choice. Fixed seeds, deterministic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

from clearinghouse.config import DATA_PROCESSED
from clearinghouse.forecast import features as F

ARTIFACT_DIR = DATA_PROCESSED / "forecast"
SPEC_FILE = "feature_spec.json"
SEED = 0
# Features that are already scale-free / differences: never re-expressed relative to the base.
_NO_RELATIVE = {*F.CALENDAR_FEATURES, "recent10h_ratio", "recent10h_diff", "last_obs_delta24"}


@dataclass(frozen=True)
class ModelSpec:
    target: str
    base: str
    mode: str  # "diff" | "ratio"
    loss: str
    max_iter: int
    learning_rate: float
    max_leaf_nodes: int
    min_samples_leaf: int
    l2_regularization: float = 1.0
    clip_min: float | None = None
    features: tuple[str, ...] = field(default=F.FEATURES)

    def regressor(self) -> HistGradientBoostingRegressor:
        return HistGradientBoostingRegressor(
            loss=self.loss, max_iter=self.max_iter, learning_rate=self.learning_rate,
            max_leaf_nodes=self.max_leaf_nodes, min_samples_leaf=self.min_samples_leaf,
            l2_regularization=self.l2_regularization, early_stopping=False, random_state=SEED)


# Chosen by in-training blocked monthly CV (see module docstring) while the issue time was still
# 10:00 CST on D-1; kept unchanged when the issue time moved to 00:00 CST on D (same feature set,
# no re-tuning). Training-period CV at the adopted issue time (Dec 2023-Sep 2024): demand MAPE
# 3.73% (yesterday 4.63%); wind MAE 4,039 MW (yesterday 5,503); solar MAE 1,041 MW (yesterday
# 1,155). At the rejected 10:00 D-1 issue time: demand 5.27%, wind 5,339, solar 1,138.
SPECS: dict[str, ModelSpec] = {
    "demand": ModelSpec("demand", base="same_hour_latest", mode="ratio", loss="absolute_error",
                        max_iter=300, learning_rate=0.03, max_leaf_nodes=7, min_samples_leaf=200),
    "wind": ModelSpec("wind", base="mean24h", mode="diff", loss="squared_error",
                      max_iter=200, learning_rate=0.05, max_leaf_nodes=4, min_samples_leaf=200,
                      clip_min=0.0),
    "solar": ModelSpec("solar", base="same_hour_latest", mode="diff", loss="absolute_error",
                       max_iter=300, learning_rate=0.03, max_leaf_nodes=7, min_samples_leaf=200,
                       clip_min=0.0),
}


def design_matrix(X: pd.DataFrame, spec: ModelSpec) -> tuple[np.ndarray, np.ndarray]:
    """Model inputs and the base vector.

    Columns: each feature in ``spec.features`` (calendar / scale-free ones raw, every other data
    feature relative to the base: ``v / base`` or ``v - base``), then the base itself.
    """
    b = X[spec.base].to_numpy(dtype="float64")
    cols = []
    for name in spec.features:
        v = X[name].to_numpy(dtype="float64")
        if name in _NO_RELATIVE:
            cols.append(v)
        else:
            with np.errstate(all="ignore"):
                cols.append(v / b if spec.mode == "ratio" else v - b)
    cols.append(b)
    with np.errstate(all="ignore"):
        Z = np.column_stack(cols)
    Z[~np.isfinite(Z)] = np.nan
    return Z, b


def column_names(spec: ModelSpec) -> list[str]:
    op = "/" if spec.mode == "ratio" else "-"
    return [name if name in _NO_RELATIVE else f"{name}{op}base" for name in spec.features] + [
        f"base={spec.base}"]


def to_label(y: np.ndarray, b: np.ndarray, spec: ModelSpec) -> np.ndarray:
    with np.errstate(all="ignore"):
        return y / b if spec.mode == "ratio" else y - b


def from_label(p: np.ndarray, b: np.ndarray, spec: ModelSpec) -> np.ndarray:
    out = p * b if spec.mode == "ratio" else p + b
    if spec.clip_min is not None:
        out = np.clip(out, spec.clip_min, None)
    return out


def fit(spec: ModelSpec, X: pd.DataFrame, y: np.ndarray) -> HistGradientBoostingRegressor:
    Z, b = design_matrix(X, spec)
    label = to_label(y, b, spec)
    ok = np.isfinite(label)
    return spec.regressor().fit(Z[ok], label[ok])


def predict(model: HistGradientBoostingRegressor, spec: ModelSpec, X: pd.DataFrame) -> np.ndarray:
    Z, b = design_matrix(X, spec)
    p = from_label(model.predict(Z), b, spec)
    # The base is missing only before MIN_HISTORY_DAYS of data exist: refuse rather than guess.
    if not np.isfinite(p).all():
        raise ValueError(f"{spec.target}: non-finite forecast (missing base feature {spec.base})")
    return p


def save(models: dict[str, HistGradientBoostingRegressor], extra: dict,
         out_dir: Path = ARTIFACT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for target, m in models.items():
        joblib.dump(m, out_dir / f"{target}.joblib", compress=3)
    spec = {
        "model": "sklearn.ensemble.HistGradientBoostingRegressor",
        "sklearn_version": sklearn.__version__,
        "as_of_rule": F.AS_OF_RULE,
        "as_of": "00:00 CST on target day D",
        "as_of_hour_counted_on_d_minus_1": F.AS_OF_HOUR,
        "rejected_as_of": "10:00 CST on D-1 (yesterday baseline would use data after issue time)",
        "time_base": "fixed CST (UTC-6), 24 slots/day, slot h = [h:00, h+1:00)",
        "features": list(F.FEATURES),
        "calendar_features": list(F.CALENDAR_FEATURES),
        "series_columns": F.SERIES_COL,
        "lag_days": list(F.LAG_DAYS),
        "same_hour_mean_days": list(F.SAME_HOUR_MEAN_DAYS),
        "windows_h": list(F.WINDOWS_H),
        "specs": {t: {**asdict(s), "features": list(s.features),
                      "design_columns": column_names(s)} for t, s in SPECS.items()},
        **extra,
    }
    (out_dir / SPEC_FILE).write_text(json.dumps(spec, indent=2) + "\n")


def load(in_dir: Path = ARTIFACT_DIR) -> tuple[dict[str, HistGradientBoostingRegressor], dict]:
    spec = json.loads((in_dir / SPEC_FILE).read_text())
    if spec.get("features") != list(F.FEATURES):
        raise ValueError("forecast artifacts were trained with a different feature list; "
                         "re-run scripts/train_forecast.py")
    models = {t: joblib.load(in_dir / f"{t}.joblib") for t in F.TARGETS}
    return models, spec
