"""
Pipedrive API functions.
"""
import requests
from config import PIPEDRIVE_BASE, PIPEDRIVE_TOKEN, GERMANY_USER_IDS


def pd_get(path: str):
    """GET request to Pipedrive API."""
    r = requests.get(
        f"{PIPEDRIVE_BASE}{path}",
        params={"api_token": PIPEDRIVE_TOKEN},
        timeout=30
    )
    r.raise_for_status()
    js = r.json()
    if not js.get("success"):
        raise RuntimeError(js)
    return js["data"]


def pd_post(path: str, data: dict):
    """POST request to Pipedrive API."""
    r = requests.post(
        f"{PIPEDRIVE_BASE}{path}",
        params={"api_token": PIPEDRIVE_TOKEN},
        json=data,
        timeout=30
    )
    r.raise_for_status()
    js = r.json()
    if not js.get("success"):
        raise RuntimeError(js)
    return js["data"]


def pd_put(path: str, data: dict):
    """PUT request to Pipedrive API."""
    r = requests.put(
        f"{PIPEDRIVE_BASE}{path}",
        params={"api_token": PIPEDRIVE_TOKEN},
        json=data,
        timeout=30
    )
    r.raise_for_status()
    js = r.json()
    if not js.get("success"):
        raise RuntimeError(js)
    return js["data"]


def pd_val(field):
    """Extract value from Pipedrive field (handles dict with 'value' key)."""
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def pd_owner_id(obj: dict):
    """
    Extract owner ID from Pipedrive object.
    Handles both owner_id and user_id fields.
    """
    if not isinstance(obj, dict):
        return None

    owner = obj.get("owner_id")
    if isinstance(owner, dict):
        return owner.get("id")
    if isinstance(owner, int):
        return owner

    owner = obj.get("user_id")
    if isinstance(owner, dict):
        return owner.get("id")
    if isinstance(owner, int):
        return owner

    return None


def owner_allowed(owner_id: int | None) -> bool:
    """Check if owner is in Germany team (or all allowed if no filter)."""
    if not GERMANY_USER_IDS:
        return True
    if owner_id is None:
        return False
    return int(owner_id) in GERMANY_USER_IDS


# ---- Person Operations ----
def pd_create_person(name: str, org_id: int = None, email: str = None,
                     phone: str = None, owner_id: int = None,
                     job_title: str = None) -> dict:
    """Create a new person in Pipedrive."""
    data = {"name": name}

    if org_id:
        data["org_id"] = org_id
    if email:
        data["email"] = [{"value": email, "primary": True, "label": "work"}]
    if phone:
        data["phone"] = [{"value": phone, "primary": True, "label": "mobile"}]
    if owner_id:
        data["owner_id"] = owner_id
    if job_title:
        data["job_title"] = job_title

    return pd_post("/persons", data)


def pd_update_person(person_id: int, email: str = None,
                     phone: str = None, job_title: str = None) -> dict | None:
    """Update an existing person in Pipedrive."""
    data = {}

    if email:
        data["email"] = [{"value": email, "primary": True, "label": "work"}]
    if phone:
        data["phone"] = [{"value": phone, "primary": True, "label": "mobile"}]
    if job_title:
        data["job_title"] = job_title

    if data:
        return pd_put(f"/persons/{person_id}", data)
    return None


# ---- Deal Operations ----
def pd_link_person_to_deal(deal_id: int, person_id: int) -> dict:
    """Link a person to a deal in Pipedrive."""
    return pd_put(f"/deals/{deal_id}", {"person_id": person_id})


# ---- Organization Operations ----
def pd_update_org(org_id: int, website: str = None) -> dict | None:
    """Update an organization in Pipedrive (e.g., set website)."""
    data = {}
    if website:
        if not website.startswith("http"):
            website = f"https://{website}"
        data["website"] = website

    if data:
        return pd_put(f"/organizations/{org_id}", data)
    return None


