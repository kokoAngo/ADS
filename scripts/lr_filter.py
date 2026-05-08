#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
LR Filter — Self-learning Phase 1 Step 2

`findings/findings/model_timing.md` の多変量 LR 多変量モデル (AUC=0.72,
Top 30% 反響率 16.8% / Bottom 30% 0.5%, 33× リフト) を本番 pipeline で
使えるようにラップしたモジュール。

- `LRFilter` クラス: StandardScaler + LogisticRegression(max_iter=2000)
  を CalibratedClassifierCV(method='isotonic', cv=5) で確率校正
- `fit(X, y)` / `predict_proba(X)` / `filter(items, threshold_pct=30)` /
  `save(path)` / `load(path)`
- 学習データは `data/outcomes_history.csv` (sync_outcomes.py が日次更新)
- 特徴量 11 個 (model_timing.md A-1):
   賃料 / 面積 / 築年数 / 人気駅(0/1) / 人気沿線(0/1) /
   間取_1R1K(0/1) / 間取_2LDK(0/1) / 間取_3LDK+(0/1) /
   高効率区(0/1) / 低効率区(0/1) / 再投放回数

実 fit / 推論集成は analysis-claude の Step 2 訓練戦略 ack 後に着手。
本ファイルは骨格のみ。CLI 実行 (`python scripts/lr_filter.py train`) で
`models/lr_filter.pkl` + `models/lr_filter_config.json` を出力する想定。
"""
import os
import sys
import json
import pickle
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline


# ============================================================
# Constants — analysis-claude confirmed interface
# ============================================================
FEATURE_NAMES = [
    "rent",            # 賃料 (万円)
    "area",            # 面積 (m²)
    "age",             # 築年数 (年)
    "hot_station",     # 0/1
    "hot_line",        # 0/1
    "layout_1R1K",     # 0/1
    "layout_2LDK",     # 0/1
    "layout_3LDK_plus",# 0/1
    "ward_high_eff",   # 0/1
    "ward_low_eff",    # 0/1
    "repost_count",    # 再投放回数
]

DEFAULT_THRESHOLD_PCT = 30  # Top 30% 採用 (model_timing.md M1 推奨)

OUTCOMES_CSV = Path("data") / "outcomes_history.csv"
MODEL_PATH = Path("models") / "lr_filter.pkl"
CONFIG_PATH = Path("models") / "lr_filter_config.json"


# ============================================================
# LRFilter
# ============================================================
class LRFilter:
    """LR + isotonic calibration による反響有無の確率推定。"""

    def __init__(self, feature_names=None):
        self.feature_names = feature_names or list(FEATURE_NAMES)
        self.pipeline = None  # fit 後に StandardScaler + LR の sklearn Pipeline
        self.calibrator = None  # fit 後に CalibratedClassifierCV (isotonic, cv=5)
        self.cv_auc_mean = None
        self.cv_auc_std = None
        self.n_train = None
        self.n_positive = None
        self.fitted_at = None

    def fit(self, X, y):
        """X: DataFrame[feature_names] / y: Series[0,1]. 訓練 + 5-fold CV AUC 評価。"""
        if isinstance(X, pd.DataFrame):
            X = X[self.feature_names].to_numpy(dtype=float)
        else:
            X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=int)

        if X.shape[1] != len(self.feature_names):
            raise ValueError(
                f"X columns {X.shape[1]} != feature_names {len(self.feature_names)}"
            )

        base = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=2000, solver="lbfgs")),
        ])

        # Stratified 5-fold CV AUC (model_timing.md と同条件)
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        auc_scores = cross_val_score(base, X, y, cv=skf, scoring="roc_auc")

        # Final fit on whole data + isotonic calibration
        # CalibratedClassifierCV cv=5 が内部で 5 fold 切って calibrator を学習
        self.pipeline = base.fit(X, y)
        self.calibrator = CalibratedClassifierCV(
            estimator=Pipeline([
                ("scaler", StandardScaler()),
                ("lr", LogisticRegression(max_iter=2000, solver="lbfgs")),
            ]),
            method="isotonic",
            cv=5,
        ).fit(X, y)

        self.cv_auc_mean = float(auc_scores.mean())
        self.cv_auc_std = float(auc_scores.std())
        self.n_train = int(len(y))
        self.n_positive = int(y.sum())
        self.fitted_at = datetime.now().isoformat()
        return self

    def predict_proba(self, X):
        """X → P(has_rev=1). isotonic 校正後の確率を返す。"""
        if self.calibrator is None:
            raise RuntimeError("LRFilter is not fitted yet")
        if isinstance(X, pd.DataFrame):
            X = X[self.feature_names].to_numpy(dtype=float)
        else:
            X = np.asarray(X, dtype=float)
        return self.calibrator.predict_proba(X)[:, 1]

    def filter(self, items, X, threshold_pct=DEFAULT_THRESHOLD_PCT):
        """
        items: 任意の list (pipeline 側で REINS_ID / page_id 等を入れて使う)
        X: items に対応する features (DataFrame or array)
        threshold_pct: Top N% を残す。30 なら上位 30% を返す。
        return: (items の subset, items に対応する scores の array)
        """
        if len(items) == 0:
            return [], np.array([])
        scores = self.predict_proba(X)
        if threshold_pct >= 100:
            return list(items), scores
        # 確率閾値を threshold_pct percentile で切る
        cutoff = np.percentile(scores, 100 - threshold_pct)
        keep = scores >= cutoff
        kept_items = [it for it, k in zip(items, keep) if k]
        return kept_items, scores

    # ============================================================
    # save / load
    # ============================================================
    def save(self, model_path=MODEL_PATH, config_path=CONFIG_PATH):
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        with open(model_path, "wb") as f:
            pickle.dump(self, f)
        config = {
            "feature_names": self.feature_names,
            "cv_auc_mean": self.cv_auc_mean,
            "cv_auc_std": self.cv_auc_std,
            "n_train": self.n_train,
            "n_positive": self.n_positive,
            "fitted_at": self.fitted_at,
            "threshold_pct_default": DEFAULT_THRESHOLD_PCT,
            "calibration": "isotonic / CalibratedClassifierCV cv=5",
            "validation": "StratifiedKFold n_splits=5",
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, model_path=MODEL_PATH):
        with open(model_path, "rb") as f:
            return pickle.load(f)


# ============================================================
# Feature engineering — outcomes_history.csv → DataFrame[FEATURE_NAMES]
# ============================================================
# NOTE: db_defb の `station` フィールドは "東急目黒線/武蔵小山品川区小山３" 形式
# (沿線 / 駅名 + 区 + 番地 が連結). hot_line / hot_station / 区効率 を抽出するには
# parse 必要. analysis-claude の Step 2 戦略 ack 後に実装着手。
#
# 暫定方針 (Step 2 Q1/Q2 で確認中):
#   (a) reuse-coef: model_timing.md の 11 features 標準化係数を hard-code で wrap
#       → feature engineering 必要だが fit 不要、即時運用可能、サンプル数依存なし
#   (b) train-from-csv: outcomes_history.csv で再 fit
#       → 現状 526 行 / positive ~50 件、border line。Phase 2 重訓まで暫定運用
#   (c) train-from-mainDB: メイン DB + 主庫 historical で訓練 (model_timing.md 流用)
#       → 一番 finding に近い。要 join + scrape historical ad outcome。

def build_features_from_csv(csv_path=OUTCOMES_CSV):
    """outcomes_history.csv → (X DataFrame, y Series, items list)
    現状未実装 (TODO Step 2 Q ack 待ち)。
    """
    raise NotImplementedError(
        "Feature engineering 未確定. analysis-claude の Step 2 訓練戦略 ack 後に実装"
    )


# ============================================================
# CLI
# ============================================================
def cmd_train():
    print("[lr_filter train] feature engineering 未実装. Step 2 戦略確定待ち.")
    print(f"  outcomes csv: {OUTCOMES_CSV} ({'exists' if OUTCOMES_CSV.exists() else 'missing'})")
    if OUTCOMES_CSV.exists():
        df = pd.read_csv(OUTCOMES_CSV)
        print(f"  rows: {len(df)} / positive (has_rev=1): {int(df['has_rev'].sum())}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("train", help="outcomes_history.csv で fit (Step 2 戦略確定後に実装)")
    args = parser.parse_args()
    if args.cmd == "train":
        cmd_train()


if __name__ == "__main__":
    main()
