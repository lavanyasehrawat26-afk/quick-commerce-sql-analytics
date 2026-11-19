-- Quick-Commerce Operations & Funnel Analytics: 13 SQL queries (SQLite)
-- Data: SYNTHETIC, Jul-Sep 2025 (see README). Tables: stores, customers, orders, inventory_daily, app_sessions.

-- Q1: Data validation before analysis: duplicates, orphan keys, invalid values
SELECT 'duplicate orders' AS check_name, COUNT(*) AS rows_flagged
FROM (SELECT customer_id, store_id, order_ts, order_value, COUNT(*) AS n
      FROM orders GROUP BY customer_id, store_id, order_ts, order_value HAVING n > 1)
UNION ALL
SELECT 'orphan customer_id', COUNT(*)
FROM orders o LEFT JOIN customers c ON c.customer_id = o.customer_id
WHERE c.customer_id IS NULL
UNION ALL
SELECT 'non-positive order_value', COUNT(*) FROM orders WHERE order_value <= 0
UNION ALL
SELECT 'delivered without delivery time', COUNT(*)
FROM orders WHERE status = 'delivered' AND delivery_minutes IS NULL;

-- Clean view used by every query below
DROP VIEW IF EXISTS clean_orders;
CREATE VIEW clean_orders AS
WITH ranked AS (
    SELECT o.*,
           ROW_NUMBER() OVER (PARTITION BY o.customer_id, o.store_id, o.order_ts, o.order_value
                              ORDER BY o.order_id) AS rn
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id      -- drops orphan rows
    WHERE o.order_value > 0
      AND NOT (o.status = 'delivered' AND o.delivery_minutes IS NULL)
)
SELECT order_id, customer_id, store_id, order_ts, category_mix, items, order_value,
       promo_code, status, promised_minutes, delivery_minutes, defect_type
FROM ranked
WHERE rn = 1;                                              -- keeps one copy of duplicates

-- Q2: Weekly KPI dashboard feed: orders, GMV, AOV, on-time %, defect rate, cancellation rate
SELECT strftime('%Y-W%W', order_ts)                                         AS week,
       COUNT(*)                                                             AS orders,
       ROUND(SUM(CASE WHEN status = 'delivered' THEN order_value END) / 1e5, 2) AS gmv_lakh,
       ROUND(AVG(CASE WHEN status = 'delivered' THEN order_value END), 0)   AS aov,
       ROUND(AVG(delivery_minutes), 1)                                      AS avg_delivery_min,
       ROUND(100.0 * SUM(delivery_minutes <= promised_minutes)
             / COUNT(delivery_minutes), 1)                                  AS on_time_pct,
       ROUND(100.0 * COUNT(defect_type)
             / SUM(status = 'delivered'), 2)                                AS defect_rate_pct,
       ROUND(100.0 * SUM(status = 'cancelled') / COUNT(*), 2)               AS cancel_rate_pct
FROM clean_orders
GROUP BY week
ORDER BY week;

-- Q3: Store league table: rank stores on on-time % and defect rate (RANK, CTE)
WITH store_kpi AS (
    SELECT s.store_id, s.city, s.zone,
           COUNT(*)                                                       AS orders,
           ROUND(AVG(o.delivery_minutes), 1)                              AS avg_delivery_min,
           ROUND(100.0 * SUM(o.delivery_minutes <= o.promised_minutes)
                 / COUNT(o.delivery_minutes), 1)                          AS on_time_pct,
           ROUND(100.0 * COUNT(o.defect_type) / SUM(o.status = 'delivered'), 2) AS defect_rate_pct
    FROM clean_orders o
    JOIN stores s ON s.store_id = o.store_id
    GROUP BY s.store_id, s.city, s.zone
)
SELECT *,
       RANK() OVER (ORDER BY on_time_pct DESC)     AS on_time_rank,
       RANK() OVER (ORDER BY defect_rate_pct ASC)  AS quality_rank
FROM store_kpi
ORDER BY on_time_rank;

