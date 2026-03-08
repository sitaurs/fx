"""
tools/validator.py — Trading plan validator (hard rule checks).

Anti-Rungkad rules (masterplan §5.3):
    - Sweep filter: don't entry at first support/demand without sweep + reclaim.
    - CHOCH filter: after entering zone, wait for M5/M15 break.
    - Crash cancel: marubozu without rejection = CANCEL.
    - ATR SL: SL must be swing extreme ± 1.5 × ATR.
    - Min R:R = 1.5

Reference: masterplan.md §5.1 (validator signature), §5.3
"""

from __future__ import annotations

import math

# D-09: Removed unused MIN_RR, SL_ATR_MULTIPLIER imports (validator uses
# VALIDATION_RULES from strategy_rules as the single source of truth).
from config.strategy_rules import VALIDATION_RULES, STRATEGY_MODES, ANTI_RUNGKAD_CHECKS


def validate_trading_plan(
    setup: dict,
    atr_value: float,
    htf_bias: str = "ranging",
    zone_freshness: str = "fresh",
    min_rr: float = VALIDATION_RULES["min_rr"],
    max_sl_atr_mult: float = VALIDATION_RULES["sl_max_atr_mult"],
    min_sl_atr_mult: float = VALIDATION_RULES["sl_min_atr_mult"],
    strategy_mode: str = "",
    sweep_confirmed: bool = False,
    choch_confirmed: bool = False,
) -> dict:
    """Validate a trading plan against hard rules.

    Args:
        setup: Dict with keys: entry, sl, tp, direction ("buy"/"sell").
        atr_value: Current ATR value.
        htf_bias: "bullish", "bearish", or "ranging".
        zone_freshness: "fresh", "touched", or "mitigated".
        min_rr: Minimum risk:reward ratio (default 1.5).
        max_sl_atr_mult: Max SL distance in ATR (default 2.5).
        min_sl_atr_mult: Min SL distance in ATR (default 0.5).
        strategy_mode: Active strategy mode (e.g. "sniper_confluence").
        sweep_confirmed: Whether a liquidity sweep was detected.
        choch_confirmed: Whether a ChoCh confirmation was detected.

    Returns:
        Dict with the following keys (CON-15: always returns dict, never raises):

        - **passed** (``bool``): ``True`` if no violations found.
        - **violations** (``list[str]``): Hard-fail rule descriptions.
        - **warnings** (``list[str]``): Advisory notes (do not block trade).
        - **risk_reward** (``float``): Computed R:R ratio (0.0 on early exit).
        - **sl_atr_distance** (``float``): SL distance in ATR multiples.

    Note:
        ``min_rr`` defaults to 1.5 (L-35) per masterplan §5.3.
        This is the minimum acceptable risk:reward ratio for any trade.
        Value sourced from ``VALIDATION_RULES["min_rr"]`` in
        ``config/strategy_rules.py`` — the single source of truth.
    """
    violations: list[str] = []
    warnings: list[str] = []

    entry = setup.get("entry", 0.0)
    sl = setup.get("sl", 0.0)
    tp = setup.get("tp", 0.0)
    direction = setup.get("direction", "buy")

    if atr_value <= 0 or math.isnan(atr_value):
        violations.append("ATR value is invalid or zero")
        return _result(False, violations, warnings, 0.0, 0.0)

    # --- Risk / Reward ---
    if direction == "buy":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp

    if risk <= 0:
        violations.append(f"Invalid SL placement: risk={risk:.5f} (must be > 0)")
        return _result(False, violations, warnings, 0.0, 0.0)

    if reward <= 0:
        violations.append(f"Invalid TP placement: reward={reward:.5f} (must be > 0)")
        return _result(False, violations, warnings, 0.0, 0.0)

    rr = reward / risk
    sl_atr = risk / atr_value

    # Rule 1: Minimum R:R
    if rr < min_rr:
        violations.append(f"R:R too low: {rr:.2f} < {min_rr}")

    # Rule 2: SL too tight
    if sl_atr < min_sl_atr_mult:
        violations.append(f"SL too tight: {sl_atr:.2f} × ATR < {min_sl_atr_mult}")

    # Rule 3: SL too wide
    if sl_atr > max_sl_atr_mult:
        violations.append(f"SL too wide: {sl_atr:.2f} × ATR > {max_sl_atr_mult}")

    # Rule 4: Counter-trend (H-12)
    # When VALIDATION_RULES["must_not_counter_htf"] is True, counter-trend
    # is a hard violation (trade is rejected).  When False, it is a warning
    # only (scorer still penalises -3 via counter_htf_bias weight).
    _counter = False
    if direction == "buy" and htf_bias == "bearish":
        _counter = True
        _msg = "Counter-trend: buying against bearish H4 bias"
    elif direction == "sell" and htf_bias == "bullish":
        _counter = True
        _msg = "Counter-trend: selling against bullish H4 bias"

    if _counter:
        if VALIDATION_RULES.get("must_not_counter_htf", False):
            violations.append(_msg)
        else:
            warnings.append(_msg)

    # Rule 5: Zone mitigated
    if zone_freshness == "mitigated":
        violations.append("Zone has been mitigated — do not trade")
    elif zone_freshness == "touched":
        warnings.append("Zone has been touched — reduced reliability")

    # Rule 6 (FIX H-11): Strategy mode enforcement
    # STRATEGY_MODES and ANTI_RUNGKAD_CHECKS are defined in config but
    # were previously only communicated to Gemini via the system prompt.
    # Now we enforce them programmatically.
    if strategy_mode:
        mode_cfg = STRATEGY_MODES.get(strategy_mode)
        if mode_cfg is not None:
            if mode_cfg.get("sweep_required") and not sweep_confirmed:
                violations.append(
                    f"Strategy '{strategy_mode}' requires sweep confirmation"
                )
            if mode_cfg.get("choch_required") and not choch_confirmed:
                violations.append(
                    f"Strategy '{strategy_mode}' requires ChoCh confirmation"
                )

        # Anti-Rungkad mandatory checks
        for check in ANTI_RUNGKAD_CHECKS:
            if strategy_mode in check.get("mandatory_for", []):
                check_id = check["id"]
                if check_id == "liquidity_sweep" and not sweep_confirmed:
                    violations.append(
                        f"Anti-Rungkad '{check_id}' mandatory for "
                        f"'{strategy_mode}' but not confirmed"
                    )
                elif check_id == "choch_confirmation" and not choch_confirmed:
                    violations.append(
                        f"Anti-Rungkad '{check_id}' mandatory for "
                        f"'{strategy_mode}' but not confirmed"
                    )

    passed = len(violations) == 0

    return _result(passed, violations, warnings, round(rr, 3), round(sl_atr, 3))


def _result(
    passed: bool,
    violations: list[str],
    warnings: list[str],
    rr: float,
    sl_atr: float,
) -> dict:
    return {
        "passed": passed,
        "violations": violations,
        "warnings": warnings,
        "risk_reward": rr,
        "sl_atr_distance": sl_atr,
    }
