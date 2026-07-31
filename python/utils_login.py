import os

from jproperties import Properties
import time
import jwt
import requests

def headers(cache, base_url):
    u, p = os.environ["username"], os.environ["password"]
    if "h" in cache:
        h = cache["h"]
        token = h["authorization"].split()[1]
        decoded = jwt.decode(token, options={"verify_signature": False})
        u_token = decoded["https://sympheny.com/email"]
        if u == u_token and is_jwt_valid_for_at_least_10_minutes(decoded):
            return h

    cache["h"] = get_headers_pwd(base_url, u, p)
    return cache["h"]


def is_jwt_valid_for_at_least_10_minutes(decoded: dict) -> bool:
    exp = decoded.get("exp")
    if exp is None:
        return False

    buffer_time = time.time() + 600
    return exp > buffer_time

def get_headers_pwd(base_url: str, username: str, pwd: str) -> dict:
    data = {"email": username, "password": pwd}
    resp = requests.post(f"{base_url}backoffice/auth/ext/token", json=data)
    token = resp.json()["access_token"]

    return {"authorization": f"Bearer {token}"}

def get_username_password(path: str) -> tuple[str, str]:
    configs = load_config(path)
    u = configs.get("username").data
    p = configs.get("password").data
    return u, p

def load_config(path: str) -> Properties:
    configs = Properties()
    with open(path, "rb") as f:
        configs.load(f)

    return configs
