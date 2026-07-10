"""
Main run loop. Pulls intraday data, applies the SELECTED strategy (from
strategy_selector.py's backtest ranking) per ticker, and routes signals to
the broker (paper by default -- see broker.py and config.MODE).

Run strategy_selector.py first to decide which strategy to use per ticker,
then set SELECTED_STRATEGY below (or wire it up to read the selector's output
automatically -- left manual here so you consciously choose what goes live).
"""
import time
import json
import os
from datetime import datetime

import config
from data_fetcher import fetch_intraday
from strategies import STRATEGY_FUNCS
from options_pricing import bs_price, select_strike
from broker import get_broker

# Set this after reviewing strategy_selector.py output. Example:
# SELECTED_STRATEGY = {"SPY": "ma_rsi", "QQQ": "bb_squeeze_breakout"}
SELECTED_STRATEGY = {}

STATE_PATH = os.path.join(os.path.dirname(__file__), "open_positions.json")


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)


def run_once(broker, state):
    if not SELECTED_STRATEGY:
        print("[!] SELECTED_STRATEGY is empty. Run strategy_selector.py and set it in live_agent.py first.")
        return state

    for ticker in config.TICKERS:
        strategy_name = SELECTED_STRATEGY.get(ticker)
        if not strategy_name:
            continue

        df = fetch_intraday(ticker, interval=config.DATA_INTERVAL, period=config.DATA_PERIOD)
        params = config.STRATEGIES[strategy_name]
        df = STRATEGY_FUNCS[strategy_name](df, params)

        latest = df.iloc[-1]
        spot = latest["close"]
        signal = latest["signal"]

        open_pos = state.get(ticker)

        if open_pos is None and signal != 0:
            option_type = "call" if signal == 1 else "put"
            strike = select_strike(spot, config.STRIKE_OFFSET_PCT, option_type)
            iv = config.DEFAULT_IV.get(ticker, 0.16)
            entry_price = bs_price(spot, strike, config.TARGET_DTE_DAYS, iv, config.RISK_FREE_RATE, option_type)

            result = broker.buy_to_open(
                ticker, option_type, strike, expiry=f"+{config.TARGET_DTE_DAYS}d",
                contracts=config.POSITION_SIZE_CONTRACTS, price_per_contract=entry_price,
            )
            if result["status"] == "filled":
                state[ticker] = {
                    "entry_time": str(datetime.now()), "entry_spot": spot,
                    "option_type": option_type, "strike": strike,
                    "entry_price": entry_price, "position": result["position"],
                }
                print(f"[{ticker}] ENTERED {option_type} @ strike {strike}, est price {entry_price:.2f}")

        elif open_pos is not None:
            iv = config.DEFAULT_IV.get(ticker, 0.16)
            current_price = bs_price(spot, open_pos["strike"], config.TARGET_DTE_DAYS, iv,
                                      config.RISK_FREE_RATE, open_pos["option_type"])
            pct_change = (current_price - open_pos["entry_price"]) / open_pos["entry_price"]

            should_exit = (pct_change >= config.PROFIT_TARGET_PCT or
                           pct_change <= -config.STOP_LOSS_PCT)

            if should_exit:
                result = broker.sell_to_close(open_pos["position"], current_price)
                print(f"[{ticker}] EXITED, pnl={result['pnl']:.2f}")
                state[ticker] = None

    return state


def main_loop(poll_seconds=60, max_iterations=None):
    broker = get_broker()
    state = load_state()
    i = 0
    while max_iterations is None or i < max_iterations:
        state = run_once(broker, state)
        save_state(state)
        print(broker.account_summary())
        i += 1
        if max_iterations is None or i < max_iterations:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main_loop(poll_seconds=60, max_iterations=1)  # single pass by default; remove max_iterations for continuous
