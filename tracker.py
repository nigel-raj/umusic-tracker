import requests
import json
import os
from datetime import datetime, timezone
from collections import Counter
from zoneinfo import ZoneInfo

BASE_URL = "https://umusic.my/collections/music/products.json"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LATEST_SNAPSHOT = "snapshots/latest.json"
HISTORY_FOLDER = "snapshots/history"
MAX_MESSAGE_LENGTH = 3800
MAX_HISTORY_FILES = 14  # keep last 14 snapshots (~7 days at 30-min runs x2 daily commits)
IS_DAILY_RUN = os.getenv("IS_DAILY_RUN", "false").lower() == "true"

WATCH_TAGS = {"vinyl", "k-pop", "kpop", "limited", "limited edition"}


# ----------------------------
# FETCH ALL PRODUCTS (PAGINATED)
# ----------------------------
def fetch_products():
    page = 1
    products = {}
    while True:
        url = f"{BASE_URL}?limit=250&page={page}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        batch = data.get("products", [])
        if not batch:
            break
        for product in batch:
            for variant in product["variants"]:
                key = str(variant["id"])
                products[key] = {
                    "product_id": product["id"],
                    "title": product["title"],
                    "handle": product["handle"],
                    "url": f"https://umusic.my/products/{product['handle']}",
                    "vendor": product.get("vendor", "Unknown"),
                    "product_type": product.get("product_type", "Unknown"),
                    "created_at": product.get("created_at"),
                    "updated_at": product.get("updated_at"),
                    "tags": product.get("tags", []),
                    "variant_title": (
                        None
                        if variant["title"] == "Default Title"
                        else variant["title"]
                    ),
                    "sku": variant.get("sku"),
                    "price": variant["price"],
                    "compare_at_price": variant.get("compare_at_price"),
                    "available": variant["available"],
                    "image": (
                        product["images"][0]["src"]
                        if product.get("images")
                        else None
                    ),
                }
        page += 1
    return products


