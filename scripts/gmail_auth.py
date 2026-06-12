"""One-time interactive Gmail OAuth — run via `make gmail-auth`.
Opens a browser, saves token.json; after this, jobs and the dashboard send
without ever prompting."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outreach.gmail_client import GmailClient  # noqa: E402


def main() -> None:
    client = GmailClient(interactive=True)
    address = client.my_address()
    print(f"Gmail authorized as {address}; token saved.")


if __name__ == "__main__":
    main()
