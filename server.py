from fastmcp import FastMCP, Context
from typing import Any, Dict, List, Optional
import os
import logging
import requests
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from oauth.google_auth import format_customer_id, get_headers_with_auto_token, execute_gaql

GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("google_ads_mcp")

mcp = FastMCP("Google Ads MCP")

logger.info("Google Ads MCP Server starting...")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_customer_name(customer_id: str) -> str:
    try:
        result = execute_gaql(customer_id, "SELECT customer.descriptive_name FROM customer")
        rows = result.get("results", [])
        if rows:
            return rows[0].get("customer", {}).get("descriptiveName", "Unknown")
    except Exception:
        pass
    return "Unknown"


def _is_manager(customer_id: str) -> bool:
    try:
        result = execute_gaql(customer_id, "SELECT customer.manager FROM customer")
        rows = result.get("results", [])
        if rows:
            return bool(rows[0].get("customer", {}).get("manager", False))
    except Exception:
        pass
    return False


def _get_sub_accounts(manager_id: str) -> List[Dict[str, Any]]:
    try:
        query = (
            "SELECT customer_client.id, customer_client.descriptive_name, "
            "customer_client.level, customer_client.manager "
            "FROM customer_client WHERE customer_client.level > 0"
        )
        result = execute_gaql(manager_id, query)
        subs = []
        for row in result.get("results", []):
            client = row.get("customerClient", {}) or row.get("customer_client", {})
            cid = format_customer_id(str(client.get("id", "")))
            subs.append({
                "id": cid,
                "name": client.get("descriptiveName", f"Account {cid}"),
                "access_type": "managed",
                "is_manager": bool(client.get("manager", False)),
                "parent_id": manager_id,
                "level": int(client.get("level", 0)),
            })
        return subs
    except Exception:
        return []


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool
def list_accounts(ctx: Context = None) -> Dict[str, Any]:
    """List all Google Ads accounts accessible to the authenticated user, including sub-accounts."""
    if not GOOGLE_ADS_DEVELOPER_TOKEN:
        raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN is not set.")

    if ctx:
        ctx.info("Fetching accessible Google Ads accounts...")

    headers = get_headers_with_auto_token()
    url = "https://googleads.googleapis.com/v20/customers:listAccessibleCustomers"
    resp = requests.get(url, headers=headers)

    if not resp.ok:
        raise Exception(f"Failed to list accounts: {resp.status_code} {resp.reason} - {resp.text}")

    resource_names = resp.json().get("resourceNames", [])
    if not resource_names:
        return {"accounts": [], "total_accounts": 0, "message": "No accessible accounts found."}

    if ctx:
        ctx.info(f"Found {len(resource_names)} top-level account(s). Fetching details...")

    accounts: List[Dict[str, Any]] = []
    seen: set = set()

    for resource in resource_names:
        cid = format_customer_id(resource.split("/")[-1])
        if cid in seen:
            continue
        name = _get_customer_name(cid)
        manager = _is_manager(cid)
        accounts.append({"id": cid, "name": name, "access_type": "direct", "is_manager": manager, "level": 0})
        seen.add(cid)

        if manager:
            for sub in _get_sub_accounts(cid):
                if sub["id"] not in seen:
                    accounts.append(sub)
                    seen.add(sub["id"])
                    if sub["is_manager"]:
                        for nested in _get_sub_accounts(sub["id"]):
                            if nested["id"] not in seen:
                                accounts.append(nested)
                                seen.add(nested["id"])

    if ctx:
        ctx.info(f"Total accounts found: {len(accounts)}")

    return {"accounts": accounts, "total_accounts": len(accounts)}


