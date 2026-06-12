"""
Batch enrichment script: reads Excel, POSTs to /admin/batch-enrich, polls for results.
Usage:
    python enrich_batch.py companies.xlsx
    python enrich_batch.py companies.xlsx --results BATCH_ID
"""
import sys
import time
import json
import argparse
import requests
import pandas as pd

# ---- CONFIGURE THESE ----
SERVER_URL = "https://sync.audit-professionals.de"
TOKEN = "bpWebhookToken_sicher_random_abc123"
# -------------------------

def start_batch(excel_path: str, limit: int = 0, offset: int = 0) -> str:
    df = pd.read_excel(excel_path)
    companies = df.to_dict(orient="records")
    # Remove NaN values (pandas fills missing cells with NaN)
    cleaned = [{k: (str(v) if pd.notna(v) else "") for k, v in row.items()} for row in companies]

    if offset:
        cleaned = cleaned[offset:]
    total = len(cleaned)

    params = {"token": TOKEN}
    if limit:
        params["limit"] = limit

    print(f"Sending {min(limit, total) if limit else total} of {len(df)} companies to server (offset={offset})...")
    r = requests.post(
        f"{SERVER_URL}/admin/batch-enrich",
        params=params,
        json=cleaned,
        timeout=30
    )
    r.raise_for_status()
    data = r.json()
    batch_id = data["batch_id"]
    print(f"\nBatch started! ID: {batch_id}")
    print(f"Companies queued: {data['queued']}")
    print(f"\nPoll for results with:")
    print(f"  python enrich_batch.py {excel_path} --results {batch_id}")
    return batch_id


def poll_results(batch_id: str, output_file: str = None):
    print(f"Fetching results for batch {batch_id}...")
    r = requests.get(
        f"{SERVER_URL}/admin/batch-results/{batch_id}",
        params={"token": TOKEN},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()

    total = data["total"]
    completed = data["completed"]
    pending = data["pending"]
    print(f"\nProgress: {completed}/{total} completed, {pending} still pending")

    results = data["results"]
    if not results:
        print("No results yet.")
        return

    # Save to Excel
    if not output_file:
        output_file = f"enriched_{batch_id}.xlsx"
    df = pd.DataFrame(results)
    df.to_excel(output_file, index=False)
    print(f"\nResults saved to: {output_file}")

    # Print summary
    with_email = sum(1 for r in results if r.get("contact_email"))
    print(f"Contacts with email: {with_email}/{completed}")

    if pending > 0:
        print(f"\n{pending} results still pending — run again in a few minutes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("excel", help="Path to Excel file")
    parser.add_argument("--results", help="Batch ID to fetch results for")
    parser.add_argument("--output", help="Output Excel filename", default=None)
    parser.add_argument("--limit", type=int, default=0, help="Max companies per batch (e.g. 50)")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N companies")
    args = parser.parse_args()

    if args.results:
        poll_results(args.results, args.output)
    else:
        start_batch(args.excel, limit=args.limit, offset=args.offset)
