"""
Better Proposals API client.
Fetches proposal data (products, prices, taxes) for syncing to Pipedrive.
"""
import re
import html
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import requests
from config import BP_API_KEY, BP_API_KEYS, BP_BASE
from pipedrive import pd_replace_deal_products, pd_add_note_to_deal, pd_put


def bp_headers(api_key: str = None):
    """Return authentication headers for Better Proposals API."""
    return {
        "Bptoken": api_key or BP_API_KEY,
        "Content-Type": "application/json"
    }


def bp_get_proposal(proposal_id: str) -> dict:
    """Fetch full proposal details including line items.
    Accepts either a ProposalID or a QuoteID (from BP URLs).
    """
    # Try direct proposal lookup first
    r = requests.get(
        f"{BP_BASE}/proposal/{proposal_id}",
        headers=bp_headers(),
        timeout=30
    )
    if r.ok:
        result = r.json()
        if result.get("status") != "error":
            print(f"BP: Found proposal directly with ID {proposal_id}")
            return result.get("data", {})

    # ID not found as ProposalID — try as QuoteID
    print(f"BP: ID {proposal_id} not found as ProposalID, searching as QuoteID...")
    for endpoint in ["/proposal/sent", "/proposal/signed", "/proposal/draft"]:
        try:
            r2 = requests.get(f"{BP_BASE}{endpoint}", headers=bp_headers(), timeout=30)
            if not r2.ok:
                continue
            proposals = r2.json().get("data", [])
            for p in proposals:
                if str(p.get("QuoteID")) == str(proposal_id):
                    real_id = p.get("ID")
                    print(f"BP: Found QuoteID {proposal_id} → ProposalID {real_id} (via {endpoint})")
                    # Fetch full proposal data with the real ID
                    r3 = requests.get(
                        f"{BP_BASE}/proposal/{real_id}",
                        headers=bp_headers(),
                        timeout=30
                    )
                    r3.raise_for_status()
                    return r3.json().get("data", {})
        except Exception as e:
            print(f"BP: Error searching {endpoint}: {e}")

    raise Exception(f"BP: Proposal not found with ID or QuoteID {proposal_id}")


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


def bp_find_proposal_by_preview_hash(preview_hash: str) -> str | None:
    """Find a BP ProposalID by matching the Preview URL hash.

    Searches /proposal/sent and /proposal/signed across ALL configured
    BP API keys. Each key sees a different user's proposals.

    Args:
        preview_hash: The hash from the BP preview URL (ProposalID= parameter)

    Returns:
        The numeric ProposalID string, or None if not found.
    """
    target_fragment = f"ProposalID={preview_hash}"

    for key_idx, api_key in enumerate(BP_API_KEYS):
        for endpoint in ["/proposal/sent", "/proposal/signed"]:
            try:
                r = requests.get(
                    f"{BP_BASE}{endpoint}",
                    headers=bp_headers(api_key),
                    timeout=30
                )
                if not r.ok:
                    continue
                proposals = r.json().get("data", [])
                for p in proposals:
                    preview_url = p.get("Preview", "")
                    if target_fragment in preview_url:
                        proposal_id = p.get("ID")
                        print(f"BP HASH: Found proposal {proposal_id} "
                              f"(key #{key_idx + 1}, {endpoint})")
                        return proposal_id
            except Exception as e:
                print(f"BP HASH: Error searching {endpoint} with key #{key_idx + 1}: {e}")

    print(f"BP HASH: No proposal found for hash {preview_hash[:20]}... "
          f"(searched {len(BP_API_KEYS)} key(s))")
    return None


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


RECURRING_LABELS = {
    "One Time Payment": "one-time",
    "Monthly Payment": "/month",
    "Quarterly Payment": "/quarter",
    "Annual Payment": "/year",
}


BP_TO_PD_BILLING = {
    "One Time Payment": "one-time",
    "Monthly Payment": "monthly",
    "Quarterly Payment": "quarterly",
    "Annual Payment": "annually",
}


def _map_billing_frequency(recurring_types: list[str]) -> str | None:
    """Determine billing frequency from list of RecurringTypes.
    Uses most common type. Returns Pipedrive billing_frequency value."""
    if not recurring_types:
        return None
    counts = Counter(recurring_types)
    most_common = counts.most_common(1)[0][0]
    return BP_TO_PD_BILLING.get(most_common)