-- Q4: Where does delivery time break? Hour-of-day bottleneck (CASE bucketing)
SELECT CASE WHEN CAST(strftime('%H', order_ts) AS INT) BETWEEN 6  AND 10 THEN '1) Morning 06-11'
            WHEN CAST(strftime('%H', order_ts) AS INT) BETWEEN 11 AND 17 THEN '2) Daytime 11-18'
            WHEN CAST(strftime('%H', order_ts) AS INT) BETWEEN 18 AND 21 THEN '3) Evening peak 18-22'
            ELSE '4) Late night 22-06' END                                   AS time_slot,
       COUNT(*)                                                             AS orders,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                   AS share_of_orders_pct,
       ROUND(AVG(delivery_minutes), 1)                                      AS avg_delivery_min,
       ROUND(100.0 * SUM(delivery_minutes <= promised_minutes)
             / COUNT(delivery_minutes), 1)                                  AS on_time_pct
FROM clean_orders
GROUP BY time_slot
ORDER BY time_slot;

-- Q5: Anomaly detection: days where network delivery time deviates >15% from its 7-day rolling average
WITH daily AS (
    SELECT DATE(order_ts) AS day,
           COUNT(*) AS orders,
           AVG(delivery_minutes) AS avg_min
    FROM clean_orders
    GROUP BY day
), rolling AS (
    SELECT day, orders, avg_min,
           AVG(avg_min) OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS prior_7d_avg
    FROM daily
)
SELECT day, orders,
       ROUND(avg_min, 1)                               AS avg_delivery_min,
       ROUND(prior_7d_avg, 1)                          AS prior_7d_avg,
       ROUND(100.0 * (avg_min - prior_7d_avg) / prior_7d_avg, 1) AS deviation_pct
FROM rolling
WHERE prior_7d_avg IS NOT NULL
  AND ABS(avg_min - prior_7d_avg) / prior_7d_avg > 0.15
ORDER BY day;

-- Q6: Defect root cause: defect mix and stockout link (orders placed when a basket category was stocked out)
WITH order_stockout AS (
    SELECT o.order_id, o.defect_type, o.status,
           MAX(CASE WHEN i.stockout_hours > 0 THEN 1 ELSE 0 END) AS hit_stockout
    FROM clean_orders o
    JOIN inventory_daily i
      ON i.store_id = o.store_id
     AND i.stock_date = DATE(o.order_ts)
     AND instr('|' || o.category_mix || '|', '|' || i.category || '|') > 0
    GROUP BY o.order_id, o.defect_type, o.status
)
SELECT CASE hit_stockout WHEN 1 THEN 'Basket had a stocked-out category' ELSE 'No stockout' END AS basket_condition,
       COUNT(*)                                                                   AS orders,
       ROUND(100.0 * SUM(status = 'cancelled') / COUNT(*), 2)                     AS cancel_rate_pct,
       ROUND(100.0 * SUM(defect_type = 'missing_item') / SUM(status = 'delivered'), 2) AS missing_item_pct,
       ROUND(100.0 * SUM(defect_type = 'damaged_item') / SUM(status = 'delivered'), 2) AS damaged_item_pct,
       ROUND(100.0 * SUM(defect_type = 'wrong_item')   / SUM(status = 'delivered'), 2) AS wrong_item_pct
FROM order_stockout
GROUP BY basket_condition;

-- Q7: Inventory turns and stockout exposure by category
SELECT category,
       SUM(units_sold)                                                     AS units_sold,
       ROUND(AVG((opening_units + closing_units) / 2.0), 0)                AS avg_inventory_per_store,
       ROUND(30.0 * AVG(units_sold) / AVG((opening_units + closing_units) / 2.0), 1) AS monthly_inventory_turns,
       ROUND(100.0 * SUM(stockout_hours > 0) / COUNT(*), 1)                AS stockout_day_pct,
       ROUND(SUM(stockout_hours), 0)                                       AS total_stockout_hours
FROM inventory_daily
GROUP BY category
ORDER BY stockout_day_pct DESC;

