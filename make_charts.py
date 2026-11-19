"""Builds the README charts (charts/*.png) from qcommerce.db. Run after generate_data.py."""
import sqlite3, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

os.makedirs("charts", exist_ok=True)
con = sqlite3.connect("qcommerce.db")
q = lambda sql: con.execute(sql).fetchall()

BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#b9b8b2"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10.5, "text.color": INK,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlesize": 13,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 26,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.dpi": 160,
})

def subtitle(ax, text):
    ax.text(0, 1.02, text, transform=ax.transAxes, color=INK2, fontsize=10, va="bottom")

def save(fig, name):
    fig.tight_layout()
    fig.savefig(f"charts/{name}.png", facecolor=SURF)
    plt.close(fig)

# 1. On-time % by time of day
rows = q("""SELECT CASE WHEN CAST(strftime('%H',order_ts) AS INT) BETWEEN 6 AND 10 THEN 'Morning\n6–11 AM'
                        WHEN CAST(strftime('%H',order_ts) AS INT) BETWEEN 11 AND 17 THEN 'Daytime\n11 AM–6 PM'
                        WHEN CAST(strftime('%H',order_ts) AS INT) BETWEEN 18 AND 21 THEN 'Evening peak\n6–10 PM'
                        ELSE 'Late night\n10 PM–6 AM' END s,
                   MIN(order_ts), 100.0*SUM(delivery_minutes<=10)/COUNT(delivery_minutes),
                   100.0*COUNT(*)/(SELECT COUNT(*) FROM clean_orders)
            FROM clean_orders GROUP BY s ORDER BY CASE WHEN s LIKE 'Morning%' THEN 1 WHEN s LIKE 'Day%' THEN 2
                                                       WHEN s LIKE 'Even%' THEN 3 ELSE 4 END""")
fig, ax = plt.subplots(figsize=(8, 4.4))
labels = [r[0] for r in rows]; vals = [r[2] for r in rows]; share = [r[3] for r in rows]
cols = [ORANGE if "Evening" in l else BLUE for l in labels]
bars = ax.bar(labels, vals, color=cols, width=0.55)
for b, v, sh in zip(bars, vals, share):
    ax.text(b.get_x()+b.get_width()/2, v+1.5, f"{v:.0f}% on time", ha="center", fontsize=10, color=INK)
    ax.text(b.get_x()+b.get_width()/2, 3, f"{sh:.0f}% of orders", ha="center", fontsize=9, color="white")
ax.set_ylim(0, 100); ax.set_ylabel("Orders delivered within 10 min (%)"); ax.grid(axis="x", visible=False)
ax.set_title("The evening peak is where the 10-minute promise breaks")
subtitle(ax, "6–10 PM carries the most orders but has the lowest on-time rate")
save(fig, "1_on_time_by_time_slot")

# 2. Store league table
rows = q("""SELECT s.zone || ' (' || s.city || ')', 100.0*SUM(o.delivery_minutes<=10)/COUNT(o.delivery_minutes),
                   AVG(o.delivery_minutes)
            FROM clean_orders o JOIN stores s USING(store_id) GROUP BY s.store_id ORDER BY 2""")
fig, ax = plt.subplots(figsize=(8, 5.2))
names = [r[0] for r in rows]; vals = [r[1] for r in rows]
cols = [ORANGE if v < 55 else BLUE for v in vals]
ax.barh(names, vals, color=cols, height=0.6)
for i, (v, m) in enumerate(zip(vals, [r[2] for r in rows])):
    ax.text(v+1, i, f"{v:.0f}%  ·  {m:.1f} min avg", va="center", fontsize=9, color=INK2)
ax.set_xlim(0, 100); ax.set_xlabel("Orders delivered within 10 min (%)"); ax.grid(axis="y", visible=False)
ax.set_title("Three stores fall far below the rest of the network")
subtitle(ax, "On-time rate by dark store; highlighted stores are below 55%")
save(fig, "2_store_on_time_ranking")

# 3. Daily delivery time with rolling baseline and anomalies
rows = q("""WITH d AS (SELECT DATE(order_ts) day, AVG(delivery_minutes) m FROM clean_orders GROUP BY day)
            SELECT day, m, AVG(m) OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) FROM d""")
days = [datetime.fromisoformat(r[0]) for r in rows]; m = [r[1] for r in rows]; base = [r[2] for r in rows]
fig, ax = plt.subplots(figsize=(9, 4.4))
ax.plot(days, m, color=BLUE, lw=2, label="Daily average delivery time")
ax.plot(days, base, color=GRAY, lw=2, ls="--", label="Prior 7-day average (baseline)")
an = [(d, v) for d, v, b in zip(days, m, base) if b and abs(v-b)/b > 0.15]
ax.scatter([a[0] for a in an], [a[1] for a in an], s=70, color=ORANGE, zorder=3,
           edgecolor=SURF, linewidth=2, label="Anomaly (>15% above baseline)")
prev = None
for d, v in an:
    if prev and (d - prev).days == 1:
        prev = d; continue
    prev = d
    ax.annotate(d.strftime("%d %b"), (d, v), xytext=(0, 9), textcoords="offset points", ha="center", fontsize=9, color=INK)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b")); ax.set_ylim(7, 15)
