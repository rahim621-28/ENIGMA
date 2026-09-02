def total_price(items):
    return sum(item["price"] for item in items)


if __name__ == "__main__":
    print(total_price([{"price": 10}, {"price": "5"}]))
