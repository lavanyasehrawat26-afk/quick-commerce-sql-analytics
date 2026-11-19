"""
Generates a SYNTHETIC quick-commerce dataset (SQLite) for SQL practice.
No real company data is used. Seeded for reproducibility.

Tables: stores, customers, orders, inventory_daily
Period: 2025-07-01 to 2025-09-28 (13 weeks)
"""
import random, sqlite3, math
from datetime import date, datetime, timedelta

random.seed(42)
DB = "qcommerce.db"
START = date(2025, 7, 1)
DAYS = 90
CATEGORIES = {  # category: (avg item price, daily demand per store, stock-out proneness)
    "Dairy & Bread": (55, 160, 0.10),
    "Fruits & Vegetables": (45, 190, 0.13),
    "Snacks & Beverages": (70, 140, 0.03),
    "Staples": (120, 80, 0.02),
    "Personal Care": (180, 40, 0.02),
    "Household": (150, 35, 0.02),
    "Electronics & Accessories": (650, 6, 0.04),
}

stores = [
    # id, city, zone, opened, pilot_promo
    (1, "Gurgaon", "Sector 29", "2023-02-10", 1),
    (2, "Gurgaon", "DLF Phase 3", "2023-05-18", 0),
    (3, "Gurgaon", "Sohna Road", "2024-08-01", 1),
    (4, "Gurgaon", "Golf Course Ext", "2025-01-15", 0),
    (5, "Delhi", "Saket", "2022-11-01", 1),
    (6, "Delhi", "Lajpat Nagar", "2023-03-22", 0),
    (7, "Delhi", "Dwarka Sec 12", "2024-02-05", 0),
    (8, "Delhi", "Rohini Sec 9", "2024-06-30", 1),
    (9, "Noida", "Sector 18", "2023-07-07", 0),
    (10, "Noida", "Sector 62", "2024-04-12", 1),
    (11, "Noida", "Sector 137", "2025-03-01", 0),
    (12, "Delhi", "Mayur Vihar", "2024-10-10", 0),
]
# Store 7 has a planted rider-shortage problem; store 11 is new and still ramping.
STORE_DELAY = {7: 4.5, 11: 2.0, 4: 1.0}
PILOT_START = START + timedelta(days=56)  # promo pilot runs in the last 34 days
RAIN_DAYS = {START + timedelta(days=d) for d in (17, 18, 41, 63)}

con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
DROP TABLE IF EXISTS stores; DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS inventory_daily;
CREATE TABLE stores (store_id INTEGER PRIMARY KEY, city TEXT, zone TEXT,
                     opened_on DATE, in_promo_pilot INTEGER);
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, home_store_id INTEGER,
                        signup_date DATE, segment TEXT);
CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, store_id INTEGER,
                     order_ts TIMESTAMP, category_mix TEXT, items INTEGER,
                     order_value REAL, promo_code TEXT, status TEXT,
                     promised_minutes INTEGER, delivery_minutes REAL, defect_type TEXT);
CREATE TABLE inventory_daily (store_id INTEGER, category TEXT, stock_date DATE,
                              opening_units INTEGER, units_sold INTEGER,
                              closing_units INTEGER, stockout_hours REAL);
