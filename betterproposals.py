"""
Better Proposals API client.
Fetches proposal data (products, prices, taxes) for syncing to Pipedrive.
"""
import re
import html
from decimal import Decimal, ROUND_HALF_UP
import requests
from config import BP_API_KEY, BP_BASE
from pipedrive import pd_replace_deal_products, pd_add_note_to_deal


def bp_headers():
    """Return authentication headers for Better Proposals API."""
    return {
        "Bptoken": BP_API_KEY,
        "Content-Type": "application/json"
    }


def bp_get_proposal(proposal_id: str) -> dict:
    """Fetch full proposal details including line items."""
    r = requests.get(
        f"{BP_BASE}/proposal/{proposal_id}",
        headers=bp_headers(),
        timeout=30
    )
    if not r.ok:
        print(f"BP ERROR: {r.status_code} - {r.text}")
    r.raise_for_status()
    result = r.json()
    if result.get("status") == "error":
        raise Exception(f"BP API error: {result}")
    return result.get("data", {})


def bp_get_signed_proposals() -> list:
    """Fetch all signed proposals."""
    r = requests.get(
        f"{BP_BASE}/proposal/signed",
        headers=bp_headers(),
        timeout=30
    )
    r.raise_for_status()
    result = r.json()
    return result.get("data", [])


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


RECURRING_LABELS = {
    "One Time Payment": "einmalig",
    "Monthly Payment": "/Monat",
    "Quarterly Payment": "/Quartal",
    "Annual Payment": "/Jahr",
}


def bp_parse_line_items(proposal: dict) -> tuple[list[dict], list[dict]]:
    """
    Parse PriceTables from BP proposal.
    Each PriceTable block becomes ONE product (not individual line items).

    Returns (included, excluded):
      - included: PriceTable blocks with summed prices → will be added to deal
      - excluded: unselected optional items → listed in note only
    """
    currency = proposal.get("CurrencyCode", "EUR")
    # Tax vorerst deaktiviert - erst Netto-Preise korrekt, dann Tax separat
    tax_pct = 0

    included = []
    excluded = []

    for table in proposal.get("PriceTables", []):
        table_title = _strip_html(table.get("Title", ""))
        if not table_title:
            continue

        table_total = Decimal("0")
        table_items = []  # for note detail
        table_excluded = []

        for item in table.get("Items", []):
            is_optional = item.get("Optional", False)
            is_selected = item.get("Selected", False)
            raw_price = str(item.get("UnitCost", "0"))
            item_price = Decimal(raw_price)
            item_qty = int(item.get("Quantity", 1))
            item_name = _strip_html(item.get("Label", ""))
            raw_discount = str(item.get("DiscountAmount", "0"))
            item_discount = Decimal(raw_discount)

            if not is_optional or is_selected:
                # Round each line total to avoid BP's division artifacts
                # (BP divides totals by qty, causing e.g. 1328.57*7=9299.99 instead of 9300)
                line_total = ((item_price - item_discount) * item_qty).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                table_total += line_total
                table_items.append({
                    "name": item_name,
                    "price": float(item_price),
                    "quantity": item_qty,
                    "discount": float(item_discount),
                    "optional": is_optional,
                    "recurring_type": item.get("RecurringType", ""),
                })
            else:
                table_excluded.append({
                    "name": item_name,
                    "price": float(item_price),
                    "recurring_type": item.get("RecurringType", ""),
                })

        if table_items:
            # Table total is already rounded per line item, convert to float
            final_price = float(table_total)
            included.append({
                "name": table_title,
                "price": final_price,
                "quantity": 1,
                "currency": currency,
                "tax": tax_pct,
                "discount": 0,
                "items": table_items,  # sub-items for note
            })

        excluded.extend(table_excluded)

    return included, excluded


def _format_price(price: float, currency: str) -> str:
    """Format price with currency symbol."""
    if currency == "EUR":
        return f"\u20ac{price:,.2f}"
    return f"{price:,.2f} {currency}"


def _build_note(event_type: str, included: list, excluded: list, currency: str) -> str:
    """Build a deal note with product summary."""
    event_labels = {
        "sent": "gesendet",
        "updated": "aktualisiert",
        "signed": "signiert",
    }
    event_label = event_labels.get(event_type, event_type or "sync")

    lines = [f"Better Proposals \u2014 Angebot {event_label}", ""]

    total = 0
    for block in included:
        price_str = _format_price(block["price"], currency)
        lines.append(f"{block['name']} \u2014 {price_str}")
        total += block["price"]

        # List sub-items for detail
        for item in block.get("items", []):
            recurring = RECURRING_LABELS.get(item.get("recurring_type", ""), "")
            item_price = _format_price(item["price"], currency)
            opt_marker = " (optional)" if item.get("optional") else ""
            lines.append(f"    {item['name']} \u2014 {item.get('quantity', 1)}x {item_price}{recurring}{opt_marker}")
        lines.append("")

    lines.append(f"Gesamt (netto): {_format_price(total, currency)}")

    if excluded:
        lines.append("\nOptionale (nicht gewaehlt):")
        for p in excluded:
            recurring = RECURRING_LABELS.get(p.get("recurring_type", ""), "")
            price_str = _format_price(p["price"], currency)
            lines.append(f"    {p['name']} \u2014 {price_str}{recurring}")

    return "\n".join(lines)


def bp_sync_products_to_deal(proposal_id: str, deal_id: int, event_type: str = None):
    """
    Main sync function:
    1. Fetch BP proposal
    2. Parse line items
    3. Replace deal products in Pipedrive
    4. Add note with history
    """
    print(f"BP SYNC: Starting sync for proposal {proposal_id} -> deal {deal_id} (event: {event_type})")

    # 1. Fetch proposal
    proposal = bp_get_proposal(proposal_id)
    currency = proposal.get("CurrencyCode", "EUR")

    # 2. Parse line items
    included, excluded = bp_parse_line_items(proposal)

    if not included:
        print(f"BP SYNC: No products found in proposal {proposal_id}")
        pd_add_note_to_deal(deal_id, f"Better Proposals \u2014 Angebot {event_type}: Keine Produkte gefunden.")
        return

    print(f"BP SYNC: Found {len(included)} products (+{len(excluded)} optional not selected)")

    # 3. Replace deal products
    products_for_pd = [
        {
            "name": p["name"],
            "price": p["price"],
            "quantity": p["quantity"],
            "currency": p["currency"],
            "discount": p["discount"],
            "tax": p["tax"],
        }
        for p in included
    ]
    pd_replace_deal_products(deal_id, products_for_pd)

    # 4. Add note
    note = _build_note(event_type, included, excluded, currency)
    pd_add_note_to_deal(deal_id, note)

    print(f"BP SYNC: Completed sync for proposal {proposal_id} -> deal {deal_id}")
