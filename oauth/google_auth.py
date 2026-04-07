"""
Google Ads OAuth 2.0 Authentication
"""

import os
import json
import requests
import logging
from typing import Dict, Any

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/adwords']
API_VERSION = "v20"

GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")


def format_customer_id(customer_id: str) -> str:
    """Normalize a customer ID to a 10-digit string without dashes."""
    customer_id = str(customer_id).replace('"', '').replace("'", "")
    customer_id = ''.join(c for c in customer_id if c.isdigit())
    return customer_id.zfill(10)


def get_oauth_credentials() -> Credentials:
    """
    Return valid OAuth credentials, running the browser auth flow if needed.
    Tokens are cached next to the OAuth config file as google_ads_token.json.
    """
    # Read fresh from env each call so server-side setup via env vars is picked up
    GOOGLE_ADS_OAUTH_CONFIG_PATH = os.environ.get("GOOGLE_ADS_OAUTH_CONFIG_PATH")

    if not GOOGLE_ADS_OAUTH_CONFIG_PATH:
        raise ValueError(
            "GOOGLE_ADS_OAUTH_CONFIG_PATH is not set. "
            "Point it to your OAuth 2.0 client secret JSON downloaded from Google Cloud Console."
        )

    if not os.path.exists(GOOGLE_ADS_OAUTH_CONFIG_PATH):
        raise FileNotFoundError(f"OAuth config file not found: {GOOGLE_ADS_OAUTH_CONFIG_PATH}")

    config_dir = os.path.dirname(os.path.abspath(GOOGLE_ADS_OAUTH_CONFIG_PATH))
    token_path = os.path.join(config_dir, "google_ads_token.json")

    creds = None

    # Load cached token
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            logger.info("Loaded cached OAuth token from %s", token_path)
        except Exception as e:
            logger.warning("Could not load cached token: %s", e)
            creds = None

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired OAuth token")
                creds.refresh(Request())
                logger.info("Token refreshed successfully")
            except RefreshError as e:
                logger.warning("Token refresh failed (%s); starting new auth flow", e)
                creds = None
            except Exception:
                raise

        if not creds:
            logger.info("Starting OAuth browser flow")
            with open(GOOGLE_ADS_OAUTH_CONFIG_PATH, "r") as f:
                client_config = json.load(f)

            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            try:
                creds = flow.run_local_server(port=0)
                logger.info("OAuth flow completed via local server")
            except Exception as e:
                logger.warning("Local server failed (%s); falling back to console flow", e)
                creds = flow.run_console()
                logger.info("OAuth flow completed via console")

        # Cache the new token
        try:
            with open(token_path, "w") as f:
                f.write(creds.to_json())
            logger.info("Token saved to %s", token_path)
        except Exception as e:
            logger.warning("Could not save token: %s", e)

    return creds


def get_headers_with_auto_token() -> Dict[str, str]:
    """Return HTTP headers with a fresh Bearer token and the developer token."""
    if not GOOGLE_ADS_DEVELOPER_TOKEN:
        raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN is not set.")

    creds = get_oauth_credentials()
    return {
        "Authorization": f"Bearer {creds.token}",
        "Developer-Token": GOOGLE_ADS_DEVELOPER_TOKEN.strip("'\""),
        "Content-Type": "application/json",
    }


def execute_gaql(customer_id: str, query: str, manager_id: str = "") -> Dict[str, Any]:
    """Execute a GAQL query and return the parsed results."""
    headers = get_headers_with_auto_token()
    fid = format_customer_id(customer_id)

    if manager_id:
        headers["login-customer-id"] = format_customer_id(manager_id)

    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{fid}/googleAds:search"
    resp = requests.post(url, headers=headers, json={"query": query})

    if not resp.ok:
        raise Exception(
            f"GAQL error {resp.status_code} {resp.reason}: {resp.text}"
        )

    data = resp.json()
    results = data.get("results", [])
    return {
        "results": results,
        "query": query,
        "totalRows": len(results),
    }
