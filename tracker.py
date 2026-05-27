import requests
import json
import os
from datetime import datetime

SHOP_URL = "https://umusic.my/collections/music/products.json?limit=250"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SNAPSHOT_FILE = "snapshots/latest.json"


def fetch_products():

    page = 1
    products = {}

    while True:

        url = f"https://umusic.my/collections/music/products.json?limit=250&page={page}"

        response = requests.get(url)
        data = response.json()

        batch = data.get("products", [])

        if not batch:
            break

        for product in batch:

            for variant in product["variants"]:

                key = str(variant["id"])

                products[key] = {

                    # PRODUCT INFO
                    "product_id": product["id"],
                    "title": product["title"],
                    "handle": product["handle"],
                    "vendor": product.get("vendor"),
                    "product_type": product.get("product_type"),

                    # IMPORTANT DATES
                    "created_at": product.get("created_at"),
                    "published_at": product.get("published_at"),
                    "updated_at": product.get("updated_at"),

                    # TAGS
                    "tags": product.get("tags", []),

                    # VARIANT INFO
                    "variant_title": variant["title"],
                    "sku": variant.get("sku"),

                    # PRICING
                    "price": variant["price"],
                    "compare_at_price": variant.get("compare_at_price"),

                    # STOCK
                    "available": variant["available"],

                    # IMAGE
                    "image": (
                        product["images"][0]["src"]
                        if product.get("images")
                        else None
                    )
                }

        print(f"Fetched page {page} ({len(batch)} products)")

        page += 1

    print(f"Total variants tracked: {len(products)}")

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