ax.set_ylabel("Minutes"); ax.legend(frameon=False, loc="upper left", ncol=3, fontsize=9, bbox_to_anchor=(0, 1.0))
ax.set_title("Four demand-surge days flagged automatically")
subtitle(ax, "Network delivery time vs its trailing 7-day baseline (Jul–Sep 2025)")
save(fig, "3_delivery_time_anomalies")

# 4. Stockout exposure by category
rows = q("""SELECT category, 100.0*SUM(stockout_hours>0)/COUNT(*) FROM inventory_daily GROUP BY category ORDER BY 2""")
fig, ax = plt.subplots(figsize=(8, 4.4))
names = [r[0] for r in rows]; vals = [r[1] for r in rows]
ax.barh(names, vals, color=[ORANGE if v > 10 else BLUE for v in vals], height=0.6)
for i, v in enumerate(vals):
    ax.text(v+0.4, i, f"{v:.1f}%", va="center", fontsize=9.5, color=INK)
ax.set_xlim(0, 25); ax.set_xlabel("Store-days with a stockout (%)"); ax.grid(axis="y", visible=False)
ax.set_title("Perishables stock out on roughly 1 in 5 store-days")
subtitle(ax, "Every other category stays under 3%")
save(fig, "4_stockouts_by_category")

# 5. Funnel + stockout impact (two panels, same unit: % of sessions)
f = q("""SELECT COUNT(*), SUM(viewed_product), SUM(added_to_cart), SUM(started_checkout), SUM(placed_order) FROM app_sessions""")[0]
c = q("""SELECT saw_stockout, 100.0*SUM(placed_order)/COUNT(*) FROM app_sessions GROUP BY saw_stockout ORDER BY saw_stockout""")
fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.6), gridspec_kw={"width_ratios": [2.2, 1]})
stages = ["App open", "Product view", "Add to cart", "Checkout", "Order placed"]
pct = [100*v/f[0] for v in f]
drops = [None] + [100-100*f[i]/f[i-1] for i in range(1, 5)]
cols = [BLUE]*5; cols[2] = ORANGE
a1.bar(stages, pct, color=cols, width=0.6)
for i, p in enumerate(pct):
    a1.text(i, p+2, f"{p:.0f}%", ha="center", fontsize=10)
    if drops[i]:
        a1.text(i, p/2, f"−{drops[i]:.0f}%\nvs prior", ha="center", fontsize=8.5, color="white")
a1.set_ylim(0, 112); a1.set_ylabel("% of app sessions"); a1.grid(axis="x", visible=False)
a1.set_title("Biggest leak: product view → add to cart"); subtitle(a1, "Only 36% of sessions end in an order")
a2.bar(["No stockout\nseen", "Saw a\nstockout"], [c[0][1], c[1][1]], color=[BLUE, ORANGE], width=0.55)
for i, v in enumerate([c[0][1], c[1][1]]):
    a2.text(i, v+1.5, f"{v:.0f}%", ha="center", fontsize=10.5)
a2.set_ylim(0, 50); a2.set_ylabel("Session conversion (%)"); a2.grid(axis="x", visible=False)
a2.set_title("Stockouts halve it"); subtitle(a2, "≈ ₹22L GMV lost / 13 wks")
save(fig, "5_funnel_and_stockout_impact")

# 6. Promo pilot difference-in-differences
rows = q("""WITH sd AS (SELECT o.store_id, s.in_promo_pilot p,
                        CASE WHEN DATE(o.order_ts) >= '2025-08-26' THEN 1 ELSE 0 END during,
                        DATE(o.order_ts) d, COUNT(*) n
                        FROM clean_orders o JOIN stores s USING(store_id) WHERE o.store_id <> 11
                        GROUP BY o.store_id, p, during, d)
            SELECT p, during, AVG(n) FROM sd GROUP BY p, during ORDER BY p, during""")
v = {(r[0], r[1]): r[2] for r in rows}
fig, ax = plt.subplots(figsize=(9, 4.4))
import numpy as np
x = np.arange(2); w = 0.34
pre = [v[(0, 0)], v[(1, 0)]]; dur = [v[(0, 1)], v[(1, 1)]]
ax.bar(x-w/2-0.01, pre, w, color=GRAY, label="Before pilot (8 weeks)")
ax.bar(x+w/2+0.01, dur, w, color=BLUE, label="During pilot (5 weeks)")
for i in range(2):
    ax.text(x[i]-w/2, pre[i]+0.6, f"{pre[i]:.1f}", ha="center", fontsize=9.5)
    ax.text(x[i]+w/2, dur[i]+0.6, f"{dur[i]:.1f}  ({100*(dur[i]/pre[i]-1):+.0f}%)", ha="center", fontsize=9.5)
ax.set_xticks(x, ["Control stores", "Pilot stores (₹50 off)"]); ax.set_ylim(0, 42)
ax.set_ylabel("Orders per store per day"); ax.grid(axis="x", visible=False)
ax.legend(frameon=False, loc="upper left", fontsize=9)
ax.set_title("Promo pilot: +18 pp extra orders, but ₹157 discount per extra order")
subtitle(ax, "Pilot stores grew +22% vs +4% in control stores (difference-in-differences)")
save(fig, "6_promo_pilot_lift")
print(sorted(os.listdir("charts")))