# ----------------------------
# SNAPSHOT
# ----------------------------
def load_previous_snapshot():
    if not os.path.exists(LATEST_SNAPSHOT):
        return {}
    with open(LATEST_SNAPSHOT, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(products):
    os.makedirs("snapshots", exist_ok=True)
    os.makedirs(HISTORY_FOLDER, exist_ok=True)

    # Use timestamp to avoid collision on multiple runs per day
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    with open(LATEST_SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)
    with open(f"{HISTORY_FOLDER}/{timestamp}.json", "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)

    prune_history()


def prune_history():
    """Keep only the most recent MAX_HISTORY_FILES snapshots to control repo size."""
    files = sorted(
        [f for f in os.listdir(HISTORY_FOLDER) if f.endswith(".json")]
    )
    excess = len(files) - MAX_HISTORY_FILES
    if excess > 0:
        for old_file in files[:excess]:
            os.remove(os.path.join(HISTORY_FOLDER, old_file))


# ----------------------------
# DIFF ENGINE
# ----------------------------
def compare_snapshots(old, new):
    changes = {
        "new": [],
        "removed": [],
        "price_drop": [],
        "price_increase": [],
        "restocked": [],
        "sold_out": [],
        "sale_started": [],
        "sale_ended": [],
    }
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    for k in new_keys - old_keys:
        changes["new"].append(new[k])

    for k in old_keys - new_keys:
        changes["removed"].append(old[k])

    for k in old_keys & new_keys:
        o = old[k]
        n = new[k]

        # Price direction
        if o["price"] != n["price"]:
            old_f = float(o["price"])
            new_f = float(n["price"])
            pct = abs((new_f - old_f) / old_f * 100) if old_f else 0
            entry = {
                "title": n["title"],
                "variant": n["variant_title"],
                "url": n["url"],
                "vendor": n["vendor"],
                "old": o["price"],
                "new": n["price"],
                "pct": pct,
                "tags": n.get("tags", []),
            }
            if new_f < old_f:
                changes["price_drop"].append(entry)
            else:
                changes["price_increase"].append(entry)

        # compare_at_price: sale detection
        old_cap = o.get("compare_at_price")
        new_cap = n.get("compare_at_price")
        if not old_cap and new_cap:
            changes["sale_started"].append({
                **n,
                "original_price": new_cap,
            })
        elif old_cap and not new_cap:
            changes["sale_ended"].append(n)

        # Stock
        if not o["available"] and n["available"]:
            changes["restocked"].append(n)
        if o["available"] and not n["available"]:
            changes["sold_out"].append(n)

    return changes


# ----------------------------
# METRICS
# ----------------------------
def build_metrics(products):
    variant_count = len(products)
    unique_products = len(set(p["product_id"] for p in products.values()))
    product_types = Counter(p["product_type"] for p in products.values())
    vendors = Counter(p["vendor"] for p in products.values())
    available = sum(1 for p in products.values() if p["available"])
    on_sale = sum(
        1 for p in products.values() if p.get("compare_at_price")
    )
    same = variant_count == unique_products
    return {
        "variant_count": variant_count,
        "unique_products": unique_products,
        "show_unique": not same,
        "available": available,
        "sold_out": variant_count - available,
        "on_sale": on_sale,
        "product_types": product_types,
        "vendors": vendors,
    }


def build_on_sale_list(products):
    """Return all variants currently marked on sale (compare_at_price set)."""
    on_sale = []
    seen_products = set()
    for v in products.values():
        if v.get("compare_at_price"):
            pid = v["product_id"]
            if pid not in seen_products:
                seen_products.add(pid)
                try:
                    discount_pct = (
                        (float(v["compare_at_price"]) - float(v["price"]))
                        / float(v["compare_at_price"])
                        * 100
                    )
                except (ValueError, ZeroDivisionError):
                    discount_pct = 0
                on_sale.append({**v, "discount_pct": discount_pct})
    on_sale.sort(key=lambda x: x["discount_pct"], reverse=True)
    return on_sale


def is_priority(item):
    """True if item tags overlap with WATCH_TAGS."""
    tags = {t.lower() for t in item.get("tags", [])}
    return bool(tags & WATCH_TAGS)


# ----------------------------
# MESSAGE UTILITIES
# ----------------------------
def chunk_text(text):
    chunks = []
    current = ""
    for line in text.split("\n"):
        addition = line + "\n"
        if len(current) + len(addition) > MAX_MESSAGE_LENGTH:
            chunks.append(current)
            current = addition
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


def send_telegram(message):
    if not message or not message.strip():
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = chunk_text(message)
    for chunk in chunks:
        try:
            resp = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError:
            # Retry without Markdown if parse failed (e.g. split mid-formatting)
            requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )


# ----------------------------
# FORMATTERS
# ----------------------------
def format_item(item):
    priority = " ⭐" if is_priority(item) else ""
    return (
        f"• *{item['vendor']}*{priority}\n"
        f"  [{item['title']}]({item['url']})\n"
        f"  `{item['product_type']}` • RM{item['price']}"
    )


def format_price_change(item, direction):
    arrow = "🔻" if direction == "drop" else "🔺"
    action = "off" if direction == "drop" else "increase"
    priority = " ⭐" if is_priority(item) else ""
    variant_str = f" — {item['variant']}" if item["variant"] else ""
    return (
        f"• *{item['vendor']}*{priority}\n"
        f"  [{item['title']}{variant_str}]({item['url']})\n"
        f"  {arrow} RM{item['old']} → RM{item['new']} *({item['pct']:.0f}% {action})*"
    )


def format_sale_item(item):
    priority = " ⭐" if is_priority(item) else ""
    try:
        pct = (
            (float(item["original_price"]) - float(item["price"]))
            / float(item["original_price"])
            * 100
        )
        pct_str = f" *({pct:.0f}% off)*"
    except (ValueError, ZeroDivisionError):
        pct_str = ""
    return (
        f"• *{item['vendor']}*{priority}\n"
        f"  [{item['title']}]({item['url']})\n"
        f"  ~~RM{item['original_price']}~~ → RM{item['price']}{pct_str}"
    )


# ----------------------------
# MESSAGE BUILDERS
# ----------------------------
def build_alert_message(changes):
    """
    Immediate alert message: new products, price drops, sales started, restocks.
    Only sent when there are actual changes.
    """
    msg = ""
    from zoneinfo import ZoneInfo
    now_str = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%d %b %Y, %I:%M %p")
    msg += f"🎵 *UMusic MY — Changes Detected*\n_{now_str}_\n\n━━━━━━━━━━━━━━\n\n"

    # NEW PRODUCTS
    if changes["new"]:
        priority_new = [i for i in changes["new"] if is_priority(i)]
        other_new = [i for i in changes["new"] if not is_priority(i)]
        msg += f"🆕 *NEW PRODUCTS ({len(changes['new'])})*\n\n"
        if priority_new:
            msg += "⭐ *Priority Items*\n"
            for i in priority_new:
                msg += format_item(i) + "\n\n"
        if other_new:
            if priority_new:
                msg += "*Others*\n"
            for i in other_new:
                msg += format_item(i) + "\n\n"

    # SALE STARTED
    if changes["sale_started"]:
        msg += f"🏷️ *SALE STARTED ({len(changes['sale_started'])})*\n\n"
        for i in changes["sale_started"]:
            msg += format_sale_item(i) + "\n\n"

    # PRICE DROPS
    if changes["price_drop"]:
        msg += f"💸 *PRICE DROPS ({len(changes['price_drop'])})*\n\n"
        for i in sorted(changes["price_drop"], key=lambda x: x["pct"], reverse=True):
            msg += format_price_change(i, "drop") + "\n\n"

    # PRICE INCREASES
    if changes["price_increase"]:
        msg += f"🔺 *PRICE INCREASES ({len(changes['price_increase'])})*\n\n"
        for i in changes["price_increase"]:
            msg += format_price_change(i, "increase") + "\n\n"

    # SALE ENDED
    if changes["sale_ended"]:
        msg += f"🔚 *SALE ENDED ({len(changes['sale_ended'])})*\n\n"
        for i in changes["sale_ended"]:
            msg += f"• [{i['title']}]({i['url']}) — RM{i['price']}\n"
        msg += "\n"

    # RESTOCKED
    if changes["restocked"]:
        msg += f"📦 *RESTOCKED ({len(changes['restocked'])})*\n\n"
        for i in changes["restocked"]:
            msg += f"• *{i['vendor']}* — {i['title']}\n"
        msg += "\n"

    # REMOVED
    if changes["removed"]:
        msg += f"❌ *REMOVED ({len(changes['removed'])})*\n\n"
        for i in changes["removed"]:
            msg += f"• *{i['vendor']}* — {i['title']}\n"
        msg += "\n"

    # SOLD OUT (lower priority, still in alert)
    if changes["sold_out"]:
        msg += f"🚫 *SOLD OUT ({len(changes['sold_out'])})*\n\n"
        for i in changes["sold_out"]:
            msg += f"• {i['title']}\n"

    return msg.strip()


def build_daily_digest(metrics, products):
    """
    Daily digest: store metrics + full on-sale list.
    Sent once per day via IS_DAILY_RUN=true.
    """
    now_str = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%d %b %Y")
    msg = f"📋 *UMusic MY — Daily Digest*\n_{now_str}_\n\n━━━━━━━━━━━━━━\n\n"

    # METRICS
    msg += "📊 *STORE METRICS*\n"
    msg += f"• Total SKUs: {metrics['variant_count']}\n"
    if metrics["show_unique"]:
        msg += f"• Unique Products: {metrics['unique_products']}\n"
    msg += f"• Available: {metrics['available']}\n"
    msg += f"• Sold Out: {metrics['sold_out']}\n"
    msg += f"• Currently On Sale: {metrics['on_sale']}\n\n"

    msg += "*By Type*\n"
    for k, v in metrics["product_types"].most_common():
        msg += f"• {k}: {v}\n"
    msg += "\n"

    msg += "*By Vendor*\n"
    for k, v in metrics["vendors"].most_common(10):
        msg += f"• {k}: {v}\n"
    msg += "\n━━━━━━━━━━━━━━\n\n"

    # CURRENT SALES
    on_sale_list = build_on_sale_list(products)
    if on_sale_list:
        msg += f"🏷️ *ALL ITEMS CURRENTLY ON SALE ({len(on_sale_list)})*\n\n"
        for i in on_sale_list:
            priority = " ⭐" if is_priority(i) else ""
            msg += (
                f"• *{i['vendor']}*{priority} — [{i['title']}]({i['url']})\n"
                f"  ~~RM{i['compare_at_price']}~~ → RM{i['price']} "
                f"*({i['discount_pct']:.0f}% off)*\n\n"
            )
    else:
        msg += "🏷️ *No items currently on sale.*\n"

    return msg.strip()


# ----------------------------
# MAIN
# ----------------------------
def main():
    current = fetch_products()
    previous = load_previous_snapshot()
    changes = compare_snapshots(previous, current)
    metrics = build_metrics(current)

    # Always send the daily digest when triggered as a daily run
    if IS_DAILY_RUN:
        digest = build_daily_digest(metrics, current)
        send_telegram(digest)

    # Only send alert if there are actual changes
    has_changes = any(v for v in changes.values())
    if has_changes:
        alert = build_alert_message(changes)
        send_telegram(alert)

    save_snapshot(current)


if __name__ == "__main__":
    main()
