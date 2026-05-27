import requests
import json
import os
from datetime import datetime
from collections import Counter

BASE_URL = "https://umusic.my/collections/music/products.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LATEST_SNAPSHOT = "snapshots/latest.json"
HISTORY_FOLDER = "snapshots/history"

MAX_MESSAGE_LENGTH = 3800  # safer than 4096


# ----------------------------
# FETCH ALL PRODUCTS (PAGINATED)
# ----------------------------
def fetch_products():

    page = 1
    products = {}

    while True:

        url = f"{BASE_URL}?limit=250&page={page}"
        response = requests.get(url)
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

                    "variant_title": None
                    if variant["title"] == "Default Title"
                    else variant["title"],

                    "sku": variant.get("sku"),
                    "price": variant["price"],
                    "compare_at_price": variant.get("compare_at_price"),
                    "available": variant["available"],

                    "image": (
                        product["images"][0]["src"]
                        if product.get("images")
                        else None
                    )
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

    today = datetime.now().strftime("%Y-%m-%d")

    with open(LATEST_SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)

    with open(f"{HISTORY_FOLDER}/{today}.json", "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)


# ----------------------------
# DIFF ENGINE
# ----------------------------
def compare_snapshots(old, new):

    changes = {
        "new": [],
        "removed": [],
        "price": [],
        "restocked": [],
        "sold_out": []
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

        if o["price"] != n["price"]:
            changes["price"].append({
                "title": n["title"],
                "variant": n["variant_title"],
                "old": o["price"],
                "new": n["price"]
            })

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

    unique_products = len(
        set(p["product_id"] for p in products.values())
    )

    product_types = Counter(
        p["product_type"] for p in products.values()
    )

    vendors = Counter(
        p["vendor"] for p in products.values()
    )

    available = sum(1 for p in products.values() if p["available"])

    # SMART LOGIC: if same, don’t duplicate in UI
    same = variant_count == unique_products

    return {
        "variant_count": variant_count,
        "unique_products": unique_products,
        "show_unique": not same,
        "available": available,
        "sold_out": variant_count - available,
        "product_types": product_types,
        "vendors": vendors
    }


# ----------------------------
# MESSAGE UTILITIES
# ----------------------------
def chunk_text(text):

    chunks = []
    current = ""

    for line in text.split("\n"):

        if len(current) + len(line) > MAX_MESSAGE_LENGTH:
            chunks.append(current)
            current = line + "\n"
        else:
            current += line + "\n"

    if current:
        chunks.append(current)

    return chunks


def send_telegram(message):

    if not message:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    chunks = chunk_text(message)

    for c in chunks:

        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": c,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        })


# ----------------------------
# FORMATTERS
# ----------------------------
def format_item(item):

    return (
        f"• *{item['vendor']}*\n"
        f"  [{item['title']}]({item['url']})\n"
        f"  `{item['product_type']}` • RM{item['price']}"
    )


def build_message(changes, metrics):

    msg = ""

    # METRICS
    msg += "📊 *STORE METRICS*\n"
    
    msg += f"• Total SKUs: {metrics['variant_count']}\n"
    
    if metrics["show_unique"]:
        msg += f"• Unique Products: {metrics['unique_products']}\n"
    
    msg += f"• Available: {metrics['available']}\n"
    msg += f"• Sold Out: {metrics['sold_out']}\n\n"

    msg += "*Product Types*\n"
    for k, v in metrics["product_types"].most_common():
        msg += f"• {k}: {v}\n"

    msg += "\n━━━━━━━━━━━━━━\n\n"

    # NEW
    if changes["new"]:
        msg += f"🆕 *NEW PRODUCTS ({len(changes['new'])})*\n\n"
        for i in changes["new"]:
            msg += format_item(i) + "\n\n"

    # PRICE
    if changes["price"]:
        msg += f"💰 *PRICE CHANGES ({len(changes['price'])})*\n\n"
        for i in changes["price"]:
            msg += (
                f"• {i['title']}\n"
                f"  RM{i['old']} → RM{i['new']}\n\n"
            )

    # RESTOCKED
    if changes["restocked"]:
        msg += f"📦 *RESTOCKED ({len(changes['restocked'])})*\n\n"
        for i in changes["restocked"]:
            msg += f"• {i['title']}\n"

    # SOLD OUT
    if changes["sold_out"]:
        msg += f"\n🚫 *SOLD OUT ({len(changes['sold_out'])})*\n\n"
        for i in changes["sold_out"]:
            msg += f"• {i['title']}\n"

    return msg


# ----------------------------
# MAIN
# ----------------------------
def main():

    current = fetch_products()
    previous = load_previous_snapshot()

    changes = compare_snapshots(previous, current)
    metrics = build_metrics(current)

    message = build_message(changes, metrics)

    send_telegram(message)
    save_snapshot(current)


if __name__ == "__main__":
    main()
