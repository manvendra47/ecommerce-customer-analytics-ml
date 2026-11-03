"""Create business-analysis tables and train an interpretable repeat-purchase model."""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss,
                             precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "outputs"
FEATURES = ["orders_180d", "spend_180d", "avg_order_value_180d", "days_since_order", "distinct_categories_180d", "region", "segment"]
NUMERIC = FEATURES[:5]
CATEGORICAL = FEATURES[5:]


def load_transactions():
    customers = pd.read_csv(DATA / "customers.csv", parse_dates=["signup_date"])
    products = pd.read_csv(DATA / "products.csv")
    orders = pd.read_csv(DATA / "orders.csv", parse_dates=["order_date"])
    items = pd.read_csv(DATA / "order_items.csv")
    # Keep the order-line price: it is the price actually charged at purchase time.
    # The product catalog also has a list price, so omit it from this merge to
    # avoid pandas renaming both columns to unit_price_x / unit_price_y.
    product_attributes = products[["product_id", "category", "unit_cost"]]
    lines = (items.merge(orders, on="order_id").merge(product_attributes, on="product_id")
             .merge(customers, on="customer_id"))
    lines["revenue"] = lines.quantity * lines.unit_price * (1 - lines.discount)
    lines["profit"] = lines.quantity * (lines.unit_price * (1 - lines.discount) - lines.unit_cost)
    return customers, lines


def save_business_analysis(customers, lines):
    OUT.mkdir(exist_ok=True)
    completed = lines[lines.status.eq("Completed")].copy()
    completed["month"] = completed.order_date.dt.to_period("M").astype(str)
    monthly = completed.groupby("month", as_index=False).agg(
        revenue=("revenue", "sum"), orders=("order_id", "nunique"), customers=("customer_id", "nunique"))
    monthly["revenue_per_order"] = (monthly.revenue / monthly.orders).round(2)
    monthly.to_csv(OUT / "monthly_retention_context.csv", index=False)

    category = completed.groupby("category", as_index=False).agg(
        revenue=("revenue", "sum"), profit=("profit", "sum"), units=("quantity", "sum"))
    category["profit_margin_pct"] = (100 * category.profit / category.revenue).round(2)
    category.sort_values("revenue", ascending=False).to_csv(OUT / "category_performance.csv", index=False)

    customer_orders = (completed.groupby(["customer_id", "order_id", "order_date"], as_index=False)
                       .revenue.sum())
    customer_summary = customer_orders.groupby("customer_id").agg(
        completed_orders=("order_id", "nunique"), lifetime_revenue=("revenue", "sum"),
        last_order_date=("order_date", "max")).reset_index()
    customer_summary = customers.merge(customer_summary, on="customer_id", how="left")
    customer_summary[["completed_orders", "lifetime_revenue"]] = customer_summary[["completed_orders", "lifetime_revenue"]].fillna(0)
    customer_summary.to_csv(OUT / "customer_retention_summary.csv", index=False)


def make_snapshots(customers, lines):
    completed = lines[lines.status.eq("Completed")].copy()
    # Collapse to order totals to avoid counting a multi-line order more than once.
    order_totals = completed.groupby(["customer_id", "order_id", "order_date"], as_index=False).agg(
        order_revenue=("revenue", "sum"), categories=("category", "nunique"))
    order_totals = order_totals.merge(customers[["customer_id", "signup_date", "region", "segment"]], on="customer_id")
    snapshots = []
    for anchor in pd.date_range("2023-07-01", "2025-09-01", freq="MS"):
        known = order_totals[order_totals.order_date < anchor]
        hist = known[known.order_date >= anchor - pd.Timedelta(days=180)]
        future = order_totals[(order_totals.order_date >= anchor) &
                              (order_totals.order_date < anchor + pd.Timedelta(days=90))]
        eligible = customers[customers.signup_date < anchor]
        if eligible.empty:
            continue
        h = hist.groupby("customer_id").agg(
            orders_180d=("order_id", "nunique"), spend_180d=("order_revenue", "sum"),
            avg_order_value_180d=("order_revenue", "mean"), last_order_date=("order_date", "max"))
        # Count true unique categories used during the lookback window.
        hist_category_lines = completed[(completed.order_date >= anchor - pd.Timedelta(days=180)) &
                                        (completed.order_date < anchor)]
        breadth = hist_category_lines.groupby("customer_id").category.nunique().rename("distinct_categories_180d")
        h = h.join(breadth)
        f = future.groupby("customer_id").order_id.nunique().rename("future_orders")
        frame = eligible.set_index("customer_id").join(h).join(f).reset_index()
        frame["anchor_date"] = anchor
        frame["orders_180d"] = frame.orders_180d.fillna(0)
        frame["spend_180d"] = frame.spend_180d.fillna(0)
        frame["avg_order_value_180d"] = frame.avg_order_value_180d.fillna(0)
        frame["distinct_categories_180d"] = frame.distinct_categories_180d.fillna(0)
        frame["days_since_order"] = (anchor - frame.last_order_date).dt.days.fillna(181).clip(upper=181)
        frame["repurchased_90d"] = (frame.future_orders.fillna(0) > 0).astype(int)
        snapshots.append(frame[["customer_id", "anchor_date", *FEATURES, "repurchased_90d"]])
    return pd.concat(snapshots, ignore_index=True)


def train_model(snapshots):
    # Hold out the latest 6 anchor months so validation is forward in time.
    train = snapshots[snapshots.anchor_date < "2025-04-01"]
    test = snapshots[snapshots.anchor_date >= "2025-04-01"]
    prep = ColumnTransformer([
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC),
        ("categorical", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                                  ("encode", OneHotEncoder(handle_unknown="ignore"))]), CATEGORICAL),
    ])
    model = Pipeline([("features", prep), ("classifier", LogisticRegression(max_iter=1000, class_weight="balanced"))])
    model.fit(train[FEATURES], train.repurchased_90d)
    probabilities = model.predict_proba(test[FEATURES])[:, 1]
    predicted = (probabilities >= .5).astype(int)
    metrics = pd.DataFrame([{
        "train_rows": len(train), "test_rows": len(test),
        "test_positive_rate": round(float(test.repurchased_90d.mean()), 4),
        "roc_auc": round(roc_auc_score(test.repurchased_90d, probabilities), 4),
        "average_precision": round(average_precision_score(test.repurchased_90d, probabilities), 4),
        "brier_score": round(brier_score_loss(test.repurchased_90d, probabilities), 4),
        "precision_at_0_5": round(precision_score(test.repurchased_90d, predicted, zero_division=0), 4),
        "recall_at_0_5": round(recall_score(test.repurchased_90d, predicted, zero_division=0), 4),
    }])
    metrics.to_csv(OUT / "model_metrics.csv", index=False)

    latest = snapshots[snapshots.anchor_date.eq(snapshots.anchor_date.max())].copy()
    latest["repurchase_probability_90d"] = model.predict_proba(latest[FEATURES])[:, 1]
    latest.sort_values("repurchase_probability_90d", ascending=False).to_csv(OUT / "latest_customer_scores.csv", index=False)
    return metrics


def main():
    if not (DATA / "orders.csv").exists():
        raise SystemExit("No data found. Run: python src/generate_data.py")
    customers, lines = load_transactions()
    save_business_analysis(customers, lines)
    snapshots = make_snapshots(customers, lines)
    snapshots.to_csv(OUT / "model_snapshot_features.csv", index=False)
    metrics = train_model(snapshots)
    print("Business outputs and model metrics saved to", OUT)
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
