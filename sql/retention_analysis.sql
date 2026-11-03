USE customer_retention;

-- 1. New vs returning monthly customers and revenue.
WITH customer_month AS (
    SELECT o.customer_id, DATE_FORMAT(o.order_date, '%Y-%m-01') AS month_start,
           SUM(oi.quantity * oi.unit_price * (1 - oi.discount)) AS revenue
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.order_id
    WHERE o.status = 'Completed'
    GROUP BY o.customer_id, DATE_FORMAT(o.order_date, '%Y-%m-01')
), first_purchase AS (
    SELECT customer_id, MIN(month_start) AS first_month FROM customer_month GROUP BY customer_id
)
SELECT cm.month_start,
       SUM(CASE WHEN cm.month_start = fp.first_month THEN 1 ELSE 0 END) AS new_customers,
       SUM(CASE WHEN cm.month_start > fp.first_month THEN 1 ELSE 0 END) AS returning_customers,
       ROUND(SUM(cm.revenue), 2) AS revenue
FROM customer_month cm JOIN first_purchase fp USING (customer_id)
GROUP BY cm.month_start ORDER BY cm.month_start;

-- 2. Cohort retention: customers with any completed order in each month after first purchase.
WITH customer_month AS (
    SELECT DISTINCT o.customer_id, DATE_FORMAT(o.order_date, '%Y-%m-01') AS month_start
    FROM orders o WHERE o.status = 'Completed'
), cohort AS (
    SELECT customer_id, MIN(month_start) AS cohort_month FROM customer_month GROUP BY customer_id
)
SELECT c.cohort_month, cm.month_start,
       PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM cm.month_start), EXTRACT(YEAR_MONTH FROM c.cohort_month)) AS months_since_first,
       COUNT(DISTINCT cm.customer_id) AS active_customers
FROM cohort c JOIN customer_month cm USING (customer_id)
GROUP BY c.cohort_month, cm.month_start ORDER BY c.cohort_month, cm.month_start;

-- 3. RFM-style customer segments at the dataset's latest transaction date.
WITH order_value AS (
    SELECT o.customer_id, o.order_id, o.order_date,
           SUM(oi.quantity * oi.unit_price * (1 - oi.discount)) AS revenue
    FROM orders o JOIN order_items oi USING (order_id)
    WHERE o.status = 'Completed' GROUP BY o.customer_id, o.order_id, o.order_date
), rfm AS (
    SELECT customer_id, DATEDIFF((SELECT MAX(order_date) FROM orders), MAX(order_date)) AS recency_days,
           COUNT(*) AS frequency, SUM(revenue) AS monetary
    FROM order_value GROUP BY customer_id
)
SELECT customer_id, recency_days, frequency, ROUND(monetary, 2) AS monetary,
       CASE WHEN recency_days <= 60 AND frequency >= 4 THEN 'Active Loyal'
            WHEN recency_days > 180 THEN 'At Risk'
            WHEN frequency = 1 THEN 'New / One-time'
            ELSE 'Developing' END AS customer_group
FROM rfm ORDER BY monetary DESC;

-- 4. Product/category mix by region (completed orders only).
SELECT c.region, p.category, COUNT(DISTINCT o.order_id) AS orders,
       ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount)), 2) AS revenue
FROM customers c JOIN orders o USING (customer_id)
JOIN order_items oi USING (order_id) JOIN products p USING (product_id)
WHERE o.status = 'Completed'
GROUP BY c.region, p.category ORDER BY c.region, revenue DESC;
