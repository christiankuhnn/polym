#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_]{1,50}$")
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass
class UserSpec:
    raw: str
    label: str
    wallet: str


class PolymarketTracker:
    def __init__(
        self,
        users_file: Path,
        state_file: Path,
        poll_seconds: int = 10,
        activity_limit: int = 50,
        discord_webhook: str = "",
        request_timeout: int = 20,
    ) -> None:
        self.users_file = users_file
        self.state_file = state_file
        self.poll_seconds = poll_seconds
        self.activity_limit = activity_limit
        self.discord_webhook = discord_webhook.strip()
        self.request_timeout = request_timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "polymarket-tracker/0.1"})
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"seen_trade_keys": [], "users": {}}

    def _save_state(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = self.session.get(url, params=params, timeout=self.request_timeout)
        response.raise_for_status()
        return response.json()

    def _safe_get_public_profile(self, wallet: str) -> Optional[Dict[str, Any]]:
        try:
            return self._get(f"{GAMMA_API}/public-profile", params={"address": wallet})
        except requests.RequestException:
            return None

    def resolve_user(self, item: str) -> UserSpec:
        value = item.strip()
        if not value:
            raise ValueError("Empty user entry found in users.json")

        if ADDRESS_RE.match(value):
            wallet = value
            profile = self._safe_get_public_profile(wallet) or {}
            label = profile.get("name") or profile.get("pseudonym") or wallet
            return UserSpec(raw=value, label=label, wallet=wallet)

        if HANDLE_RE.match(value):
            handle = value.lstrip("@")
            result = self._get(
                f"{GAMMA_API}/public-search",
                params={
                    "q": handle,
                    "search_profiles": "true",
                    "limit_per_type": 10,
                    "optimized": "true",
                },
            )
            profiles = result.get("profiles") or []
            if not profiles:
                raise ValueError(f"Could not resolve handle @{handle}")

            exact = None
            for profile in profiles:
                name = (profile.get("name") or "").strip().lower()
                pseudonym = (profile.get("pseudonym") or "").strip().lower()
                if handle.lower() in {name, pseudonym}:
                    exact = profile
                    break

            chosen = exact or profiles[0]
            wallet = chosen.get("proxyWallet")
            if not wallet or not ADDRESS_RE.match(wallet):
                raise ValueError(
                    f"Profile @{handle} resolved, but no valid proxyWallet was returned"
                )

            label = chosen.get("name") or chosen.get("pseudonym") or f"@{handle}"
            return UserSpec(raw=value, label=label, wallet=wallet)

        raise ValueError(
            f"Unsupported identifier: {value}. Use a wallet address or a public handle like @rdba"
        )

    def load_users(self) -> List[UserSpec]:
        if not self.users_file.exists():
            raise FileNotFoundError(
                f"{self.users_file} not found. Create it from users.example.json first."
            )

        raw = json.loads(self.users_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "users" not in raw or not isinstance(raw["users"], list):
            raise ValueError("users.json must be an object with a 'users' list")

        return [self.resolve_user(entry) for entry in raw["users"]]

    def fetch_activity(self, wallet: str) -> List[Dict[str, Any]]:
        return self._get(
            f"{DATA_API}/activity",
            params={
                "user": wallet,
                "type": "TRADE",
                "limit": self.activity_limit,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            },
        )

    def trade_key(self, item: Dict[str, Any]) -> str:
        tx = item.get("transactionHash") or "nohash"
        ts = item.get("timestamp") or 0
        asset = item.get("asset") or ""
        side = item.get("side") or ""
        size = item.get("size") or 0
        price = item.get("price") or 0
        outcome = item.get("outcome") or ""
        title = item.get("title") or ""
        return f"{tx}|{ts}|{asset}|{side}|{size}|{price}|{outcome}|{title}"

    def format_alert(self, user: UserSpec, item: Dict[str, Any]) -> str:
        slug = item.get("slug", "")
        market_url = f"https://polymarket.com/event/{slug}" if slug else "N/A"
        return (
            f"New Polymarket trade detected\n"
            f"User: {user.label}\n"
            f"Wallet: {user.wallet}\n"
            f"Market: {item.get('title', 'Unknown market')}\n"
            f"Outcome: {item.get('outcome', '?')}\n"
            f"Side: {item.get('side', '?')}\n"
            f"Price: {item.get('price', '?')}\n"
            f"Size: {item.get('size', '?')}\n"
            f"USDC Size: {item.get('usdcSize', '?')}\n"
            f"Timestamp: {item.get('timestamp', '?')}\n"
            f"Market URL: {market_url}"
        )

    def send_discord(self, text: str) -> None:
        if not self.discord_webhook:
            return
        response = requests.post(
            self.discord_webhook,
            json={"content": text[:1900]},
            timeout=self.request_timeout,
        )
        response.raise_for_status()

    def bootstrap_user(self, user: UserSpec) -> None:
        items = self.fetch_activity(user.wallet)
        seen_keys = set(self.state.get("seen_trade_keys", []))
        newest_ts = 0
        for item in items:
            seen_keys.add(self.trade_key(item))
            newest_ts = max(newest_ts, int(item.get("timestamp") or 0))
        self.state["seen_trade_keys"] = list(seen_keys)[-5000:]
        self.state["users"][user.wallet] = {"label": user.label, "last_bootstrap_ts": newest_ts}

    def poll_once(self, users: List[UserSpec]) -> int:
        seen_keys = set(self.state.get("seen_trade_keys", []))
        alert_count = 0
        for user in users:
            items = self.fetch_activity(user.wallet)
            new_items: List[Tuple[int, Dict[str, Any]]] = []
            for item in items:
                key = self.trade_key(item)
                usdc_value = item.get("usdcSize")
                usdc_text = str(usdc_value).strip() if usdc_value is not None else ""
                usdc_size = float(usdc_value or 0)
                if key not in seen_keys and usdc_size >= 100 and usdc_text.isdigit():
                    new_items.append((int(item.get("timestamp") or 0), item))
                    seen_keys.add(key)
            new_items.sort(key=lambda x: x[0])


            for _, item in new_items:
                alert = self.format_alert(user, item)
                print("=" * 80)
                print(alert)
                print("=" * 80)
                try:
                    self.send_discord(alert)
                except requests.RequestException as exc:
                    print(f"[warn] Discord send failed: {exc}", file=sys.stderr)
                alert_count += 1

        self.state["seen_trade_keys"] = list(seen_keys)[-5000:]
        return alert_count

    def run(self) -> None:
        users = self.load_users()
        print("Tracking users:")
        for user in users:
            print(f" - {user.label} ({user.wallet})")

        missing_bootstrap = [u for u in users if u.wallet not in self.state.get("users", {})]
        if missing_bootstrap:
            print("\nInitializing baseline so existing trades do not trigger alerts...")
            for user in missing_bootstrap:
                self.bootstrap_user(user)
            self._save_state()
            print("Baseline saved.\n")

        print(f"Polling every {self.poll_seconds} seconds. Press Ctrl+C to stop.\n")
        while True:
            try:
                count = self.poll_once(users)
                if count == 0:
                    print("[ok] No new tracked trades.")
                self._save_state()
            except requests.RequestException as exc:
                print(f"[error] Request failed: {exc}", file=sys.stderr)
            except Exception as exc:
                print(f"[error] Unexpected error: {exc}", file=sys.stderr)
            time.sleep(self.poll_seconds)


def main() -> None:
    load_dotenv()
    tracker = PolymarketTracker(
        users_file=Path(os.getenv("USERS_FILE", "users.json")),
        state_file=Path(os.getenv("STATE_FILE", ".state.json")),
        poll_seconds=int(os.getenv("POLL_SECONDS", "10")),
        activity_limit=int(os.getenv("ACTIVITY_LIMIT", "50")),
        discord_webhook=os.getenv("DISCORD_WEBHOOK_URL", ""),
    )
    tracker.run()


if __name__ == "__main__":
    main()
