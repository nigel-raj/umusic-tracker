import requests
import json
import os
from datetime import datetime

SHOP_URL = "https://umusic.my/collections/music/products.json?limit=250"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SNAPSHOT_FILE = "snapshots/latest.json"


def fetch_products():
    response = requests.get(SHOP_URL)
    data = response.json()

    products = {}

    for product in data["products"]:
        for variant in product["variants"]:

            key = str(variant["id"])

            products[key] = {
                "product_title": product["title"],
                "variant_title": variant["title"],
                "sku": variant.get("sku"),
                "price": variant["price"],
                "available": variant["available"]
            }

    return products


def load_previous_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return {}

    with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(products):
    os.makedirs("snapshots", exist_ok=True)

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)


def compare_snapshots(old, new):

    new_products = []
    removed_products = []
    price_changes = []
    restocked = []
    sold_out = []

    old_keys = set(old.keys())
    new_keys = set(new.keys())

    # New products
    for key in new_keys - old_keys:
        item = new[key]
        new_products.append(
            f"• {item['product_title']} ({item['variant_title']}) - RM{item['price']}"
        )

    # Removed products
    for key in old_keys - new_keys:
        item = old[key]
        removed_products.append(
            f"• {item['product_title']} ({item['variant_title']})"
        )

    # Existing products
    for key in old_keys & new_keys:

        old_item = old[key]
        new_item = new[key]

        # Price changes
        if old_item["price"] != new_item["price"]:
            price_changes.append(
                f"• {new_item['product_title']} ({new_item['variant_title']})\n"
                f"  RM{old_item['price']} → RM{new_item['price']}"
            )

        # Restocked
        if not old_item["available"] and new_item["available"]:
            restocked.append(
                f"• {new_item['product_title']} ({new_item['variant_title']})"
            )

        # Sold out
        if old_item["available"] and not new_item["available"]:
            sold_out.append(
                f"• {new_item['product_title']} ({new_item['variant_title']})"
            )

    return {
        "new_products": new_products,
        "removed_products": removed_products,
        "price_changes": price_changes,
        "restocked": restocked,
        "sold_out": sold_out
    }


def build_message(changes):

    sections = []

    if changes["new_products"]:
        sections.append(
            "🆕 NEW PRODUCTS\n" +
            "\n".join(changes["new_products"])
        )

    if changes["removed_products"]:
        sections.append(
            "❌ REMOVED PRODUCTS\n" +
            "\n".join(changes["removed_products"])
        )

    if changes["price_changes"]:
        sections.append(
            "💰 PRICE CHANGES\n" +
            "\n".join(changes["price_changes"])
        )

    if changes["restocked"]:
        sections.append(
            "📦 RESTOCKED\n" +
            "\n".join(changes["restocked"])
        )

    if changes["sold_out"]:
        sections.append(
            "🚫 SOLD OUT\n" +
            "\n".join(changes["sold_out"])
        )

    if not sections:
        return None

    today = datetime.now().strftime("%Y-%m-%d")

    message = f"🎵 UMUSIC DAILY SUMMARY ({today})\n\n"
    message += "\n\n".join(sections)

    return message


def send_telegram(message):

    if not message:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    requests.post(url, data=payload)


def main():

    current_products = fetch_products()

    previous_products = load_previous_snapshot()

    changes = compare_snapshots(previous_products, current_products)

    message = build_message(changes)

    send_telegram(message)

    save_snapshot(current_products)


if __name__ == "__main__":
    main()
