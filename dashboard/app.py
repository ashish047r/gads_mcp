import sys
import os
import json
import tempfile
import logging
import requests as req

# Allow importing from the parent MCP project
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from functools import wraps
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ---------------------------------------------------------------------------
# Production credential setup — MUST run before importing oauth.google_auth
# because google_auth reads GOOGLE_ADS_OAUTH_CONFIG_PATH at module level.
# ---------------------------------------------------------------------------

def _setup_credentials_from_env():
    """Write credentials from env vars to temp files so the OAuth library can find them."""
    secret_json = os.environ.get("GOOGLE_ADS_CLIENT_SECRET_JSON")
    token_json  = os.environ.get("GOOGLE_ADS_TOKEN_JSON")

    if secret_json and not os.environ.get("GOOGLE_ADS_OAUTH_CONFIG_PATH"):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="gads_secret_"
        )
        tmp.write(secret_json)
        tmp.close()
        os.environ["GOOGLE_ADS_OAUTH_CONFIG_PATH"] = tmp.name

        if token_json:
            token_path = os.path.join(os.path.dirname(tmp.name), "google_ads_token.json")
            with open(token_path, "w") as f:
                f.write(token_json)

_setup_credentials_from_env()

from oauth.google_auth import execute_gaql, format_customer_id, get_headers_with_auto_token
from users import USERS

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dashboard")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-this-in-prod")

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = USERS.get(username)
        if user and user["password"] == password:
            session["username"]     = username
            session["display_name"] = user.get("display_name", username)
            session["is_admin"]     = user["account_ids"] is None
            session["account_ids"]  = user["account_ids"]
            session["manager_id"]   = user.get("manager_id", "")
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        username=session["display_name"],
        is_admin=session["is_admin"],
    )


@app.route("/api/metrics")
@login_required
def api_metrics():
    """
    Returns campaign metrics as JSON.
    Query params:
        start  — YYYY-MM-DD (default: 30 days ago)
        end    — YYYY-MM-DD (default: today)
    """
    today     = datetime.today()
    end_date  = request.args.get("end",   today.strftime("%Y-%m-%d"))
    start_date = request.args.get("start", (today - timedelta(days=30)).strftime("%Y-%m-%d"))

    is_admin    = session.get("is_admin", False)
    account_ids = session.get("account_ids")   # None if admin
    manager_id  = session.get("manager_id", "")

    # Admin: discover all accessible account IDs dynamically
    if is_admin:
        account_ids = _list_all_account_ids()

    if not account_ids:
        return jsonify({"error": "No accounts found.", "data": []})

    all_rows  = []
    errors    = []

    for account_id in account_ids:
        try:
            rows = _fetch_campaign_metrics(account_id, start_date, end_date, manager_id)
            all_rows.extend(rows)
        except Exception as e:
            logger.error("Error fetching account %s: %s", account_id, e)
            errors.append({"account_id": account_id, "error": str(e)})

    # Sort by impressions descending
    all_rows.sort(key=lambda r: r["impressions"], reverse=True)

    return jsonify({
        "data":       all_rows,
        "errors":     errors,
        "start_date": start_date,
        "end_date":   end_date,
        "total_rows": len(all_rows),
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _list_all_account_ids():
    """Return all accessible customer IDs (flat list) for the admin user."""
    try:
        headers = get_headers_with_auto_token()
        url     = "https://googleads.googleapis.com/v20/customers:listAccessibleCustomers"
        resp    = req.get(url, headers=headers)
        if not resp.ok:
            logger.error("listAccessibleCustomers failed: %s", resp.text)
            return []
        resource_names = resp.json().get("resourceNames", [])
        return [format_customer_id(r.split("/")[-1]) for r in resource_names]
    except Exception as e:
        logger.error("Error listing accounts: %s", e)
        return []


def _fetch_campaign_metrics(account_id, start_date, end_date, manager_id=""):
    """Fetch impressions, clicks, CTR for all enabled campaigns in a date range."""
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
          AND campaign.status != 'REMOVED'
        ORDER BY metrics.impressions DESC
    """
    result = execute_gaql(account_id, query, manager_id)
    rows   = []
    for row in result.get("results", []):
        campaign = row.get("campaign", {})
        m        = row.get("metrics", {})
        rows.append({
            "account_id":    account_id,
            "campaign_id":   campaign.get("id", ""),
            "campaign_name": campaign.get("name", "—"),
            "impressions":   int(m.get("impressions", 0)),
            "clicks":        int(m.get("clicks", 0)),
            "ctr":           round(float(m.get("ctr", 0)) * 100, 2),
            "cost":          round(int(m.get("costMicros", 0)) / 1_000_000, 2),
        })
    return rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
