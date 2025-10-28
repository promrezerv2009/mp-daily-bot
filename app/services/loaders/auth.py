from typing import Dict


def ozon_headers(client_id: str, api_key: str) -> Dict[str, str]:
    return {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }


def wb_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": token,
        "Content-Type": "application/json",
    }
