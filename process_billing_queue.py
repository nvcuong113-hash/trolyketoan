from billing_api import process_pending_events

if __name__ == "__main__":
    process_pending_events(limit=100)