# ---- Product Operations ----
def pd_search_product(name: str, exact: bool = True) -> dict | None:
    """Search for a product by name. Returns first match or None."""
    r = requests.get(
        f"{PIPEDRIVE_BASE}/products/search",
        params={"api_token": PIPEDRIVE_TOKEN, "term": name, "exact_match": exact},
        timeout=30
    )
    r.raise_for_status()
    js = r.json()
    items = js.get("data", {}).get("items", [])
    if not items:
        return None

    if exact:
        return items[0].get("item")

    # Fuzzy: pick best match (case-insensitive name comparison)
    name_lower = name.lower().strip()
    for item in items:
        product = item.get("item", {})
        if product.get("name", "").lower().strip() == name_lower:
            return product
    # No exact case-insensitive match, return first result
    return items[0].get("item")


def pd_create_product(name: str, price: float, currency: str = "EUR") -> dict:
    """Create a new product in Pipedrive."""
    return pd_post("/products", {
        "name": name,
        "prices": [{"price": price, "currency": currency}]
    })


def pd_find_or_create_product(name: str, price: float, currency: str = "EUR") -> dict:
    """
    Find existing product by name or create a new one.
    Strategy: exact match → fuzzy search → create new.
    """
    # 1. Try exact match
    existing = pd_search_product(name, exact=True)
    if existing:
        print(f"PD PRODUCT: Found exact match '{name}' (id={existing['id']})")
        return existing

    # 2. Try fuzzy search (handles minor differences like case, whitespace)
    fuzzy = pd_search_product(name, exact=False)
    if fuzzy:
        print(f"PD PRODUCT: Found fuzzy match '{fuzzy.get('name')}' for '{name}' (id={fuzzy['id']})")
        return fuzzy

    # 3. Create new
    print(f"PD PRODUCT: No match found, creating '{name}' (price={price} {currency})")
    return pd_create_product(name, price, currency)


def pd_get_deal_products(deal_id: int) -> list:
    """Get all products attached to a deal."""
    r = requests.get(
        f"{PIPEDRIVE_BASE}/deals/{deal_id}/products",
        params={"api_token": PIPEDRIVE_TOKEN},
        timeout=30
    )
    r.raise_for_status()
    js = r.json()
    if not js.get("success"):
        return []
    return js.get("data", []) or []


def pd_delete_deal_product(deal_id: int, deal_product_id: int):
    """Remove a product from a deal."""
    r = requests.delete(
        f"{PIPEDRIVE_BASE}/deals/{deal_id}/products/{deal_product_id}",
        params={"api_token": PIPEDRIVE_TOKEN},
        timeout=30
    )
    r.raise_for_status()
    return r.json()


def pd_add_product_to_deal(deal_id: int, product_id: int, item_price: float,
                           quantity: int = 1, discount: float = 0,
                           tax: float = 0) -> dict:
    """Add a product to a deal."""
    data = {
        "product_id": product_id,
        "item_price": item_price,
        "quantity": quantity,
    }
    if discount:
        data["discount"] = discount
        data["discount_type"] = "percentage"
    if tax:
        data["tax"] = tax
    return pd_post(f"/deals/{deal_id}/products", data)


def pd_replace_deal_products(deal_id: int, products: list[dict]) -> list:
    """
    Replace all products on a deal.
    Deletes existing products, then adds new ones.

    products: list of dicts with keys: name, price, quantity, currency, discount, tax
    Returns list of added deal-product entries.
    """
    # Delete existing
    existing = pd_get_deal_products(deal_id)
    for p in existing:
        dp_id = p.get("id")
        if dp_id:
            pd_delete_deal_product(deal_id, dp_id)
            print(f"PD PRODUCT: Removed '{p.get('name')}' from deal {deal_id}")

    # Add new
    added = []
    for p in products:
        product = pd_find_or_create_product(
            name=p["name"],
            price=p.get("price", 0),
            currency=p.get("currency", "EUR")
        )
        result = pd_add_product_to_deal(
            deal_id=deal_id,
            product_id=product["id"],
            item_price=p.get("price", 0),
            quantity=p.get("quantity", 1),
            discount=p.get("discount", 0),
            tax=p.get("tax", 0)
        )
        added.append(result)
        print(f"PD PRODUCT: Added '{p['name']}' to deal {deal_id} ({p.get('quantity', 1)}x €{p.get('price', 0)})")

    return added


# ---- Notes ----
def pd_add_note_to_deal(deal_id: int, content: str) -> dict:
    """Add a note to a deal in Pipedrive."""
    return pd_post("/notes", {
        "deal_id": deal_id,
        "content": content
    })
