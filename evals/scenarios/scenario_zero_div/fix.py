def calculate_metrics(total, count):
    if count == 0:
        return 0
    return total / count


if __name__ == "__main__":
    print(calculate_metrics(100, 0))
