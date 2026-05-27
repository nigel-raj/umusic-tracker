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

                variant_title = (
                    variant["title"]
                    if variant["title"] != "Default Title"
                    else None
                )

                key = str(variant["id"])

                products[key] = {

                    # PRODUCT
                    "product_id": product["id"],
                    "title": product["title"],
                    "handle": product["handle"],
                    "url": f"https://umusic.my/products/{product['handle']}",

                    # ARTIST / TYPE
                    "vendor": product.get("vendor"),
                    "product_type": product.get("product_type"),

                    # DATES
                    "created_at": product.get("created_at"),
                    "published_at": product.get("published_at"),
                    "updated_at": product.get("updated_at"),

                    # TAGS
                    "tags": product.get("tags", []),

                    # VARIANT
                    "variant_title": variant_title,
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

    print(f"Total tracked variants: {len(products)}")

    return products


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

    history_path = f"{HISTORY_FOLDER}/{today}.json"

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)


def compare_snapshots(old, new):

    changes = {
        "new_products": [],
        "removed_products": [],
        "price_changes": [],
        "restocked": [],
        "sold_out": []
    }

    old_keys = set(old.keys())
    new_keys = set(new.keys())

    # NEW PRODUCTS
    for key in new_keys - old_keys:

        item = new[key]

        changes["new_products"].append(item)

    # REMOVED PRODUCTS
    for key in old_keys - new_keys:

        item = old[key]

        changes["removed_products"].append(item)

    # EXISTING PRODUCTS
    for key in old_keys & new_keys:

        old_item = old[key]
        new_item = new[key]

        # PRICE CHANGES
        if old_item["price"] != new_item["price"]:

            changes["price_changes"].append({
                "title": new_item["title"],
                "variant_title": new_item["variant_title"],
                "old_price": old_item["price"],
                "new_price": new_item["price"]
            })

        # RESTOCKED
        if not old_item["available"] and new_item["available"]:

            changes["restocked"].append(new_item)

        # SOLD OUT
        if old_item["available"] and not new_item["available"]:

            changes["sold_out"].append(new_item)

    return changes


def build_metrics(products):

    total_products = len(products)

    available_products = sum(
        1 for p in products.values() if p["available"]
    )

    sold_out_products = total_products - available_products

    product_types = Counter(
        p.get("product_type", "Unknown")
        for p in products.values()
    )

    return {
        "total_products": total_products,
        "available_products": available_products,
        "sold_out_products": sold_out_products,
        "product_types": product_types
    }


def format_product(item):

    variant = (
        f" ({item['variant_title']})"
        if item.get("variant_title")
        else ""
    )

    return (
        f"• *{item['vendor']}*\n"
        f"  [{item['title']}]({item['url']}){variant}\n"
        f"  `{item['product_type']}` • RM{item['price']}"
    )


def build_message(changes, metrics):

    sections = []

    # METRICS
    metrics_section = (
        "📊 *STORE METRICS*\n"
        f"• Total Products: {metrics['total_products']}\n"
        f"• Available: {metrics['available_products']}\n"
        f"• Sold Out: {metrics['sold_out_products']}\n\n"
        "*Formats*\n"
    )

    for product_type, count in metrics["product_types"].most_common():

        metrics_section += f"• {product_type}: {count}\n"

    sections.append(metrics_section)

    # NEW PRODUCTS
    if changes["new_products"]:

        text = "🆕 *NEW PRODUCTS*\n\n"

        text += "\n\n".join(
            format_product(item)
            for item in changes["new_products"][:10]
        )

        sections.append(text)

    # REMOVED PRODUCTS
    if changes["removed_products"]:

        text = "❌ *REMOVED PRODUCTS*\n\n"

        text += "\n".join(
            f"• {item['title']}"
            for item in changes["removed_products"][:10]
        )

        sections.append(text)

    # PRICE CHANGES
    if changes["price_changes"]:

        text = "💰 *PRICE CHANGES*\n\n"

        for item in changes["price_changes"][:15]:

            variant = (
                f" ({item['variant_title']})"
                if item["variant_title"]
                else ""
            )

            text += (
                f"• *{item['title']}*{variant}\n"
                f"  RM{item['old_price']} → RM{item['new_price']}\n\n"
            )

        sections.append(text)

    # RESTOCKED
    if changes["restocked"]:

        text = "📦 *RESTOCKED*\n\n"

        text += "\n".join(
            f"• {item['title']}"
            for item in changes["restocked"][:15]
        )

        sections.append(text)

    # SOLD OUT
    if changes["sold_out"]:

        text = "🚫 *SOLD OUT*\n\n"

        text += "\n".join(
            f"• {item['title']}"
            for item in changes["sold_out"][:15]
        )

        sections.append(text)

    today = datetime.now().strftime("%Y-%m-%d")

    message = (
        f"🎵 *UMUSIC DAILY SUMMARY*\n"
        f"_{today}_\n\n"
    )

    message += "\n━━━━━━━━━━━━━━\n\n".join(sections)

    return message


def send_telegram(message):

    if not message:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    requests.post(url, data=payload)


def main():

    current_products = fetch_products()

    previous_products = load_previous_snapshot()

    changes = compare_snapshots(
        previous_products,
        current_products
    )

    metrics = build_metrics(current_products)

    message = build_message(
        changes,
        metrics
    )

    send_telegram(message)

    save_snapshot(current_products)


if __name__ == "__main__":
    main()