""")
cur.executemany("INSERT INTO stores VALUES (?,?,?,?,?)", stores)

# customers
customers = []
cid = 1
for s in stores:
    n = 900 if s[0] != 11 else 400
    for _ in range(n):
        signup = START - timedelta(days=random.randint(0, 400)) if random.random() < 0.7 \
            else START + timedelta(days=random.randint(0, DAYS - 1))
        seg = random.choices(["Regular", "Occasional", "Power"], [0.5, 0.35, 0.15])[0]
        customers.append((cid, s[0], signup.isoformat(), seg))
        cid += 1
cur.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)
by_store = {}
for c in customers:
    by_store.setdefault(c[1], []).append(c)

HOUR_W = [0.2,0.1,0.05,0.05,0.05,0.1,0.4,1.0,1.6,1.5,1.2,1.1,1.2,1.1,0.9,0.9,1.0,1.3,1.8,2.4,2.6,2.2,1.4,0.6]
SEG_W = {"Regular": 1.0, "Occasional": 0.35, "Power": 2.6}
cats = list(CATEGORIES)

# inventory first (stockouts drive cancellations)
inv_rows, stockout_lookup = [], {}
for s in stores:
    for cat, (_, demand, prone) in CATEGORIES.items():
        stock = int(demand * 2.5)
        for d in range(DAYS):
            day = START + timedelta(days=d)
            dem = demand * (0.6 if s[0] == 11 else 1) * random.uniform(0.8, 1.2) * (1.3 if day in RAIN_DAYS else 1)
            # replenishment: perishables restock daily to ~1 day of cover; others every 3 days to ~3.5 days
            perishable = cat in ("Dairy & Bread", "Fruits & Vegetables")
            if perishable:
                stock = max(stock, int(demand * random.uniform(0.95, 1.45)))
            elif d % 3 == 0:
                stock = max(stock, int(demand * random.uniform(3.3, 4.2)))
            sold = min(stock, int(dem))
            so_hours = 0.0
            if dem > stock:
                so_hours = round(min(10, 2 + 8 * (dem - stock) / dem), 1)
            elif random.random() < prone * 0.3:
                so_hours = round(random.uniform(0.5, 2), 1)
            closing = stock - sold
            inv_rows.append((s[0], cat, day.isoformat(), stock, sold, closing, so_hours))
            stockout_lookup[(s[0], cat, day)] = so_hours
            stock = closing
cur.executemany("INSERT INTO inventory_daily VALUES (?,?,?,?,?,?,?)", inv_rows)

# orders
orders, oid = [], 1
for d in range(DAYS):
    day = START + timedelta(days=d)
    weekend = day.weekday() >= 5
    for s in stores:
        sid, pilot = s[0], s[4]
        base = 26 if sid != 11 else 12 + d * 0.08
        lam = base * (1.15 if weekend else 1) * (1.35 if day in RAIN_DAYS else 1)
        promo_on = pilot and day >= PILOT_START
        if promo_on:
            lam *= 1.14
        lam *= 1 + d * 0.002  # organic growth
        n = max(0, int(random.gauss(lam, math.sqrt(lam))))
        pool = by_store[sid]
        weights = [SEG_W[c[3]] if c[2] <= day.isoformat() else 0 for c in pool]
        for _ in range(n):
            cust = random.choices(pool, weights)[0]
            hour = random.choices(range(24), HOUR_W)[0]
            ts = datetime(day.year, day.month, day.day, hour, random.randint(0, 59), random.randint(0, 59))
            mix = random.sample(cats, k=random.choices([1, 2, 3], [0.45, 0.4, 0.15])[0])
            items = random.randint(1, 4) + len(mix)
            value = sum(CATEGORIES[c][0] for c in mix) * items / len(mix) * random.uniform(0.7, 1.3)
            promo = None
            if promo_on and random.random() < 0.45:
                promo = "PILOT50"
                value = max(99, value - 50) * 0.97  # discount + smaller baskets
            # cancellation if a category in the basket is stocked out
            so = max(stockout_lookup[(sid, c, day)] for c in mix)
            cancel_p = 0.01 + (0.08 * so / 10 if so > 0 else 0)
            status = "cancelled" if random.random() < cancel_p else "delivered"
            # delivery time
            peak = 2.6 if 18 <= hour <= 21 else (1.0 if 8 <= hour <= 10 else 0)
            rain = 3.5 if day in RAIN_DAYS else 0
            mins = random.gauss(7.4, 1.8) + peak + rain + STORE_DELAY.get(sid, 0) + items * 0.08
            mins = round(max(4.0, mins), 1)
            defect = None
            if status == "delivered":
                r = random.random()
                p_missing = 0.008 + (0.025 if so > 0 else 0)
                p_damaged = 0.004 + (0.006 if "Fruits & Vegetables" in mix else 0)
                if r < p_missing:
                    defect = "missing_item"
                elif r < p_missing + p_damaged:
                    defect = "damaged_item"
                elif r < p_missing + p_damaged + (0.01 if mins > 18 else 0.002):
                    defect = "wrong_item"
            else:
                mins = None
            orders.append((oid, cust[0], sid, ts.strftime("%Y-%m-%d %H:%M:%S"), "|".join(mix), items,
                           round(value, 2), promo, status, 10, mins, defect))
            oid += 1

# planted data-quality issues for the validation query
orders.append((orders[100][0] + 10**6, *orders[100][1:]))           # duplicate row (new id, same content)
orders.append((oid + 2, 99999, 3, "2025-08-01 12:00:00", "Staples", 2, 240.0, None, "delivered", 10, 9.1, None))  # orphan customer
orders.append((oid + 3, 15, 2, "2025-08-02 13:00:00", "Staples", 2, -120.0, None, "delivered", 10, 8.0, None))   # negative value
orders.append((oid + 4, 16, 2, "2025-08-03 14:00:00", "Staples", 2, 210.0, None, "delivered", 10, None, None))   # delivered w/o time
cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", orders)
con.commit()
for t in ("stores", "customers", "orders", "inventory_daily"):
    print(t, cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
con.close()

# ---------------- App funnel sessions (added module) ----------------
# Every placed order has a converting session; non-converting sessions drop off at a stage.
random.seed(7)
con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
DROP TABLE IF EXISTS app_sessions;
CREATE TABLE app_sessions (session_id INTEGER PRIMARY KEY, customer_id INTEGER, store_id INTEGER,
    session_ts TIMESTAMP, device TEXT, saw_stockout INTEGER,
    viewed_product INTEGER, added_to_cart INTEGER, started_checkout INTEGER, placed_order INTEGER);
""")
signup = {c[0]: c[2] for c in customers}
rows, sid_ = [], 1
order_rows = cur.execute("SELECT customer_id, store_id, order_ts FROM orders WHERE order_id < 1000000 AND customer_id <> 99999").fetchall()
for cust, store, ts in order_rows:
    device = random.choices(["Android", "iOS"], [0.68, 0.32])[0]
    rows.append((sid_, cust, store, ts, device, int(random.random() < 0.10), 1, 1, 1, 1)); sid_ += 1
    # non-converting sessions
    for _ in range(random.choices([0, 1, 2, 3, 4], [0.15, 0.3, 0.3, 0.15, 0.1])[0]):
        device = random.choices(["Android", "iOS"], [0.68, 0.32])[0]
        so = int(random.random() < 0.22)
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") - timedelta(hours=random.randint(1, 120))
        new_user = (dt.date() - date.fromisoformat(signup[cust])).days <= 30
        # drop-off stage: browse-only, view-only, cart abandon, checkout abandon
        p_view = 0.70
        p_cart = 0.55 - (0.25 if so else 0)                          # stockouts kill add-to-cart
        p_chk = 0.50 - (0.12 if device == "Android" else 0) - (0.10 if new_user else 0)  # payment/address friction
        v = int(random.random() < p_view)
        a = int(v and random.random() < p_cart)
        c = int(a and random.random() < p_chk)
        rows.append((sid_, cust, store, dt.strftime("%Y-%m-%d %H:%M:%S"), device, so, v, a, c, 0)); sid_ += 1
cur.executemany("INSERT INTO app_sessions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
con.commit()
print("app_sessions", cur.execute("SELECT COUNT(*) FROM app_sessions").fetchone()[0])
con.close()
