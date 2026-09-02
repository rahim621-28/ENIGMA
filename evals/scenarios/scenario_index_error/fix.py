def most_recent_event(events):
    if not events:
        return None
    return events[-1]


if __name__ == "__main__":
    print(most_recent_event([]))
