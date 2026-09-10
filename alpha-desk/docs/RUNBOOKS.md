# Runbooks

Short, rehearsable procedures. Read them before you need them.

---

## 1. Emergency flatten (manual)

**When:** anything unexpected — runaway loop, wrong instrument, broker behaving oddly.

1. Engage the kill switch. From any terminal in the repo:

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from alpha_desk.config.settings import load_settings; from alpha_desk.risk.killswitch import KillSwitch; KillSwitch(load_settings().data_root / 'state').engage('manual emergency', 'HoQR')"
```

   The kill switch is a file (`<data_root>/state/KILL_SWITCH.json`). Creating that file by
   hand works too, and the Risk Kernel refuses every order while it exists, including
   exits — so step 2 is deliberate, not automatic.

2. Flatten positions through the broker terminal or the running process's
   `OrderPath.flatten(reason, state)` (state must be passed so RiskState stays in sync with the broker).
3. Confirm flat in the broker terminal. Do not trust the log alone.
4. Write what happened into the audit log before clearing anything.

**Clearing** requires a named owner and a written reason:

```python
KillSwitch(state_dir).clear(cleared_by="HoQR", written_reason="postmortem complete: <link>")
```

---

## 2. Daily loss halt fired

The desk halts for the session automatically when losses reach
`daily_loss.max_daily_loss_pct` of start-of-day equity.

1. Do not resume the session. The halt expires on the next session roll by design.
2. New entries are refused; exits still pass the kernel so positions can be closed.
3. Before the next session: write a short note on what the losing trades had in common.
4. **Do not increase size to recover.** The risk basis is `min(current, start-of-day)`
   equity, so permitted size has already shrunk. This is intentional.

---

## 3. Drawdown halt fired

Equity fell `drawdown.max_drawdown_halt_pct` from its peak.

1. Stop trading the strategy. This halt does **not** expire with the session.
2. Written postmortem required (`resume_requires_written_signoff: true`).
3. Decide explicitly: resume unchanged, resume at reduced size, open a new trial, or
   retire the strategy. Record the decision in the trial registry.
4. Clearing the halt is a code-level action taken only after the note exists.

---

## 4. Data feed loss / stale heartbeat

Automatic: no heartbeat within `ops.max_heartbeat_age_seconds` (90s) engages
`FEED_LOSS_HALT` and blocks new risk. It clears by itself when the feed returns.

1. Check whether existing positions are still hedged or should be flattened
   (`flatten_on_data_feed_loss: true` is the policy).
2. Do not disable the check to "keep trading". Fix the feed.
3. If the outage was longer than a few minutes, reconcile positions against the broker
   before resuming.

---

## 5. Broker disconnect

1. New risk is blocked while `broker_connected` is false.
2. Reconnect, then **reconcile**: broker positions are the truth, local state is a cache.
3. If local state and broker disagree, stop and investigate before any new order.

---

## 6. Kill switch drill (rehearse monthly, and before Gate G4)

Instrumented script (preferred):

```powershell
.\.venv\Scripts\python.exe scripts\kill_switch_drill.py
```

Manual checklist:

1. During a paper session, engage the kill switch (script step [1], or create the file under `<data_root>/state/KILL_SWITCH.json`).
2. Time how long until the desk is flat. Record the flatten wall-clock the script prints.
3. Confirm the audit log contains the risk refusals and the flatten.
4. Clear with a written reason (`--clear-reason` on the script).
5. Anything that took longer than expected is a bug, not a footnote.

---

## 7. Gate G0 verification

```powershell
.\.venv\Scripts\python.exe scripts\verify_foundation.py
```

Mandatory checks must pass. The two warnings (unsigned risk config, unverified cost
schedule) are HoQR actions, not code defects.

---

## 8. Before the first live order (Gate G5, not yet reachable)

Run `AngelOneBroker.preflight_checklist()` and satisfy every item:

* F&O enabled on the account
* SmartAPI app created; credentials in local `.env` only
* Static IP whitelisted with the broker
* Algo registration completed as required for the account type
* Rate limiter verified against published SmartAPI limits
* Kill-switch drill rehearsed against the live adapter
* `ALPHA_LIVE_GO_SIGNATURE` set to a written HoQR sign-off

Absent any of these, the adapter refuses to construct.