-- Q8: Promo pilot evaluation: difference-in-differences (pilot vs control stores, pre vs during pilot)
WITH store_day AS (
    SELECT o.store_id, s.in_promo_pilot,
           CASE WHEN DATE(o.order_ts) >= '2025-08-26' THEN 'during' ELSE 'pre' END AS period,
           DATE(o.order_ts) AS day,
           COUNT(*) AS orders,
           AVG(CASE WHEN o.status = 'delivered' THEN o.order_value END) AS aov
    FROM clean_orders o
    JOIN stores s ON s.store_id = o.store_id
    WHERE o.store_id <> 11            -- exclude store still ramping up after launch
    GROUP BY o.store_id, s.in_promo_pilot, period, day
), summary AS (
    SELECT CASE in_promo_pilot WHEN 1 THEN 'Pilot' ELSE 'Control' END AS grp,
           AVG(CASE WHEN period = 'pre'    THEN orders END) AS pre_orders_per_store_day,
           AVG(CASE WHEN period = 'during' THEN orders END) AS during_orders_per_store_day,
           AVG(CASE WHEN period = 'pre'    THEN aov END)    AS pre_aov,
           AVG(CASE WHEN period = 'during' THEN aov END)    AS during_aov
    FROM store_day
    GROUP BY grp
)
SELECT grp,
       ROUND(pre_orders_per_store_day, 1)  AS pre_orders,
       ROUND(during_orders_per_store_day, 1) AS during_orders,
       ROUND(100.0 * (during_orders_per_store_day / pre_orders_per_store_day - 1), 1) AS orders_change_pct,
       ROUND(pre_aov, 0)    AS pre_aov,
       ROUND(during_aov, 0) AS during_aov,
       ROUND(100.0 * (during_aov / pre_aov - 1), 1) AS aov_change_pct
FROM summary
ORDER BY grp DESC;

-- Q9: Week-over-week order growth by city (LAG window, PARTITION BY)
WITH city_week AS (
    SELECT s.city, strftime('%Y-W%W', o.order_ts) AS week, COUNT(*) AS orders
    FROM clean_orders o
    JOIN stores s ON s.store_id = o.store_id
    GROUP BY s.city, week
)
SELECT city, week, orders,
       LAG(orders) OVER (PARTITION BY city ORDER BY week) AS prev_week,
       ROUND(100.0 * (orders - LAG(orders) OVER (PARTITION BY city ORDER BY week))
             / LAG(orders) OVER (PARTITION BY city ORDER BY week), 1) AS wow_pct
FROM city_week
WHERE week BETWEEN '2025-W27' AND '2025-W38'   -- full weeks only
ORDER BY city, week;

-- Q10: Customer repeat behaviour: share of first-time July customers who reorder within 30 days
WITH first_order AS (
    SELECT customer_id, MIN(order_ts) AS first_ts
    FROM clean_orders
    WHERE status = 'delivered'
    GROUP BY customer_id
), repeat_flag AS (
    SELECT f.customer_id, c.segment,
           MAX(CASE WHEN o.order_ts >  f.first_ts
                     AND o.order_ts <= DATETIME(f.first_ts, '+30 days') THEN 1 ELSE 0 END) AS reordered_30d
    FROM first_order f
    JOIN customers c   ON c.customer_id = f.customer_id
    JOIN clean_orders o ON o.customer_id = f.customer_id
    WHERE f.first_ts < '2025-08-01'
    GROUP BY f.customer_id, c.segment
)
SELECT segment,
       COUNT(*)                                AS customers,
       ROUND(100.0 * AVG(reordered_30d), 1)    AS repeat_30d_pct
FROM repeat_flag
GROUP BY segment
ORDER BY repeat_30d_pct DESC;

-- Q11: App conversion funnel: open -> product view -> cart -> checkout -> order, with step drop-off
WITH stages AS (
    SELECT 1 AS step, '1) App open'       AS stage, COUNT(*)              AS sessions FROM app_sessions
    UNION ALL SELECT 2, '2) Product view',  SUM(viewed_product)   FROM app_sessions
    UNION ALL SELECT 3, '3) Add to cart',   SUM(added_to_cart)    FROM app_sessions
    UNION ALL SELECT 4, '4) Checkout start',SUM(started_checkout) FROM app_sessions
    UNION ALL SELECT 5, '5) Order placed',  SUM(placed_order)     FROM app_sessions
)
SELECT stage, sessions,
       ROUND(100.0 * sessions / FIRST_VALUE(sessions) OVER (ORDER BY step), 1)  AS pct_of_opens,
       ROUND(100.0 * sessions / LAG(sessions) OVER (ORDER BY step), 1)          AS step_conversion_pct,
       ROUND(100.0 - 100.0 * sessions / LAG(sessions) OVER (ORDER BY step), 1)  AS step_drop_off_pct