def bp_parse_line_items(proposal: dict) -> tuple[list[dict], list[dict]]:
    """
    Parse PriceTables from BP proposal.
    Each PriceTable block becomes ONE product (not individual line items).

    Returns (included, excluded):
      - included: PriceTable blocks with summed prices → will be added to deal
      - excluded: unselected optional items → listed in note only
    """
    currency = proposal.get("CurrencyCode", "EUR")
    tax_pct = int(float(proposal.get("TaxAmount", 0)))

    included = []
    excluded = []

    for table in proposal.get("PriceTables", []):
        table_title = _strip_html(table.get("Title", ""))
        if not table_title:
            continue

        table_gross = Decimal("0")   # sum of Cost (pre-discount gross)
        table_items = []
        table_excluded = []
        recurring_types = []
        discount_pcts = []  # collect active discount percentages

        for item in table.get("Items", []):
            is_optional = item.get("Optional", False)
            is_selected = item.get("Selected", False)
            item_name = _strip_html(item.get("Label", ""))
            item_qty = int(item.get("Quantity", 1))
            item_price = Decimal(str(item.get("UnitCost", "0")))
            # Cost = pre-calculated total (UnitCost is already discounted)
            line_cost = Decimal(str(item.get("Cost", "0")))

            # Track discount percentage if active
            has_discount = item.get("Discount", False)
            if has_discount:
                discount_pcts.append(float(item.get("DiscountAmount", 0)))

            if not is_optional or is_selected:
                table_gross += line_cost
                recurring_types.append(item.get("RecurringType", ""))
                table_items.append({
                    "name": item_name,
                    "price": float(item_price),
                    "quantity": item_qty,
                    "cost": float(line_cost),
                    "optional": is_optional,
                    "recurring_type": item.get("RecurringType", ""),
                })
            else:
                table_excluded.append({
                    "name": item_name,
                    "price": float(line_cost),
                    "recurring_type": item.get("RecurringType", ""),
                })

        if table_items:
            # Round to nearest 0.50 (BP rounds to 50 cents)
            def _round_50(val):
                return (val * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2

            gross_rounded = float(_round_50(table_gross))

            # Cost = pre-discount gross price
            # Discount percentage is applied on top by Pipedrive
            if discount_pcts:
                discount_pct = max(set(discount_pcts), key=discount_pcts.count)
                net_price = round(gross_rounded * (1 - discount_pct / 100), 2)
            else:
                discount_pct = 0
                net_price = gross_rounded

            # Determine billing frequency from most common RecurringType
            billing_freq = _map_billing_frequency(recurring_types)

            included.append({
                "name": table_title,
                "price": gross_rounded,         # pre-discount price (Cost sum)
                "quantity": 1,
                "currency": currency,
                "tax": tax_pct,
                "discount": discount_pct,       # discount as percentage
                "discount_type": "percentage",
                "billing_frequency": billing_freq,
                "net_price": net_price,          # for note display
                "items": table_items,
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
    event_label = event_type or "sync"

    lines = [f"Better Proposals \u2014 Proposal {event_label}", ""]

    total_net = 0
    for block in included:
        discount_pct = block.get("discount", 0)
        net = block.get("net_price", block["price"])
        billing = block.get("billing_frequency", "")

        net_str = _format_price(net, currency)
        if discount_pct:
            lines.append(f"{block['name']} \u2014 {net_str} ({discount_pct:.0f}% discount)")
        else:
            lines.append(f"{block['name']} \u2014 {net_str}")

        if billing and billing != "one-time":
            billing_label = {"monthly": "monthly", "quarterly": "quarterly", "annually": "annually"}.get(billing, "")
            lines.append(f"    Billing: {billing_label}")

        total_net += net

        # List sub-items for detail
        for item in block.get("items", []):
            recurring = RECURRING_LABELS.get(item.get("recurring_type", ""), "")
            cost_str = _format_price(item.get("cost", item["price"]), currency)
            opt_marker = " (optional)" if item.get("optional") else ""
            lines.append(f"    {item['name']} \u2014 {cost_str}{recurring}{opt_marker}")
        lines.append("")

    lines.append(f"Total (net): {_format_price(total_net, currency)}")

    if excluded:
        lines.append("\nOptional (not selected):")
        for p in excluded:
            recurring = RECURRING_LABELS.get(p.get("recurring_type", ""), "")
            price_str = _format_price(p["price"], currency)
            lines.append(f"    {p['name']} \u2014 {price_str}{recurring}")

    return "\n".join(lines)


def bp_get_onboarding_data(proposal_id: str, api_key: str = None) -> list | dict | None:
    """Fetch onboarding form responses for a signed proposal."""
    headers = bp_headers(api_key)
    for endpoint in [f"/proposal/{proposal_id}/onboarding", f"/onboarding/{proposal_id}"]:
        try:
            r = requests.get(f"{BP_BASE}{endpoint}", headers=headers, timeout=30)
            if r.ok:
                data = r.json()
                if data.get("status") != "error" and data.get("data"):
                    print(f"BP ONBOARDING: Found data via {endpoint}")
                    return data.get("data")
        except Exception:
            pass

    # Fall back to proposal response fields
    try:
        proposal = bp_get_proposal(proposal_id)
        for field in ["OnboardingData", "Onboarding", "FormData", "QuestionsAndAnswers", "ClientFields"]:
            if proposal.get(field):
                return proposal.get(field)
    except Exception:
        pass
    return None


def bp_format_onboarding_note(onboarding_data) -> str:
    """Format BP onboarding responses as a readable Pipedrive note."""
    lines = ["📋 Onboarding-Informationen (Proposal signiert)", ""]

    if isinstance(onboarding_data, list):
        for section in onboarding_data:
            if not isinstance(section, dict):
                continue
            title = section.get("Title") or section.get("title") or section.get("name") or ""
            if title:
                lines.append(f"▸ {title}")
            fields = (section.get("Fields") or section.get("fields")
                      or section.get("answers") or section.get("Questions") or [])
            for field in fields:
                if isinstance(field, dict):
                    label = (field.get("Label") or field.get("label")
                             or field.get("question") or field.get("Name") or "")
                    value = (field.get("Value") or field.get("value")
                             or field.get("answer") or field.get("Answer") or "—")
                    if label:
                        lines.append(f"  • {label}: {value}")
            lines.append("")
    elif isinstance(onboarding_data, dict):
        for key, value in onboarding_data.items():
            if value and not key.startswith("_"):
                lines.append(f"• {key}: {value}")

    return "\n".join(lines) if len(lines) > 2 else ""


def bp_sync_signed(proposal_id: str, deal_id: int):
    """
    Handle a signed proposal:
    1. Fetch onboarding form responses → add as note to Pipedrive deal
    2. Try to attach PDF to Pipedrive deal
    """
    from pipedrive import pd_upload_file_to_deal

    print(f"BP SIGNED: Processing signed proposal {proposal_id} for deal {deal_id}")

    # Fetch proposal (try all API keys)
    proposal = None
    api_key_used = None
    for key in BP_API_KEYS:
        try:
            r = requests.get(f"{BP_BASE}/proposal/{proposal_id}",
                             headers=bp_headers(key), timeout=30)
            if r.ok:
                data = r.json()
                if data.get("status") != "error" and data.get("data"):
                    proposal = data.get("data", {})
                    api_key_used = key
                    break
        except Exception:
            pass
    if not proposal:
        try:
            proposal = bp_get_proposal(proposal_id)
        except Exception as e:
            print(f"BP SIGNED: Could not fetch proposal {proposal_id}: {e}")
            return

    # 1. Update Pipedrive deal value from proposal total
    try:
        included, _ = bp_parse_line_items(proposal)
        total_net = sum(p.get("net_price", p["price"]) for p in included)
        if total_net:
            pd_put(f"/deals/{deal_id}", {"value": round(total_net, 2)})
            print(f"BP SIGNED: Updated deal {deal_id} value to {total_net:.2f}")
    except Exception as e:
        print(f"BP SIGNED: Could not update deal value: {e}")

    # 2. Onboarding note
    onboarding_data = bp_get_onboarding_data(proposal_id, api_key=api_key_used)
    if onboarding_data:
        note = bp_format_onboarding_note(onboarding_data)
        if note:
            pd_add_note_to_deal(deal_id, note)
            print(f"BP SIGNED: Added onboarding note to deal {deal_id}")
        else:
            pd_add_note_to_deal(deal_id, f"📋 Proposal signiert. Onboarding-Rohdaten:\n{str(onboarding_data)[:1000]}")
    else:
        signed_name = proposal.get("SignedName", "—")
        signed_date = proposal.get("SignedDate", "—")
        signed_time = proposal.get("SignedTime", "")
        note = (
            f"✅ Proposal signiert\n\n"
            f"• Unterzeichnet von: {signed_name}\n"
            f"• Datum: {signed_date} {signed_time}\n\n"
            f"Onboarding-Daten folgen sobald die BP API diese bereitstellt."
        )
        pd_add_note_to_deal(deal_id, note)
        print(f"BP SIGNED: Added basic signed note to deal {deal_id}")

    # 2. PDF attachment
    pdf_url = None
    for field in ["PdfUrl", "PDF", "DownloadUrl", "DocumentUrl", "FileUrl", "SignedPdfUrl"]:
        url = proposal.get(field)
        if url and str(url).startswith("http"):
            pdf_url = url
            break

    if pdf_url:
        try:
            r = requests.get(pdf_url, timeout=60)
            if r.ok:
                filename = f"Proposal_{proposal_id}_signed.pdf"
                pd_upload_file_to_deal(deal_id, filename, r.content)
                print(f"BP SIGNED: Attached PDF to deal {deal_id}")
        except Exception as e:
            print(f"BP SIGNED: Could not attach PDF: {e}")
    else:
        print(f"BP SIGNED: No PDF URL in proposal response (fields: {list(proposal.keys())})")


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
        pd_add_note_to_deal(deal_id, f"Better Proposals \u2014 Proposal {event_type}: No products found.")
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
            "discount_type": p.get("discount_type", "amount"),
            "tax": p["tax"],
            "billing_frequency": p.get("billing_frequency"),
        }
        for p in included
    ]
    pd_replace_deal_products(deal_id, products_for_pd)

    # 4. Add note
    note = _build_note(event_type, included, excluded, currency)
    pd_add_note_to_deal(deal_id, note)

    print(f"BP SYNC: Completed sync for proposal {proposal_id} -> deal {deal_id}")