@mcp.tool
def run_gaql(
    customer_id: str,
    query: str,
    manager_id: str = "",
    ctx: Context = None,
) -> Dict[str, Any]:
    """
    Run a Google Ads Query Language (GAQL) query against a specific account.

    Args:
        customer_id: The Google Ads customer ID (10 digits, no dashes).
        query: A valid GAQL query string.
        manager_id: Manager account ID if the customer is under an MCC account.

    Returns:
        Query results with row count.
    """
    if not GOOGLE_ADS_DEVELOPER_TOKEN:
        raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN is not set.")

    if ctx:
        ctx.info(f"Running GAQL query for account {customer_id}")

    result = execute_gaql(customer_id, query, manager_id)

    if ctx:
        ctx.info(f"Query returned {result['totalRows']} row(s).")

    return result


@mcp.tool
def run_keyword_planner(
    customer_id: str,
    keywords: List[str],
    manager_id: str = "",
    page_url: Optional[str] = None,
    start_year: Optional[int] = None,
    start_month: Optional[str] = None,
    end_year: Optional[int] = None,
    end_month: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """
    Generate keyword ideas with search volume and bid metrics using Google Ads Keyword Planner.

    Args:
        customer_id: The Google Ads customer ID (10 digits, no dashes).
        keywords: Seed keywords to generate ideas from.
        manager_id: Manager account ID if the customer is under an MCC account.
        page_url: Optional URL to use as a seed for keyword ideas.
        start_year: Start year for historical metrics (defaults to last year).
        start_month: Start month name e.g. JANUARY (defaults to JANUARY).
        end_year: End year for historical metrics (defaults to current year).
        end_month: End month name e.g. DECEMBER (defaults to current month).

    Returns:
        List of keyword ideas with avg monthly searches, competition, and bid estimates.

    Note:
        At least one of `keywords` or `page_url` must be provided.
        Valid months: JANUARY, FEBRUARY, MARCH, APRIL, MAY, JUNE,
                      JULY, AUGUST, SEPTEMBER, OCTOBER, NOVEMBER, DECEMBER
    """
    if not GOOGLE_ADS_DEVELOPER_TOKEN:
        raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN is not set.")

    if not keywords and not page_url:
        raise ValueError("Provide at least one seed keyword or a page_url.")

    if ctx:
        ctx.info(f"Generating keyword ideas for account {customer_id}")

    headers = get_headers_with_auto_token()
    fid = format_customer_id(customer_id)

    if manager_id:
        headers["login-customer-id"] = format_customer_id(manager_id)

    now = datetime.now()
    valid_months = [
        "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
        "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
    ]

    s_year = start_year or (now.year - 1)
    s_month = (start_month or "JANUARY").upper()
    if s_month not in valid_months:
        s_month = "JANUARY"

    e_year = end_year or now.year
    e_month = (end_month or now.strftime("%B")).upper()
    if e_month not in valid_months:
        e_month = now.strftime("%B").upper()

    body: Dict[str, Any] = {
        "language": "languageConstants/1000",
        "geoTargetConstants": ["geoTargetConstants/2840"],
        "keywordPlanNetwork": "GOOGLE_SEARCH_AND_PARTNERS",
        "includeAdultKeywords": False,
        "pageSize": 25,
        "historicalMetricsOptions": {
            "yearMonthRange": {
                "start": {"year": s_year, "month": s_month},
                "end": {"year": e_year, "month": e_month},
            }
        },
    }

    if keywords and page_url:
        body["keywordAndUrlSeed"] = {"url": page_url, "keywords": keywords}
    elif keywords:
        body["keywordSeed"] = {"keywords": keywords}
    else:
        body["urlSeed"] = {"url": page_url}

    url = f"https://googleads.googleapis.com/v20/customers/{fid}:generateKeywordIdeas"
    resp = requests.post(url, headers=headers, json=body)

    if not resp.ok:
        raise Exception(f"Keyword Planner error {resp.status_code} {resp.reason}: {resp.text}")

    data = resp.json()
    raw_results = data.get("results", [])

    if not raw_results:
        return {
            "keyword_ideas": [],
            "total_ideas": 0,
            "input_keywords": keywords or [],
            "input_page_url": page_url,
            "date_range": f"{s_month} {s_year} to {e_month} {e_year}",
            "message": "No keyword ideas found for the given inputs.",
        }

    ideas = []
    for item in raw_results:
        metrics = item.get("keywordIdeaMetrics", {})
        ideas.append({
            "keyword": item.get("text", "N/A"),
            "avg_monthly_searches": metrics.get("avgMonthlySearches", "N/A"),
            "competition": metrics.get("competition", "N/A"),
            "competition_index": metrics.get("competitionIndex", "N/A"),
            "low_top_of_page_bid_micros": metrics.get("lowTopOfPageBidMicros", "N/A"),
            "high_top_of_page_bid_micros": metrics.get("highTopOfPageBidMicros", "N/A"),
        })

    if ctx:
        ctx.info(f"Found {len(ideas)} keyword idea(s).")

    return {
        "keyword_ideas": ideas,
        "total_ideas": len(ideas),
        "input_keywords": keywords or [],
        "input_page_url": page_url,
        "date_range": f"{s_month} {s_year} to {e_month} {e_year}",
    }


# ---------------------------------------------------------------------------
# GAQL Reference Resource
# ---------------------------------------------------------------------------

@mcp.resource("gaql://reference")
def gaql_reference() -> str:
    """GAQL quick-reference: syntax, common fields, and example queries."""
    return """
## GAQL Basic Syntax

    SELECT field1, field2
    FROM   resource_type
    WHERE  condition
    ORDER BY field [ASC|DESC]
    LIMIT  n

## Common Resources
- campaign
- ad_group
- keyword_view
- ad_group_ad
- campaign_budget

## Metric Fields
- metrics.impressions
- metrics.clicks
- metrics.cost_micros        (divide by 1,000,000 for currency)
- metrics.conversions
- metrics.conversions_value  (primary revenue metric)
- metrics.ctr
- metrics.average_cpc

## Segment Fields
- segments.date
- segments.device
- segments.day_of_week

## Date Filters
    WHERE segments.date DURING LAST_7_DAYS
    WHERE segments.date DURING LAST_30_DAYS
    WHERE segments.date BETWEEN '2024-01-01' AND '2024-01-31'

## String Matching
    Use LIKE '%text%'  (CONTAINS is not supported)

## Example Queries

1. Campaign metrics (last 7 days):
   SELECT campaign.id, campaign.name,
          metrics.clicks, metrics.impressions, metrics.cost_micros
   FROM   campaign
   WHERE  segments.date DURING LAST_7_DAYS

2. Ad group performance:
   SELECT campaign.id, ad_group.name,
          metrics.conversions, metrics.cost_micros
   FROM   ad_group
   WHERE  metrics.clicks > 100

3. Keyword analysis:
   SELECT campaign.id,
          ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type,
          metrics.ctr
   FROM   keyword_view
   WHERE  segments.date DURING LAST_30_DAYS
   ORDER BY metrics.impressions DESC

4. Conversion revenue:
   SELECT campaign.id, campaign.name,
          metrics.conversions, metrics.conversions_value,
          metrics.cost_micros
   FROM   campaign
   WHERE  segments.date DURING LAST_30_DAYS

## Common Mistakes
- WRONG:   campaign.campaign_budget.amount_micros
  CORRECT: campaign_budget.amount_micros  (query FROM campaign_budget)

- WRONG:   keyword.text
  CORRECT: ad_group_criterion.keyword.text

- Open-ended date ranges are NOT supported.
  Use DURING or BETWEEN with explicit dates.
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if "--http" in sys.argv:
        logger.info("Starting HTTP transport on http://127.0.0.1:8000/mcp")
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, path="/mcp")
    else:
        logger.info("Starting STDIO transport (Claude Desktop mode)")
        mcp.run(transport="stdio")