FROM stages
ORDER BY step;

-- Q12: Where is the biggest leak? Funnel segmented by device, user tenure and stockout exposure
SELECT a.device,
       CASE WHEN JULIANDAY(DATE(a.session_ts)) - JULIANDAY(c.signup_date) <= 30
            THEN 'New (<=30 days)' ELSE 'Returning' END                         AS user_type,
       CASE a.saw_stockout WHEN 1 THEN 'Saw stockout' ELSE 'No stockout' END   AS stock_view,
       COUNT(*)                                                                AS sessions,
       ROUND(100.0 * SUM(added_to_cart)    / NULLIF(SUM(viewed_product), 0), 1)  AS view_to_cart_pct,
       ROUND(100.0 * SUM(placed_order)     / NULLIF(SUM(added_to_cart), 0), 1)   AS cart_to_order_pct,
       ROUND(100.0 * SUM(placed_order)     / COUNT(*), 1)                        AS session_conversion_pct
FROM app_sessions a
JOIN customers c ON c.customer_id = a.customer_id
GROUP BY a.device, user_type, stock_view
HAVING COUNT(*) >= 300
ORDER BY session_conversion_pct ASC;

-- Q13: Impact sizing in rupees: (a) GMV lost to stockouts in the funnel, (b) promo pilot payback
WITH funnel AS (
    SELECT SUM(saw_stockout)                                                     AS stockout_sessions,
           1.0 * SUM(CASE WHEN saw_stockout = 1 THEN placed_order END) / SUM(saw_stockout)  AS conv_stockout,
           1.0 * SUM(CASE WHEN saw_stockout = 0 THEN placed_order END) / SUM(1 - saw_stockout) AS conv_clean
    FROM app_sessions
), aov AS (
    SELECT AVG(order_value) AS aov FROM clean_orders WHERE status = 'delivered'
), pilot AS (
    SELECT SUM(s.in_promo_pilot = 1 AND DATE(o.order_ts) >= '2025-08-26')                         AS pilot_orders,
           SUM(o.promo_code IS NOT NULL)                                                          AS promo_redemptions,
           COUNT(DISTINCT CASE WHEN s.in_promo_pilot = 1 AND DATE(o.order_ts) >= '2025-08-26'
                               THEN o.store_id || DATE(o.order_ts) END)                           AS pilot_store_days
    FROM clean_orders o JOIN stores s ON s.store_id = o.store_id
    WHERE o.store_id <> 11
)
SELECT 'Stockout-driven lost orders (13 weeks)' AS metric,
       ROUND(f.stockout_sessions * (f.conv_clean - f.conv_stockout), 0) AS value
FROM funnel f
UNION ALL
SELECT 'Stockout-driven lost GMV, Rs lakh (13 weeks)',
       ROUND(f.stockout_sessions * (f.conv_clean - f.conv_stockout) * a.aov / 1e5, 1)
FROM funnel f, aov a
UNION ALL
-- incremental orders = pilot orders minus what pilot stores would have done at control-group growth (+3.7%)
SELECT 'Promo incremental orders', ROUND(p.pilot_orders - p.pilot_store_days * 28.5 * 1.037, 0) FROM pilot p
UNION ALL
SELECT 'Promo discount cost, Rs lakh', ROUND(p.promo_redemptions * 50 / 1e5, 2) FROM pilot p
UNION ALL
SELECT 'Promo incremental GMV, Rs lakh',
       ROUND((p.pilot_orders - p.pilot_store_days * 28.5 * 1.037) * a.aov / 1e5, 2) FROM pilot p, aov a
UNION ALL
SELECT 'Discount cost per incremental order, Rs',
       ROUND(p.promo_redemptions * 50.0 / (p.pilot_orders - p.pilot_store_days * 28.5 * 1.037), 0) FROM pilot p;
