"""Position sizing. Leverage is an OUTPUT of the stop distance, never an input."""
import math


def position_size(equity, risk_pct, entry, stop, cfg, max_margin_frac=0.25):
    risk_usd = equity * risk_pct / 100.0
    dist = abs(entry - stop)
    qty = risk_usd / dist
    notional = qty * entry
    # isolated margin: liquidation distance ~ entry / leverage (ignoring maintenance margin)
    # require it to be >= liq_buffer_mult * stop distance
    lev_cap_by_liq = max(1, math.floor(entry / (cfg.liq_buffer_mult * dist)))
    lev_needed = max(1, math.ceil(notional / (max_margin_frac * equity)))  # keep margin <= 25% of equity
    leverage = min(cfg.max_leverage, lev_cap_by_liq, max(lev_needed, 1))
    margin = notional / leverage
    ok = margin <= equity * max_margin_frac + 1e-9
    return {"risk_usd": round(risk_usd, 2), "qty": qty, "notional": round(notional, 2),
            "leverage": leverage, "margin": round(margin, 2),
            "note": "" if ok else "stop too tight for safe leverage: margin > 25% of equity, reduce size or skip"}
