"""
Broker abstraction layer.

PaperBroker: fully functional, simulates fills locally, tracks a ledger in
paper_ledger.json. Safe to run indefinitely, no real money at risk.

LiveBroker: a STUB. It defines the interface but does not ship with any
credentials, API keys, or account connection. To go live you must:
  1. Open and fund an account with a broker that supports options API trading
     (Tradier and Alpaca both do).
  2. Get your own API key/secret from that broker.
  3. Fill in the request calls below with their actual endpoints (they differ
     per broker -- check the broker's current API docs, since endpoints and
     auth schemes change).
  4. Test extensively in that broker's own sandbox/paper environment first.
  5. Only then flip config.MODE to "live".

I'm not wiring up real trade execution automatically -- that's a deliberate
line. Sending real orders to a real brokerage account is an action with
financial consequences, and it should require you to deliberately plug in
your own verified credentials and flip the switch yourself, not happen because
a script defaulted to it.
"""
import json
import os
from datetime import datetime

LEDGER_PATH = os.path.join(os.path.dirname(__file__), "paper_ledger.json")


class PaperBroker:
    def __init__(self, starting_capital=10000.0):
        self.capital = starting_capital
        self.positions = []
        self.closed_trades = []
        self._load_ledger()

    def _load_ledger(self):
        if os.path.exists(LEDGER_PATH):
            with open(LEDGER_PATH) as f:
                data = json.load(f)
                self.capital = data.get("capital", self.capital)
                self.closed_trades = data.get("closed_trades", [])

    def _save_ledger(self):
        with open(LEDGER_PATH, "w") as f:
            json.dump({"capital": self.capital, "closed_trades": self.closed_trades}, f, indent=2, default=str)

    def buy_to_open(self, ticker, option_type, strike, expiry, contracts, price_per_contract):
        cost = price_per_contract * 100 * contracts
        if cost > self.capital:
            return {"status": "rejected", "reason": "insufficient paper capital"}
        self.capital -= cost
        position = {
            "ticker": ticker, "option_type": option_type, "strike": strike,
            "expiry": str(expiry), "contracts": contracts,
            "entry_price": price_per_contract, "entry_time": str(datetime.now()),
        }
        self.positions.append(position)
        return {"status": "filled", "position": position}

    def sell_to_close(self, position, price_per_contract):
        proceeds = price_per_contract * 100 * position["contracts"]
        self.capital += proceeds
        pnl = proceeds - (position["entry_price"] * 100 * position["contracts"])
        closed = {**position, "exit_price": price_per_contract, "exit_time": str(datetime.now()), "pnl": pnl}
        self.closed_trades.append(closed)
        self.positions.remove(position)
        self._save_ledger()
        return {"status": "closed", "pnl": pnl}

    def account_summary(self):
        return {
            "capital": round(self.capital, 2),
            "open_positions": len(self.positions),
            "closed_trades": len(self.closed_trades),
        }


class LiveBroker:
    """Stub. Not implemented -- see module docstring. Raises on use so it
    can never accidentally fire real orders."""

    def __init__(self, api_key=None, api_secret=None, account_id=None):
        if not (api_key and api_secret and account_id):
            raise NotImplementedError(
                "LiveBroker requires your own broker API credentials. See broker.py "
                "docstring for setup steps. This is intentionally not pre-wired."
            )
        # You would initialize the actual broker SDK/client here, e.g.:
        # self.client = tradier_client.Client(api_key, account_id)
        raise NotImplementedError(
            "Live order routing is not implemented in this template. Implement "
            "buy_to_open/sell_to_close against your chosen broker's documented "
            "options order endpoints before using this class."
        )

    def buy_to_open(self, *args, **kwargs):
        raise NotImplementedError

    def sell_to_close(self, *args, **kwargs):
        raise NotImplementedError


def get_broker():
    import config
    if config.MODE == "paper":
        return PaperBroker(starting_capital=config.STARTING_CAPITAL)
    elif config.MODE == "live":
        return LiveBroker()  # will raise until you fill in credentials + implementation
    else:
        raise ValueError(f"Unknown MODE: {config.MODE}")
