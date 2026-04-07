# -------------------------------------------------------
# USER CONFIG
# -------------------------------------------------------
# account_ids: None  → admin, sees ALL accounts
# account_ids: list  → client, sees only listed account IDs
# manager_id:  ""    → not under an MCC
# manager_id:  "ID"  → account is under this MCC (login-customer-id)
# -------------------------------------------------------

USERS = {
    "ashish": {
        "password": "admin123",          # change this
        "account_ids": None,             # None = see all
        "manager_id": "",
        "display_name": "Ashish (Admin)",
    },

    # --- Add your clients below ---
    # "client_acme": {
    #     "password": "acme2024",
    #     "account_ids": ["1234567890"],
    #     "manager_id": "",              # set MCC ID if applicable
    #     "display_name": "Acme Corp",
    # },
    # "client_xyz": {
    #     "password": "xyz2024",
    #     "account_ids": ["0987654321", "1122334455"],
    #     "manager_id": "",
    #     "display_name": "XYZ Agency",
    # },
}
