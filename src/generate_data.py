"""Generate a reproducible, fictional e-commerce transactions dataset."""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def generate(seed: int = 42, n_customers: int = 2500) -> None:
    rng = np.random.default_rng(seed)
    DATA.mkdir(exist_ok=True)

    categories = ["Electronics", "Home", "Beauty", "Clothing", "Sports", "Books"]
    regions = ["North", "South", "East", "West", "Central"]
    customer_ids = np.arange(1, n_customers + 1)
    customer_segment = rng.choice(["standard", "loyal", "premium"], n_customers, p=[.67, .25, .08])
    signup_days = rng.integers(0, 700, n_customers)
    customers = pd.DataFrame({
        "customer_id": customer_ids,
        "signup_date": pd.Timestamp("2023-01-01") + pd.to_timedelta(signup_days, unit="D"),
        "region": rng.choice(regions, n_customers),
        "segment": customer_segment,
    })
    customers["signup_date"] = customers.signup_date.dt.strftime("%Y-%m-%d")

    products = pd.DataFrame([
        {"product_id": i + 1, "product_name": f"{cat} item {i + 1:02d}", "category": cat,
         "unit_price": round(float(rng.uniform(12, 260)), 2), "unit_cost": round(float(rng.uniform(5, 170)), 2)}
        for i, cat in enumerate(categories * 4)
    ])
    products["unit_cost"] = np.minimum(products.unit_cost, products.unit_price * .88).round(2)

    # Latent propensity creates repeat-purchase patterns for a meaningful modeling demo.
    propensity = rng.beta(1.8, 4.2, n_customers)
    propensity += np.where(customer_segment == "loyal", .20, 0)
    propensity += np.where(customer_segment == "premium", .32, 0)
    propensity = np.clip(propensity, .03, .88)
    orders = []
    line_items = []
    order_id = 1
    item_id = 1
    start = pd.Timestamp("2023-01-01")
    end = pd.Timestamp("2025-12-31")
    for ix, cid in enumerate(customer_ids):
        signup = pd.Timestamp(customers.iloc[ix].signup_date)
        available = max(1, (end - signup).days)
        # Higher propensity customers place more orders across the observation period.
        count = int(rng.poisson(1.0 + propensity[ix] * 10))
        if count == 0:
            continue
        for day in np.sort(rng.integers(0, available + 1, count)):
            date = signup + pd.Timedelta(days=int(day))
            status = rng.choice(["Completed", "Cancelled", "Returned"], p=[.91, .055, .035])
            orders.append({"order_id": order_id, "customer_id": int(cid), "order_date": date.strftime("%Y-%m-%d"), "status": status})
            n_lines = int(rng.choice([1, 2, 3], p=[.68, .25, .07]))
            for _ in range(n_lines):
                product = products.iloc[int(rng.integers(0, len(products)))]
                qty = int(rng.choice([1, 2, 3], p=[.78, .18, .04]))
                discount = float(rng.choice([0, .05, .10, .15], p=[.58, .20, .17, .05]))
                line_items.append({"order_item_id": item_id, "order_id": order_id,
                                   "product_id": int(product.product_id), "quantity": qty,
                                   "unit_price": float(product.unit_price), "discount": discount})
                item_id += 1
            order_id += 1

    customers.to_csv(DATA / "customers.csv", index=False)
    products.to_csv(DATA / "products.csv", index=False)
    pd.DataFrame(orders).to_csv(DATA / "orders.csv", index=False)
    pd.DataFrame(line_items).to_csv(DATA / "order_items.csv", index=False)
    print(f"Generated {len(customers):,} customers, {len(orders):,} orders, {len(line_items):,} order lines in {DATA}")


if __name__ == "__main__":
    generate()
