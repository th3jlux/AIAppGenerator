"""Net Worth Calculator route.

Tracks savings, loans, real estate, stocks and crypto across multiple
currencies, converts everything to a single display currency using live FX
rates, and records a daily history so net worth can be charted over time.

Design notes
------------
* Money is stored in each item's *native* currency plus a USD snapshot for
  reference, but display values are always recomputed live from the native
  amount using current FX rates, so nothing goes stale.
* Prices are fetched via yfinance ``fast_info`` (fast, robust) and normalised
  to USD, so a stock listed in EUR/GBP is not mis-summed as USD.
* Writes are atomic (temp file + os.replace) and guarded by a lock, so a crash
  or concurrent request can't corrupt the JSON.
* GET endpoints are read-only. Mutations (price refresh, recurring processing,
  add/update/delete) happen only on explicit POSTs or on a full page load.
"""
from flask import (
    Flask, render_template, request, Blueprint, jsonify, redirect, url_for
)
import requests
import yfinance as yf
import json
import os
import re
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

My_Networth_blueprint = Blueprint('My_Networth_blueprint', __name__)

# Path to the data files
DATA_DIR = Path(__file__).parent.parent / 'data'
DATA_FILE = DATA_DIR / 'networth.json'
HISTORY_FILE = DATA_DIR / 'networth_history.json'
LEDGER_FILE = DATA_DIR / 'networth_ledger.json'

CURRENCY_API_URL = 'https://api.exchangerate-api.com/v4/latest/USD'
PRICE_TTL_SECONDS = 60 * 60 * 24      # refresh security prices at most once/day
FX_TTL_SECONDS = 60 * 60             # cache FX rates for an hour

SUPPORTED_CURRENCIES = ('USD', 'EUR', 'INR', 'TRY', 'GBP')

# Serialise all reads/writes of the data file to avoid corruption / races.
_IO_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    """UTC, timezone-aware ISO timestamp (e.g. 2026-07-04T06:55:08.613+00:00)."""
    return now_utc().isoformat()


def parse_iso(value):
    """Parse timestamps written by this module (or the legacy format)."""
    if not value:
        return None
    try:
        v = value.strip()
        # Legacy "Z" suffix -> proper offset
        if v.endswith('Z'):
            v = v[:-1] + '+00:00'
        # Normalise fractional seconds to 6 digits (older fromisoformat is picky)
        m = re.match(r'^(.*T\d{2}:\d{2}:\d{2})\.(\d+)(.*)$', v)
        if m:
            frac = (m.group(2) + '000000')[:6]
            v = f"{m.group(1)}.{frac}{m.group(3)}"
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            # Very old format without timezone
            dt = datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def needs_refresh(last_updated_str, ttl_seconds=PRICE_TTL_SECONDS):
    """True if last_updated is missing or older than ttl_seconds."""
    dt = parse_iso(last_updated_str)
    if dt is None:
        return True
    return (now_utc() - dt).total_seconds() > ttl_seconds


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

def get_currency_symbol(currency):
    return {
        'USD': '$', 'EUR': '€', 'INR': '₹',
        'TRY': '₺', 'GBP': '£',
    }.get(currency, f"{currency} ")


def _group_thousands(digits, sep):
    out = []
    while len(digits) > 3:
        out.insert(0, digits[-3:])
        digits = digits[:-3]
    out.insert(0, digits)
    return sep.join(out)


def _group_indian(digits):
    """1234567 -> 12,34,567 (last three, then groups of two)."""
    if len(digits) <= 3:
        return digits
    last3 = digits[-3:]
    rest = digits[:-3]
    parts = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return ','.join(parts) + ',' + last3


def format_currency_value(value, currency):
    """Format a number in its currency's conventional grouping.

    Locale-independent: does not rely on system locales being installed
    (they usually aren't on slim Linux hosts, which is why the old
    locale-based version silently fell back to US formatting for everything).
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    negative = value < 0
    value = abs(value)
    int_part = str(int(round(value * 100)) // 100)
    frac_part = f"{value:.2f}".split('.')[1]

    if currency == 'INR':
        grouped, dec = _group_indian(int_part), '.'
    elif currency in ('EUR', 'TRY'):
        grouped, dec = _group_thousands(int_part, '.'), ','
    else:  # USD, GBP and anything else
        grouped, dec = _group_thousands(int_part, ','), '.'

    return ('-' if negative else '') + f"{grouped}{dec}{frac_part}"


# ---------------------------------------------------------------------------
# FX rates (cached)
# ---------------------------------------------------------------------------

_FX_CACHE = {'rates': {}, 'fetched_at': None}


def get_fx_rates(force=False):
    """Return USD-based FX rates, cached for FX_TTL_SECONDS.

    exchangerate-api returns ``rates`` where 1 USD = rates[X] units of X.
    On failure we reuse the last successful cache (or an empty dict).
    """
    fetched = _FX_CACHE['fetched_at']
    fresh = fetched and (now_utc() - fetched).total_seconds() < FX_TTL_SECONDS
    if _FX_CACHE['rates'] and fresh and not force:
        return _FX_CACHE['rates']
    try:
        resp = requests.get(CURRENCY_API_URL, timeout=10)
        rates = resp.json().get('rates', {})
        if rates:
            rates.setdefault('USD', 1.0)
            _FX_CACHE['rates'] = rates
            _FX_CACHE['fetched_at'] = now_utc()
    except Exception as e:
        print(f"[FX] Could not refresh rates: {e}")
    return _FX_CACHE['rates']


def convert(amount, from_ccy, to_ccy, rates):
    """Convert between two currencies using USD-based rates."""
    if amount is None:
        return 0.0
    if from_ccy == to_ccy:
        return amount
    if not rates:
        return amount
    from_rate = rates.get(from_ccy)
    to_rate = rates.get(to_ccy)
    if not from_rate or not to_rate:
        return amount  # can't convert; return as-is rather than silently zero
    usd = amount / from_rate  # native -> USD
    return usd * to_rate      # USD -> target


def to_usd(amount, ccy, rates):
    return convert(amount, ccy, 'USD', rates)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def default_portfolio():
    return {
        "schema_version": "1.1",
        "currency": "USD",
        "last_updated": None,
        "savings": [],
        "loans": [],
        "real_estate": [],
        "investments": {"stocks": [], "cryptos": []},
        "recurring_transactions": {"income": [], "expenses": []},
        "goal": None,
    }


def _atomic_write(path: Path, data):
    """Write JSON atomically: temp file in the same dir, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_portfolio():
    with _IO_LOCK:
        if not DATA_FILE.exists():
            return default_portfolio()
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading networth data: {e}")
            return default_portfolio()


def save_portfolio(portfolio_data):
    with _IO_LOCK:
        try:
            portfolio_data['last_updated'] = now_iso()
            _atomic_write(DATA_FILE, portfolio_data)
        except Exception as e:
            print(f"Error saving networth data: {e}")


def load_history():
    with _IO_LOCK:
        if not HISTORY_FILE.exists():
            return []
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            print(f"Error loading history: {e}")
            return []


def load_ledger():
    with _IO_LOCK:
        if not LEDGER_FILE.exists():
            return []
        try:
            with open(LEDGER_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            print(f"Error loading ledger: {e}")
            return []


def append_ledger(entries):
    if not entries:
        return
    with _IO_LOCK:
        ledger = load_ledger()
        ledger.extend(entries)
        try:
            _atomic_write(LEDGER_FILE, ledger)
        except Exception as e:
            print(f"Error saving ledger: {e}")


def backfill_ledger_institutions(portfolio_data=None):
    """Fill account_institution on older ledger entries that predate the field.

    Resolves each entry's account_id against current savings and persists the
    result once so the ledger is complete going forward.
    """
    with _IO_LOCK:
        ledger = load_ledger()
        if not ledger:
            return ledger
        savings = {a['id']: a for a in (portfolio_data or load_portfolio()).get('savings', [])}
        changed = False
        for entry in ledger:
            if not entry.get('account_institution'):
                acct = savings.get(entry.get('account_id'))
                if acct and acct.get('institution'):
                    entry['account_institution'] = acct['institution']
                    changed = True
        if changed:
            try:
                _atomic_write(LEDGER_FILE, ledger)
            except Exception as e:
                print(f"Error backfilling ledger: {e}")
        return ledger


def record_snapshot(totals_usd):
    """Append (or replace) today's net-worth snapshot, stored in USD.

    At most one entry per calendar day so the history stays a clean daily
    series regardless of how often the page is opened.
    """
    with _IO_LOCK:
        history = load_history()
        today = now_utc().date().isoformat()
        entry = {
            'date': today,
            'timestamp': now_iso(),
            'currency': 'USD',
            'grand_total': round(totals_usd.get('grand_total', 0), 2),
            'breakdown': {k: round(v, 2) for k, v in totals_usd.items()},
        }
        if history and history[-1].get('date') == today:
            history[-1] = entry
        else:
            history.append(entry)
        try:
            _atomic_write(HISTORY_FILE, history)
        except Exception as e:
            print(f"Error saving history: {e}")


def get_next_id(category, existing_items):
    existing_ids = {item.get('id', '') for item in existing_items}
    counter = 1
    while True:
        new_id = f"{category}_{counter:03d}"
        if new_id not in existing_ids:
            return new_id
        counter += 1


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    pass


def req_float(body, key, minimum=None, allow_negative=True):
    if key not in body or body[key] in (None, ''):
        raise ValidationError(f"Missing required field: {key}")
    try:
        val = float(body[key])
    except (TypeError, ValueError):
        raise ValidationError(f"'{key}' must be a number")
    if not allow_negative and val < 0:
        raise ValidationError(f"'{key}' must not be negative")
    if minimum is not None and val < minimum:
        raise ValidationError(f"'{key}' must be at least {minimum}")
    return val


def opt_float(body, key, default=0.0):
    if key not in body or body[key] in (None, ''):
        return default
    try:
        return float(body[key])
    except (TypeError, ValueError):
        raise ValidationError(f"'{key}' must be a number")


def req_currency(body, key='currency', default='USD'):
    ccy = (body.get(key) or default).strip().upper()
    return ccy


def req_str(body, key, default=''):
    return (body.get(key) or default).strip()


# ---------------------------------------------------------------------------
# Optional shared-secret guard (off by default)
# ---------------------------------------------------------------------------

def _api_token():
    return os.environ.get('NETWORTH_API_TOKEN')


def token_ok():
    """True if no token is configured, or the request presents the right one."""
    token = _api_token()
    if not token:
        return True
    presented = request.headers.get('X-Networth-Token') or request.form.get('_token')
    return presented == token


def guard():
    """Return a 401 response if the request fails the optional token check."""
    if not token_ok():
        return jsonify({'error': 'Unauthorized'}), 401
    return None


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

# Minor-unit currencies quoted by exchanges (e.g. LSE quotes pence, not pounds)
_MINOR_UNITS = {'GBP': 'GBP', 'GBX': 'GBP', 'ZAC': 'ZAR', 'ILA': 'ILS'}


def get_real_time_price(symbol, is_crypto=False):
    """Return (price, currency) for a symbol, or (None, None) on failure.

    Uses fast_info (much faster and more reliable than .info), falling back
    to a 1-day history close if needed.
    """
    ticker_symbol = f"{symbol}-USD" if is_crypto else symbol
    try:
        ticker = yf.Ticker(ticker_symbol)
        price = None
        ccy = 'USD'
        fi = getattr(ticker, 'fast_info', None)
        if fi is not None:
            for attr in ('last_price', 'lastPrice'):
                try:
                    price = fi[attr] if hasattr(fi, '__getitem__') else None
                except (KeyError, TypeError):
                    price = None
                if price is None:
                    price = getattr(fi, 'last_price', None)
                if price is not None:
                    break
            try:
                ccy = (fi['currency'] if hasattr(fi, '__getitem__') else None) \
                    or getattr(fi, 'currency', None) or 'USD'
            except (KeyError, TypeError):
                ccy = getattr(fi, 'currency', None) or 'USD'
        if price is None:
            hist = ticker.history(period='1d')
            if hist is not None and not hist.empty:
                price = float(hist['Close'].iloc[-1])
        if price is None:
            return None, None
        return float(price), (ccy or 'USD')
    except Exception as e:
        print(f"Error fetching price for {symbol}: {e}")
        return None, None


def price_to_usd(price, ccy, rates):
    """Normalise a quoted price (possibly in a minor unit / foreign ccy) to USD."""
    if price is None:
        return None
    ccy = (ccy or 'USD').upper()
    if ccy in ('GBX', 'GBP') and ccy == 'GBX':
        price /= 100.0
        ccy = 'GBP'
    elif ccy in _MINOR_UNITS and ccy not in ('GBP',):
        price /= 100.0
        ccy = _MINOR_UNITS[ccy]
    if ccy == 'USD':
        return price
    return to_usd(price, ccy, rates)


def update_portfolio_prices(stocks, cryptos, rates):
    """Refresh market values (stored canonically in USD). Returns errors list."""
    errors = []
    for stock in stocks:
        price, ccy = get_real_time_price(stock['symbol'], is_crypto=False)
        usd = price_to_usd(price, ccy, rates)
        if usd is not None:
            stock['market_value'] = usd * stock['shares']
            stock['price_currency'] = ccy
            stock['last_updated'] = now_iso()
        else:
            errors.append(f"Could not update price for stock {stock['symbol']}")
    for crypto in cryptos:
        price, ccy = get_real_time_price(crypto['symbol'], is_crypto=True)
        usd = price_to_usd(price, ccy, rates)
        if usd is not None:
            crypto['market_value'] = usd * crypto['amount']
            crypto['last_updated'] = now_iso()
        else:
            errors.append(f"Could not update price for cryptocurrency {crypto['symbol']}")
    return errors


# ---------------------------------------------------------------------------
# Equity / linking
# ---------------------------------------------------------------------------

def compute_equity_usd(property_item, loans, rates):
    """Equity = market value minus the outstanding balance of linked loans."""
    market_usd = to_usd(property_item['market_value'], property_item['currency'], rates)
    linked_ids = set(property_item.get('mortgage_loan_ids', []))
    owed = 0.0
    for loan in loans:
        if loan['id'] in linked_ids or loan.get('linked_property_id') == property_item['id']:
            owed += to_usd(loan['outstanding_principal'], loan['currency'], rates)
    return market_usd - owed


# ---------------------------------------------------------------------------
# Recurring transactions
# ---------------------------------------------------------------------------

def calculate_next_due_date(current_date_str, frequency):
    current_date = datetime.strptime(current_date_str, "%Y-%m-%d")
    if frequency == "weekly":
        next_date = current_date + timedelta(weeks=1)
    elif frequency == "monthly":
        month = current_date.month + 1
        year = current_date.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        try:
            next_date = current_date.replace(year=year, month=month)
        except ValueError:
            next_date = current_date.replace(year=year, month=month, day=28)
    elif frequency == "quarterly":
        month = current_date.month + 3
        year = current_date.year
        if month > 12:
            year += (month - 1) // 12
            month = ((month - 1) % 12) + 1
        try:
            next_date = current_date.replace(year=year, month=month)
        except ValueError:
            next_date = current_date.replace(year=year, month=month, day=28)
    elif frequency == "yearly":
        try:
            next_date = current_date.replace(year=current_date.year + 1)
        except ValueError:
            next_date = current_date.replace(year=current_date.year + 1, day=28)
    else:
        raise ValidationError(f"Unsupported frequency: {frequency}")
    return next_date.strftime("%Y-%m-%d")


# Frequency -> number of occurrences per month (for steady-state cash flow).
MONTHLY_FACTOR = {'weekly': 52.0 / 12.0, 'monthly': 1.0, 'quarterly': 1.0 / 3.0, 'yearly': 1.0 / 12.0}


def _to_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _month_end(year, month):
    if month >= 12:
        return datetime(year, 12, 31).date()
    return (datetime(year, month + 1, 1) - timedelta(days=1)).date()


def is_active_on(item, on_date):
    """True if the recurring item is active and within its start/end window."""
    if not item.get('is_active', True):
        return False
    start = _to_date(item.get('start_date'))
    end = _to_date(item.get('end_date'))
    if start and start > on_date:
        # Not started yet: still "active" for headline purposes if it starts
        # within a month; callers decide. Here we only gate on end_date.
        pass
    if end and end < on_date:
        return False
    return True


def generate_occurrences(item, horizon_date, from_date=None):
    """List of occurrence dates from `from_date` (default today) up to horizon.

    Respects start_date, end_date and is_active. Never mutates the item and is
    bounded so malformed data can't loop forever.
    """
    if not item.get('is_active', True):
        return []
    freq = item.get('frequency')
    if freq not in MONTHLY_FACTOR:
        return []
    # Anchor on the next unposted occurrence so already-posted periods are never
    # re-projected (which would double-count against updated balances).
    anchor = _to_date(item.get('next_due_date')) or _to_date(item.get('start_date'))
    if anchor is None:
        return []
    end = _to_date(item.get('end_date'))
    window_start = from_date or now_utc().date()

    occurrences = []
    d = anchor
    guard = 0
    # Fast-forward to the first occurrence on/after the window start.
    while d < window_start and guard < 10000:
        nxt = _to_date(calculate_next_due_date(d.strftime("%Y-%m-%d"), freq))
        if nxt is None or nxt <= d:
            return occurrences
        d = nxt
        guard += 1
    # Collect occurrences within the horizon (and before end_date).
    while d <= horizon_date and (end is None or d <= end) and guard < 10000:
        occurrences.append(d)
        nxt = _to_date(calculate_next_due_date(d.strftime("%Y-%m-%d"), freq))
        if nxt is None or nxt <= d:
            break
        d = nxt
        guard += 1
    return occurrences


def due_occurrences(item, as_of):
    """Occurrence dates that are due (on/before as_of) but not yet posted.

    Starts from next_due_date and walks forward by frequency. Returns the list
    of due dates plus the new next_due_date (the first not-yet-due date), so the
    caller can advance the pointer and stay idempotent across runs.
    """
    current_next = item.get('next_due_date') or item.get('start_date')
    if not item.get('is_active', True):
        return [], current_next
    freq = item.get('frequency')
    if freq not in MONTHLY_FACTOR:
        return [], current_next
    d = _to_date(current_next)
    if d is None:
        return [], current_next
    end = _to_date(item.get('end_date'))
    due = []
    guard = 0
    while d <= as_of and (end is None or d <= end) and guard < 10000:
        due.append(d)
        nxt = _to_date(calculate_next_due_date(d.strftime("%Y-%m-%d"), freq))
        if nxt is None or nxt <= d:
            break
        d = nxt
        guard += 1
    return due, d.strftime("%Y-%m-%d")


def apply_due_transactions(portfolio_data):
    """Post all due recurring occurrences to real account balances.

    Catches up every missed period at once (not one/day), honors end_date and
    pause, records a ledger entry per posting, and advances next_due_date so
    re-running is idempotent. Returns the list of new ledger entries.
    """
    rates = get_fx_rates()
    today = now_utc().date()
    accounts = {a['id']: a for a in portfolio_data.get('savings', [])}
    recurring = portfolio_data.get('recurring_transactions', {'income': [], 'expenses': []})
    counter = len(load_ledger())
    new_entries = []

    def process(items, kind, sign, account_field):
        nonlocal counter
        for it in items:
            due, new_next = due_occurrences(it, today)
            if not due:
                continue
            acct = accounts.get(it.get(account_field))
            if not acct:
                # No valid target account: don't post and don't advance, so it
                # can be corrected and applied later rather than silently lost.
                continue
            for d in due:
                delta = sign * convert(it['amount'], it['currency'], acct['currency'], rates)
                acct['balance'] += delta
                acct['balance_usd'] = to_usd(acct['balance'], acct['currency'], rates)
                acct['last_updated'] = now_iso()
                counter += 1
                new_entries.append({
                    'id': f"ledger_{counter:04d}",
                    'date': d.isoformat(),
                    'type': kind,
                    'name': it.get('name', ''),
                    'amount': it['amount'],
                    'currency': it['currency'],
                    'account_id': acct['id'],
                    'account_name': acct.get('name', ''),
                    'account_institution': acct.get('institution', ''),
                    'account_currency': acct['currency'],
                    'delta_account': round(delta, 2),
                    'resulting_balance': round(acct['balance'], 2),
                    'applied_at': now_iso(),
                })
            it['last_processed'] = today.isoformat()
            it['next_due_date'] = new_next

    process(recurring.get('income', []), 'income', +1, 'target_account_id')
    process(recurring.get('expenses', []), 'expense', -1, 'source_account_id')
    append_ledger(new_entries)
    return new_entries


def build_recurring_summary(portfolio_data, rates, display_currency, horizon_months=12):
    """Cash-flow summary, upcoming schedule and net-worth projection.

    Pure/read-only: computes a forecast from recurring items without touching
    any account balance.
    """
    today = now_utc().date()
    income = portfolio_data.get('recurring_transactions', {}).get('income', [])
    expenses = portfolio_data.get('recurring_transactions', {}).get('expenses', [])

    def monthly_rate(items):
        total = 0.0
        for it in items:
            if not is_active_on(it, today):
                continue
            total += convert(it['amount'], it['currency'], display_currency, rates) * MONTHLY_FACTOR.get(it.get('frequency'), 0)
        return total

    monthly_income = monthly_rate(income)
    monthly_expenses = monthly_rate(expenses)

    # Horizon a little past the requested months so month-ends are covered.
    horizon = today + timedelta(days=int(31 * horizon_months) + 5)
    # Forecast strictly the future (tomorrow onward). Anything due up to today is
    # posted to balances by apply_due_transactions and already sits in current_nw,
    # so counting it here too would double it.
    forecast_start = today + timedelta(days=1)

    accounts = {a['id']: a for a in portfolio_data.get('savings', [])}

    def acct_info(item, field):
        a = accounts.get(item.get(field))
        if not a:
            return 'Unknown Account', ''
        return a.get('name', ''), a.get('institution', '')

    events = []  # (date, signed_amount_display, name, type, account_name, institution)
    for it in income:
        amt = convert(it['amount'], it['currency'], display_currency, rates)
        aname, ainst = acct_info(it, 'target_account_id')
        for d in generate_occurrences(it, horizon, from_date=forecast_start):
            events.append((d, amt, it['name'], 'income', aname, ainst))
    for it in expenses:
        amt = convert(it['amount'], it['currency'], display_currency, rates)
        aname, ainst = acct_info(it, 'source_account_id')
        for d in generate_occurrences(it, horizon, from_date=forecast_start):
            events.append((d, -amt, it['name'], 'expense', aname, ainst))
    events.sort(key=lambda e: e[0])

    totals = build_payload(portfolio_data, display_currency, rates)['totals']
    current_nw = totals['grand_total']
    liquid_savings = totals['savings']

    # Month-end projection points.
    projection = [{'date': today.isoformat(), 'net_worth': round(current_nw, 2)}]
    for m in range(1, horizon_months + 1):
        total_month_index = (today.month - 1) + m
        year = today.year + total_month_index // 12
        month = total_month_index % 12 + 1
        boundary = _month_end(year, month)
        cum = sum(amt for (d, amt, *_rest) in events if d <= boundary)
        projection.append({'date': boundary.isoformat(), 'net_worth': round(current_nw + cum, 2)})

    upcoming = [{'date': d.isoformat(), 'name': n, 'amount': round(amt, 2), 'type': t,
                 'account_name': an, 'account_institution': ai}
                for (d, amt, n, t, an, ai) in events if d >= today][:12]

    monthly_net = monthly_income - monthly_expenses
    savings_rate = round(monthly_net / monthly_income * 100, 1) if monthly_income > 0 else None
    runway_months = round(liquid_savings / monthly_expenses, 1) if monthly_expenses > 0 else None

    goal = _goal_status(portfolio_data, current_nw, monthly_net, display_currency, rates, today)

    return {
        'currency': display_currency,
        'monthly_income': round(monthly_income, 2),
        'monthly_expenses': round(monthly_expenses, 2),
        'monthly_net': round(monthly_net, 2),
        'annual_net': round(monthly_net * 12, 2),
        'current_net_worth': round(current_nw, 2),
        'liquid_savings': round(liquid_savings, 2),
        'savings_rate': savings_rate,
        'runway_months': runway_months,
        'goal': goal,
        'projection': projection,
        'upcoming': upcoming,
    }


# ---------------------------------------------------------------------------
# Allocation, cash-flow history and goals
# ---------------------------------------------------------------------------

CURRENCY_REGION = {'USD': 'United States', 'EUR': 'Eurozone', 'GBP': 'United Kingdom',
                   'INR': 'India', 'TRY': 'Turkey'}
CODE_COUNTRY = {'DE': 'Germany', 'IN': 'India', 'LU': 'Luxembourg', 'US': 'United States',
                'GB': 'United Kingdom', 'UK': 'United Kingdom', 'TR': 'Turkey', 'FR': 'France',
                'NL': 'Netherlands', 'ES': 'Spain', 'IT': 'Italy'}


def build_allocation(portfolio_data, rates, display_currency):
    """Asset allocation by class, currency and country/region (display currency)."""
    p = build_payload(portfolio_data, display_currency, rates)

    def bucketize(pairs):
        agg = {}
        for label, value in pairs:
            if value <= 0:
                continue
            agg[label] = agg.get(label, 0.0) + value
        return [{'label': k, 'value': round(v, 2)} for k, v in
                sorted(agg.items(), key=lambda kv: -kv[1])]

    # By asset class, with drill-down items.
    by_class = []
    def add_class(label, items, name_key, value_key):
        arr = [{'name': it.get(name_key) or '—', 'value': round(it[value_key], 2)}
               for it in items if it[value_key] > 0]
        total = sum(x['value'] for x in arr)
        if total > 0:
            by_class.append({'label': label, 'value': round(total, 2),
                             'items': sorted(arr, key=lambda x: -x['value'])})
    add_class('Stocks', p['stocks'], 'symbol', 'market_value')
    add_class('Crypto', p['cryptos'], 'symbol', 'market_value')
    add_class('Cash', [{'name': s.get('institution') or s.get('name'), 'display_value': s['display_value']}
                       for s in p['savings']], 'name', 'display_value')
    add_class('Real Estate', p['real_estate'], 'name', 'display_value')
    by_class.sort(key=lambda c: -c['value'])

    # By currency (native currency of each holding).
    ccy_pairs = []
    for s in p['stocks']:
        ccy_pairs.append(('USD', s['market_value']))
    for c in p['cryptos']:
        ccy_pairs.append(('USD', c['market_value']))
    for a in p['savings']:
        ccy_pairs.append((a['currency'], a['display_value']))
    for r in p['real_estate']:
        ccy_pairs.append((r['currency'], r['display_value']))
    by_currency = bucketize(ccy_pairs)

    # By country/region. Cash uses the account's country code (name) when it looks
    # like one; everything else is grouped by currency region.
    country_pairs = []
    for a in portfolio_data.get('savings', []):
        code = (a.get('name') or '').strip().upper()
        country = CODE_COUNTRY.get(code) or CURRENCY_REGION.get(a['currency'], 'Other')
        country_pairs.append((country, convert(a['balance'], a['currency'], display_currency, rates)))
    for r in p['real_estate']:
        country_pairs.append((CURRENCY_REGION.get(r['currency'], 'Other'), r['display_value']))
    for s in p['stocks']:
        country_pairs.append(('United States', s['market_value']))
    for c in p['cryptos']:
        country_pairs.append(('Global / Crypto', c['market_value']))
    by_country = bucketize(country_pairs)

    return {
        'currency': display_currency,
        'net_worth': p['totals']['grand_total'],
        'total_assets': round(sum(c['value'] for c in by_class), 2),
        'by_class': by_class,
        'by_currency': by_currency,
        'by_country': by_country,
    }


def build_cashflow(portfolio_data, rates, display_currency, months_back=6, months_fwd=6):
    """Per-month income vs expenses: actuals from the ledger, future from forecast."""
    today = now_utc().date()
    income = portfolio_data.get('recurring_transactions', {}).get('income', [])
    expenses = portfolio_data.get('recurring_transactions', {}).get('expenses', [])

    def month_key(d):
        return f"{d.year:04d}-{d.month:02d}"

    # Ordered list of month keys from months_back ago .. months_fwd ahead.
    keys = []
    for offset in range(-months_back, months_fwd + 1):
        idx = (today.year * 12 + today.month - 1) + offset
        keys.append(f"{idx // 12:04d}-{idx % 12 + 1:02d}")
    buckets = {k: {'month': k, 'income': 0.0, 'expenses': 0.0} for k in keys}
    cur_key = month_key(today)

    # Actuals from the ledger (past + current month).
    for e in load_ledger():
        d = _to_date(e['date'])
        if not d:
            continue
        k = month_key(d)
        if k not in buckets:
            continue
        amt = convert(e['amount'], e['currency'], display_currency, rates)
        if e['type'] == 'income':
            buckets[k]['income'] += amt
        else:
            buckets[k]['expenses'] += amt

    # Projected future occurrences (strictly after today).
    horizon = today + timedelta(days=int(31 * months_fwd) + 5)
    fstart = today + timedelta(days=1)
    for it in income:
        amt = convert(it['amount'], it['currency'], display_currency, rates)
        for d in generate_occurrences(it, horizon, from_date=fstart):
            k = month_key(d)
            if k in buckets:
                buckets[k]['income'] += amt
    for it in expenses:
        amt = convert(it['amount'], it['currency'], display_currency, rates)
        for d in generate_occurrences(it, horizon, from_date=fstart):
            k = month_key(d)
            if k in buckets:
                buckets[k]['expenses'] += amt

    out = []
    for k in keys:
        b = buckets[k]
        out.append({'month': k, 'income': round(b['income'], 2), 'expenses': round(b['expenses'], 2),
                    'net': round(b['income'] - b['expenses'], 2),
                    'kind': 'actual' if k <= cur_key else 'projected'})
    return {'currency': display_currency, 'months': out}


def _goal_status(portfolio_data, current_nw, monthly_net, display_currency, rates, today):
    """Net-worth goal progress + projected completion date, or None if unset."""
    goal = portfolio_data.get('goal')
    if not goal or not goal.get('target'):
        return None
    target_display = convert(goal['target'], goal.get('currency', 'USD'), display_currency, rates)
    if target_display <= 0:
        return None
    progress = max(0.0, min(1.0, current_nw / target_display))
    result = {
        'target': round(target_display, 2),
        'target_currency': display_currency,
        'progress': round(progress * 100, 1),
        'reached': current_nw >= target_display,
        'projected_date': None,
        'months_to_go': None,
    }
    if current_nw < target_display and monthly_net > 0:
        import math
        months = math.ceil((target_display - current_nw) / monthly_net)
        idx = (today.year * 12 + today.month - 1) + months
        result['months_to_go'] = months
        result['projected_date'] = f"{idx // 12:04d}-{idx % 12 + 1:02d}"
    return result


# ---------------------------------------------------------------------------
# Core: build the display payload (read-only, live conversion)
# ---------------------------------------------------------------------------

def build_payload(portfolio_data, display_currency, rates):
    """Compute display + USD views of the whole portfolio. No side effects."""
    stocks = portfolio_data.get('investments', {}).get('stocks', [])
    cryptos = portfolio_data.get('investments', {}).get('cryptos', [])
    savings = portfolio_data.get('savings', [])
    loans = portfolio_data.get('loans', [])
    real_estate = portfolio_data.get('real_estate', [])

    stocks_out, cryptos_out = [], []
    stocks_usd = cryptos_usd = 0.0
    for s in stocks:
        usd = s['market_value']  # stored canonically in USD
        stocks_usd += usd
        stocks_out.append({'id': s['id'], 'symbol': s['symbol'], 'shares': s['shares'],
                           'currency': s.get('price_currency', 'USD'),
                           'market_value': round(convert(usd, 'USD', display_currency, rates), 2)})
    for c in cryptos:
        usd = c['market_value']
        cryptos_usd += usd
        cryptos_out.append({'id': c['id'], 'symbol': c['symbol'], 'amount': c['amount'],
                            'currency': 'USD',
                            'market_value': round(convert(usd, 'USD', display_currency, rates), 2)})

    savings_out, savings_usd = [], 0.0
    for a in savings:
        usd = to_usd(a['balance'], a['currency'], rates)
        savings_usd += usd
        savings_out.append({'id': a['id'], 'name': a['name'], 'balance': a['balance'],
                            'currency': a['currency'],
                            'display_value': round(convert(a['balance'], a['currency'], display_currency, rates), 2),
                            'institution': a.get('institution', ''),
                            'account_type': a.get('account_type', 'checking')})

    loans_out, loans_usd = [], 0.0
    for l in loans:
        usd = to_usd(l['outstanding_principal'], l['currency'], rates)
        loans_usd += usd
        loans_out.append({'id': l['id'], 'name': l['name'],
                          'outstanding_principal': l['outstanding_principal'], 'currency': l['currency'],
                          'display_value': round(-convert(l['outstanding_principal'], l['currency'], display_currency, rates), 2),
                          'interest_rate': l.get('interest_rate', 0), 'loan_type': l.get('loan_type', ''),
                          'linked_property_id': l.get('linked_property_id'),
                          'monthly_payment': l.get('monthly_payment')})

    re_out, re_usd = [], 0.0
    for p in real_estate:
        market_usd = to_usd(p['market_value'], p['currency'], rates)
        equity_usd = compute_equity_usd(p, loans, rates)
        re_usd += market_usd
        re_out.append({'id': p['id'], 'name': p['name'], 'market_value': p['market_value'],
                       'currency': p['currency'],
                       'display_value': round(convert(market_usd, 'USD', display_currency, rates), 2),
                       'equity': round(convert(equity_usd, 'USD', display_currency, rates), 2),
                       'property_type': p.get('property_type', ''), 'address': p.get('address', ''),
                       'mortgage_loan_ids': p.get('mortgage_loan_ids', [])})

    def disp(usd):
        return round(convert(usd, 'USD', display_currency, rates), 2)

    net_cash_usd = savings_usd - loans_usd
    grand_usd = stocks_usd + cryptos_usd + net_cash_usd + re_usd

    totals_usd = {'stocks': stocks_usd, 'cryptos': cryptos_usd, 'savings': savings_usd,
                  'loans': -loans_usd, 'real_estate': re_usd, 'net_cash': net_cash_usd,
                  'grand_total': grand_usd}
    totals = {'stocks': disp(stocks_usd), 'cryptos': disp(cryptos_usd), 'savings': disp(savings_usd),
              'loans': disp(-loans_usd), 'real_estate': disp(re_usd), 'net_cash': disp(net_cash_usd),
              'grand_total': disp(grand_usd)}

    lu = parse_iso(portfolio_data.get('last_updated'))
    last_updated_display = lu.strftime("%Y-%m-%d %H:%M UTC") if lu else None

    return {
        'stocks': stocks_out, 'cryptos': cryptos_out, 'savings': savings_out,
        'loans': loans_out, 'real_estate': re_out, 'totals': totals, 'totals_usd': totals_usd,
        'currency': display_currency, 'last_updated': last_updated_display,
    }


def resolve_currency():
    req = (request.args.get('currency') or '').strip().upper()
    if req in SUPPORTED_CURRENCIES:
        return req
    portfolio = None
    return req if req else None


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@My_Networth_blueprint.route('/api/portfolio', methods=['GET'])
def api_portfolio():
    """Read-only portfolio snapshot converted to the requested display currency."""
    portfolio_data = load_portfolio()
    display_currency = (request.args.get('currency') or portfolio_data.get('currency', 'USD')).upper()
    rates = get_fx_rates()
    payload = build_payload(portfolio_data, display_currency, rates)
    payload['errors'] = []
    payload.pop('totals_usd', None)
    return jsonify(payload)


@My_Networth_blueprint.route('/api/portfolio/history', methods=['GET'])
def api_portfolio_history():
    """Net-worth history (stored in USD) converted to the requested currency."""
    display_currency = (request.args.get('currency') or 'USD').upper()
    rates = get_fx_rates()
    history = load_history()

    def conv(v):
        return round(convert(v, 'USD', display_currency, rates), 2)

    series = []
    for h in history:
        bd = h.get('breakdown', {}) or {}
        series.append({
            'date': h['date'],
            'grand_total': conv(h.get('grand_total', 0)),
            'breakdown': {
                'stocks': conv(bd.get('stocks', 0)),
                'cryptos': conv(bd.get('cryptos', 0)),
                'savings': conv(bd.get('savings', 0)),
                'real_estate': conv(bd.get('real_estate', 0)),
                'loans': conv(bd.get('loans', 0)),
            },
        })
    return jsonify({'currency': display_currency, 'history': series})


@My_Networth_blueprint.route('/api/portfolio/allocation', methods=['GET'])
def api_portfolio_allocation():
    """Asset allocation by class (with drill-down), currency and country."""
    portfolio_data = load_portfolio()
    display_currency = (request.args.get('currency') or portfolio_data.get('currency', 'USD')).upper()
    return jsonify(build_allocation(portfolio_data, get_fx_rates(), display_currency))


@My_Networth_blueprint.route('/api/portfolio/cashflow', methods=['GET'])
def api_portfolio_cashflow():
    """Per-month income vs expenses (actual from ledger + projected)."""
    portfolio_data = load_portfolio()
    display_currency = (request.args.get('currency') or portfolio_data.get('currency', 'USD')).upper()
    try:
        back = max(0, min(24, int(request.args.get('back', 6))))
        fwd = max(0, min(24, int(request.args.get('fwd', 6))))
    except (TypeError, ValueError):
        back, fwd = 6, 6
    return jsonify(build_cashflow(portfolio_data, get_fx_rates(), display_currency, back, fwd))


@My_Networth_blueprint.route('/api/goal', methods=['GET', 'POST'])
def api_goal():
    """Get or set the net-worth goal. POST {target, currency} or {target: null} to clear."""
    portfolio_data = load_portfolio()
    if request.method == 'GET':
        return jsonify({'goal': portfolio_data.get('goal')})
    denied = guard()
    if denied:
        return denied
    try:
        body = request.get_json(force=True)
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400
    target = body.get('target')
    if target in (None, '', 0, '0'):
        portfolio_data['goal'] = None
    else:
        try:
            target = float(target)
        except (TypeError, ValueError):
            return jsonify({'error': "'target' must be a number"}), 400
        if target <= 0:
            return jsonify({'error': "'target' must be greater than 0"}), 400
        currency = (body.get('currency') or portfolio_data.get('currency', 'USD')).strip().upper()
        portfolio_data['goal'] = {'target': target, 'currency': currency}
    save_portfolio(portfolio_data)
    return jsonify({'ok': True, 'goal': portfolio_data['goal']})


@My_Networth_blueprint.route('/api/portfolio/refresh', methods=['POST'])
def api_portfolio_refresh():
    """Force-refresh security prices, record a snapshot, return the payload."""
    denied = guard()
    if denied:
        return denied
    portfolio_data = load_portfolio()
    rates = get_fx_rates(force=True)
    stocks = portfolio_data.get('investments', {}).get('stocks', [])
    cryptos = portfolio_data.get('investments', {}).get('cryptos', [])
    errors = update_portfolio_prices(stocks, cryptos, rates)
    save_portfolio(portfolio_data)

    display_currency = (request.args.get('currency') or portfolio_data.get('currency', 'USD')).upper()
    payload = build_payload(portfolio_data, display_currency, rates)
    record_snapshot(payload['totals_usd'])
    payload.pop('totals_usd', None)
    payload['errors'] = errors
    return jsonify(payload)


@My_Networth_blueprint.route('/api/portfolio/delete', methods=['POST'])
def api_portfolio_delete():
    denied = guard()
    if denied:
        return denied
    try:
        body = request.get_json(force=True)
        category = body.get('category')
        item_id = body.get('id')
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400
    if category not in ('stocks', 'cryptos', 'savings', 'loans', 'real_estate'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()
    if category in ('stocks', 'cryptos'):
        items = portfolio_data.get('investments', {}).get(category, [])
        updated = [i for i in items if i.get('id') != item_id]
        if len(updated) == len(items):
            return jsonify({'error': 'Item not found'}), 404
        portfolio_data['investments'][category] = updated
    else:
        items = portfolio_data.get(category, [])
        updated = [i for i in items if i.get('id') != item_id]
        if len(updated) == len(items):
            return jsonify({'error': 'Item not found'}), 404
        # Clean up any dangling property<->loan links
        if category == 'loans':
            for p in portfolio_data.get('real_estate', []):
                if item_id in p.get('mortgage_loan_ids', []):
                    p['mortgage_loan_ids'].remove(item_id)
        portfolio_data[category] = updated

    save_portfolio(portfolio_data)
    return jsonify({'ok': True})


@My_Networth_blueprint.route('/api/portfolio/add', methods=['POST'])
def api_portfolio_add():
    denied = guard()
    if denied:
        return denied
    try:
        body = request.get_json(force=True)
        category = body.get('category')
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400
    if category not in ('stocks', 'cryptos', 'savings', 'loans', 'real_estate'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()
    rates = get_fx_rates()

    try:
        if category == 'stocks':
            symbol = req_str(body, 'symbol').upper()
            if not symbol:
                raise ValidationError('symbol is required')
            shares = req_float(body, 'shares', allow_negative=False)
            price, ccy = get_real_time_price(symbol, is_crypto=False)
            usd = price_to_usd(price, ccy, rates)
            if usd is None:
                return jsonify({'error': f'Could not fetch price for {symbol}'}), 502
            entry = {'id': get_next_id('stock', portfolio_data['investments']['stocks']),
                     'symbol': symbol, 'shares': shares, 'currency': 'USD',
                     'price_currency': ccy, 'market_value': usd * shares,
                     'last_updated': now_iso()}
            portfolio_data['investments']['stocks'].append(entry)

        elif category == 'cryptos':
            symbol = req_str(body, 'symbol').upper()
            if not symbol:
                raise ValidationError('symbol is required')
            amount = req_float(body, 'amount', allow_negative=False)
            price, ccy = get_real_time_price(symbol, is_crypto=True)
            usd = price_to_usd(price, ccy, rates)
            if usd is None:
                return jsonify({'error': f'Could not fetch price for {symbol}'}), 502
            entry = {'id': get_next_id('crypto', portfolio_data['investments']['cryptos']),
                     'symbol': symbol, 'amount': amount, 'currency': 'USD',
                     'market_value': usd * amount, 'last_updated': now_iso()}
            portfolio_data['investments']['cryptos'].append(entry)

        elif category == 'savings':
            name = req_str(body, 'name')
            balance = req_float(body, 'balance')
            currency = req_currency(body)
            entry = {'id': get_next_id('saving', portfolio_data['savings']),
                     'name': name, 'balance': balance, 'currency': currency,
                     'balance_usd': to_usd(balance, currency, rates),
                     'institution': req_str(body, 'institution', name) or name,
                     'account_type': req_str(body, 'account_type', 'checking') or 'checking',
                     'last_updated': now_iso()}
            portfolio_data['savings'].append(entry)

        elif category == 'loans':
            name = req_str(body, 'name')
            outstanding = req_float(body, 'outstanding_principal', allow_negative=False)
            currency = req_currency(body)
            entry = {'id': get_next_id('loan', portfolio_data['loans']),
                     'name': name, 'outstanding_principal': outstanding, 'currency': currency,
                     'outstanding_usd': to_usd(outstanding, currency, rates),
                     'interest_rate': opt_float(body, 'interest_rate', 0.0),
                     'lender': req_str(body, 'lender', 'Bank') or 'Bank',
                     'loan_type': req_str(body, 'loan_type', 'personal') or 'personal',
                     'monthly_payment': opt_float(body, 'monthly_payment', 0.0) or None,
                     'principal_amount': opt_float(body, 'principal_amount', 0.0) or None,
                     'start_date': req_str(body, 'start_date') or None,
                     'term_months': int(opt_float(body, 'term_months', 0)) or None,
                     'linked_property_id': body.get('linked_property_id') or None,
                     'last_updated': now_iso()}
            portfolio_data['loans'].append(entry)
            # Keep the reverse link in sync
            if entry['linked_property_id']:
                for p in portfolio_data['real_estate']:
                    if p['id'] == entry['linked_property_id']:
                        p.setdefault('mortgage_loan_ids', [])
                        if entry['id'] not in p['mortgage_loan_ids']:
                            p['mortgage_loan_ids'].append(entry['id'])

        elif category == 'real_estate':
            name = req_str(body, 'name')
            market_value = req_float(body, 'market_value', allow_negative=False)
            currency = req_currency(body)
            entry = {'id': get_next_id('realestate', portfolio_data['real_estate']),
                     'name': name, 'market_value': market_value, 'currency': currency,
                     'market_value_usd': to_usd(market_value, currency, rates),
                     'address': req_str(body, 'address') or 'Not specified',
                     'purchase_price': opt_float(body, 'purchase_price', 0.0) or None,
                     'purchase_date': req_str(body, 'purchase_date') or None,
                     'property_type': req_str(body, 'property_type', 'residential') or 'residential',
                     'mortgage_loan_ids': body.get('mortgage_loan_ids', []) or [],
                     'last_updated': now_iso()}
            portfolio_data['real_estate'].append(entry)

        save_portfolio(portfolio_data)
        return jsonify({'ok': True, 'entry': entry})
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@My_Networth_blueprint.route('/api/portfolio/update', methods=['POST'])
def api_portfolio_update():
    denied = guard()
    if denied:
        return denied
    try:
        body = request.get_json(force=True)
        category = body.get('category')
        item_id = body.get('id')
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400
    if category not in ('stocks', 'cryptos', 'savings', 'loans', 'real_estate'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()
    rates = get_fx_rates()
    if category in ('stocks', 'cryptos'):
        items = portfolio_data.get('investments', {}).get(category, [])
    else:
        items = portfolio_data.get(category, [])
    idx = next((i for i, it in enumerate(items) if it.get('id') == item_id), None)
    if idx is None:
        return jsonify({'error': 'Item not found'}), 404

    try:
        item = items[idx]
        if category == 'stocks':
            if 'symbol' in body:
                item['symbol'] = body['symbol'].strip().upper()
            if 'shares' in body:
                item['shares'] = req_float(body, 'shares', allow_negative=False)
            if 'symbol' in body or 'shares' in body:
                price, ccy = get_real_time_price(item['symbol'], is_crypto=False)
                usd = price_to_usd(price, ccy, rates)
                if usd is not None:
                    item['market_value'] = usd * item['shares']
                    item['price_currency'] = ccy
        elif category == 'cryptos':
            if 'symbol' in body:
                item['symbol'] = body['symbol'].strip().upper()
            if 'amount' in body:
                item['amount'] = req_float(body, 'amount', allow_negative=False)
            if 'symbol' in body or 'amount' in body:
                price, ccy = get_real_time_price(item['symbol'], is_crypto=True)
                usd = price_to_usd(price, ccy, rates)
                if usd is not None:
                    item['market_value'] = usd * item['amount']
        elif category == 'savings':
            if 'name' in body:
                item['name'] = body['name'].strip()
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
            if 'balance' in body:
                item['balance'] = req_float(body, 'balance')
            item['balance_usd'] = to_usd(item['balance'], item['currency'], rates)
            if 'institution' in body:
                item['institution'] = body['institution'].strip()
            if 'account_type' in body:
                item['account_type'] = body['account_type'].strip()
        elif category == 'loans':
            if 'name' in body:
                item['name'] = body['name'].strip()
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
            if 'outstanding_principal' in body:
                item['outstanding_principal'] = req_float(body, 'outstanding_principal', allow_negative=False)
            item['outstanding_usd'] = to_usd(item['outstanding_principal'], item['currency'], rates)
            if 'interest_rate' in body:
                item['interest_rate'] = opt_float(body, 'interest_rate', item.get('interest_rate', 0))
            if 'monthly_payment' in body:
                item['monthly_payment'] = opt_float(body, 'monthly_payment', 0) or None
            if 'loan_type' in body:
                item['loan_type'] = body['loan_type'].strip()
        elif category == 'real_estate':
            if 'name' in body:
                item['name'] = body['name'].strip()
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
            if 'market_value' in body:
                item['market_value'] = req_float(body, 'market_value', allow_negative=False)
            item['market_value_usd'] = to_usd(item['market_value'], item['currency'], rates)
            if 'address' in body:
                item['address'] = body['address'].strip()
            if 'property_type' in body:
                item['property_type'] = body['property_type'].strip()

        item['last_updated'] = now_iso()
        save_portfolio(portfolio_data)
        return jsonify({'ok': True, 'entry': item})
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Recurring transactions API ---------------------------------------------

@My_Networth_blueprint.route('/api/recurring', methods=['GET'])
def api_recurring_list():
    portfolio_data = load_portfolio()
    recurring = portfolio_data.get('recurring_transactions', {'income': [], 'expenses': []})
    account_map = {a['id']: a['name'] for a in portfolio_data.get('savings', [])}
    for income in recurring['income']:
        income['target_account_name'] = account_map.get(income.get('target_account_id'), 'Unknown Account')
    for expense in recurring['expenses']:
        expense['source_account_name'] = account_map.get(expense.get('source_account_id'), 'Unknown Account')
    return jsonify(recurring)


def _add_recurring(kind, account_field):
    denied = guard()
    if denied:
        return denied
    try:
        body = request.get_json(force=True)
        portfolio_data = load_portfolio()
        for field in ('name', 'amount', 'currency', 'frequency', 'start_date', account_field):
            if field not in body or body[field] in (None, ''):
                return jsonify({'error': f'Missing required field: {field}'}), 400
        if not any(a['id'] == body[account_field] for a in portfolio_data.get('savings', [])):
            return jsonify({'error': 'Account not found'}), 400
        frequency = body['frequency'].strip().lower()
        if frequency not in ('weekly', 'monthly', 'quarterly', 'yearly'):
            return jsonify({'error': f'Unsupported frequency: {frequency}'}), 400
        amount = float(body['amount'])
        if amount <= 0:
            return jsonify({'error': "'amount' must be greater than 0"}), 400
        end_date = body.get('end_date') or None
        start = _to_date(body['start_date'])
        if end_date and start and _to_date(end_date) and _to_date(end_date) < start:
            return jsonify({'error': 'end_date cannot be before start_date'}), 400
        entry = {
            'id': get_next_id(f'recurring_{kind}', portfolio_data['recurring_transactions'][kind + ('s' if kind == 'expense' else '')]),
            'name': body['name'].strip(),
            'amount': amount,
            'currency': body['currency'].strip().upper(),
            'frequency': frequency,
            'start_date': body['start_date'],
            'end_date': end_date,
            'next_due_date': body['start_date'],
            account_field: body[account_field],
            'description': (body.get('description') or '').strip(),
            'is_active': body.get('is_active', True),
            'last_processed': None,
            'created_date': now_iso(),
        }
        if kind == 'expense':
            entry['category'] = (body.get('category') or 'general').strip()
        bucket = 'income' if kind == 'income' else 'expenses'
        portfolio_data['recurring_transactions'][bucket].append(entry)
        save_portfolio(portfolio_data)
        return jsonify({'ok': True, 'entry': entry})
    except ValueError:
        return jsonify({'error': "'amount' must be a number"}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@My_Networth_blueprint.route('/api/recurring/income/add', methods=['POST'])
def api_recurring_income_add():
    return _add_recurring('income', 'target_account_id')


@My_Networth_blueprint.route('/api/recurring/expense/add', methods=['POST'])
def api_recurring_expense_add():
    return _add_recurring('expense', 'source_account_id')


@My_Networth_blueprint.route('/api/recurring/update', methods=['POST'])
def api_recurring_update():
    denied = guard()
    if denied:
        return denied
    try:
        body = request.get_json(force=True)
        transaction_type = body.get('type')
        transaction_id = body.get('id')
        if transaction_type not in ('income', 'expense'):
            return jsonify({'error': 'Invalid transaction type'}), 400
        portfolio_data = load_portfolio()
        bucket = 'income' if transaction_type == 'income' else 'expenses'
        account_field = 'target_account_id' if transaction_type == 'income' else 'source_account_id'
        items = portfolio_data['recurring_transactions'].get(bucket, [])
        item = next((i for i in items if i.get('id') == transaction_id), None)
        if item is None:
            return jsonify({'error': 'Transaction not found'}), 404

        if 'name' in body:
            item['name'] = body['name'].strip()
        if 'amount' in body:
            item['amount'] = float(body['amount'])
        if 'currency' in body:
            item['currency'] = body['currency'].strip().upper()
        if 'description' in body:
            item['description'] = (body.get('description') or '').strip()
        if transaction_type == 'expense' and 'category' in body:
            item['category'] = (body.get('category') or 'general').strip()
        if 'is_active' in body:
            item['is_active'] = bool(body['is_active'])
        if 'end_date' in body:
            item['end_date'] = body['end_date'] or None
        if body.get(account_field):
            if not any(a['id'] == body[account_field] for a in portfolio_data.get('savings', [])):
                return jsonify({'error': 'Account not found'}), 400
            item[account_field] = body[account_field]
        if 'frequency' in body:
            freq = body['frequency'].strip().lower()
            if freq not in ('weekly', 'monthly', 'quarterly', 'yearly'):
                return jsonify({'error': f'Unsupported frequency: {freq}'}), 400
            item['frequency'] = freq
        # If the schedule changed, restart it from the (new) start date.
        if 'start_date' in body and body['start_date']:
            item['start_date'] = body['start_date']
            item['next_due_date'] = body['start_date']
            item['last_processed'] = None

        save_portfolio(portfolio_data)
        return jsonify({'ok': True, 'entry': item})
    except ValueError:
        return jsonify({'error': "'amount' must be a number"}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@My_Networth_blueprint.route('/api/recurring/delete', methods=['POST'])
def api_recurring_delete():
    denied = guard()
    if denied:
        return denied
    try:
        body = request.get_json(force=True)
        transaction_type = body.get('type')
        transaction_id = body.get('id')
        if transaction_type not in ('income', 'expense'):
            return jsonify({'error': 'Invalid transaction type'}), 400
        portfolio_data = load_portfolio()
        bucket = 'income' if transaction_type == 'income' else 'expenses'
        items = portfolio_data['recurring_transactions'].get(bucket, [])
        updated = [i for i in items if i.get('id') != transaction_id]
        if len(updated) == len(items):
            return jsonify({'error': 'Transaction not found'}), 404
        portfolio_data['recurring_transactions'][bucket] = updated
        save_portfolio(portfolio_data)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@My_Networth_blueprint.route('/api/recurring/summary', methods=['GET'])
def api_recurring_summary():
    """Forecast: monthly cash flow, upcoming schedule, and net-worth projection."""
    portfolio_data = load_portfolio()
    display_currency = (request.args.get('currency') or portfolio_data.get('currency', 'USD')).upper()
    rates = get_fx_rates()
    return jsonify(build_recurring_summary(portfolio_data, rates, display_currency))


@My_Networth_blueprint.route('/api/recurring/ledger', methods=['GET'])
def api_recurring_ledger():
    """Posting history (most recent first), with each amount shown in display currency."""
    portfolio_data = load_portfolio()
    display_currency = (request.args.get('currency') or portfolio_data.get('currency', 'USD')).upper()
    rates = get_fx_rates()
    ledger = backfill_ledger_institutions(portfolio_data)
    out = []
    for e in reversed(ledger[-200:]):
        signed_native = e['amount'] * (1 if e['type'] == 'income' else -1)
        out.append({**e, 'account_institution': e.get('account_institution', ''),
                    'display_amount': round(convert(signed_native, e['currency'], display_currency, rates), 2)})
    return jsonify({'currency': display_currency, 'ledger': out})


@My_Networth_blueprint.route('/api/recurring/apply', methods=['POST'])
def api_recurring_apply():
    """Manually trigger posting of any due recurring transactions."""
    denied = guard()
    if denied:
        return denied
    portfolio_data = load_portfolio()
    applied = apply_due_transactions(portfolio_data)
    if applied:
        save_portfolio(portfolio_data)
    return jsonify({'ok': True, 'applied_count': len(applied), 'applied': applied})


# ---------------------------------------------------------------------------
# Legacy textarea form parsing (page POST)
# ---------------------------------------------------------------------------

def _parse_form_lines(portfolio_data, rates, errors):
    """Parse the multi-line textareas on the page form and append entries."""
    inv = portfolio_data.setdefault('investments', {'stocks': [], 'cryptos': []})

    for line in request.form.get('stocks', '').splitlines():
        parts = [p.strip() for p in line.split(',') if p.strip() != '' or True]
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 2:
            symbol = parts[0].upper()
            try:
                shares = float(parts[1])
            except ValueError:
                errors.append(f"Invalid share count for {symbol}")
                continue
            price, ccy = get_real_time_price(symbol, is_crypto=False)
            usd = price_to_usd(price, ccy, rates)
            if usd is None:
                errors.append(f"Could not fetch price for stock {symbol}")
                continue
            inv['stocks'].append({'id': get_next_id('stock', inv['stocks']), 'symbol': symbol,
                                  'shares': shares, 'currency': 'USD', 'price_currency': ccy,
                                  'market_value': usd * shares, 'last_updated': now_iso()})

    for line in request.form.get('cryptos', '').splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 2:
            symbol = parts[0].upper()
            try:
                amount = float(parts[1])
            except ValueError:
                errors.append(f"Invalid amount for {symbol}")
                continue
            price, ccy = get_real_time_price(symbol, is_crypto=True)
            usd = price_to_usd(price, ccy, rates)
            if usd is None:
                errors.append(f"Could not fetch price for cryptocurrency {symbol}")
                continue
            inv['cryptos'].append({'id': get_next_id('crypto', inv['cryptos']), 'symbol': symbol,
                                   'amount': amount, 'currency': 'USD',
                                   'market_value': usd * amount, 'last_updated': now_iso()})

    for line in request.form.get('savings', '').splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 3:
            try:
                balance = float(parts[1])
            except ValueError:
                errors.append(f"Invalid balance for {parts[0]}")
                continue
            currency = parts[2].upper()
            portfolio_data.setdefault('savings', []).append({
                'id': get_next_id('saving', portfolio_data.get('savings', [])),
                'name': parts[0], 'balance': balance, 'currency': currency,
                'balance_usd': to_usd(balance, currency, rates),
                'institution': parts[3] if len(parts) >= 4 else parts[0],
                'account_type': 'checking', 'last_updated': now_iso()})

    for line in request.form.get('loans', '').splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 3:
            try:
                outstanding = float(parts[1])
                interest = float(parts[3]) if len(parts) >= 4 else 0.0
            except ValueError:
                errors.append(f"Invalid number for loan {parts[0]}")
                continue
            currency = parts[2].upper()
            portfolio_data.setdefault('loans', []).append({
                'id': get_next_id('loan', portfolio_data.get('loans', [])),
                'name': parts[0], 'outstanding_principal': outstanding, 'currency': currency,
                'outstanding_usd': to_usd(outstanding, currency, rates),
                'interest_rate': interest, 'lender': 'Bank', 'loan_type': 'personal',
                'monthly_payment': None, 'principal_amount': None, 'start_date': None,
                'term_months': None, 'linked_property_id': None, 'last_updated': now_iso()})

    for line in request.form.get('real_estate', '').splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 3:
            try:
                market_value = float(parts[1])
            except ValueError:
                errors.append(f"Invalid market value for {parts[0]}")
                continue
            currency = parts[2].upper()
            portfolio_data.setdefault('real_estate', []).append({
                'id': get_next_id('realestate', portfolio_data.get('real_estate', [])),
                'name': parts[0], 'market_value': market_value, 'currency': currency,
                'market_value_usd': to_usd(market_value, currency, rates),
                'address': parts[3] if len(parts) >= 4 else 'Not specified',
                'purchase_price': None, 'purchase_date': None, 'property_type': 'residential',
                'mortgage_loan_ids': [], 'last_updated': now_iso()})


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@My_Networth_blueprint.route('/My_Networth_html', methods=['GET', 'POST'])
def calculate_net_worth():
    errors = []

    # Clear: actually reset the stored data to defaults.
    if request.args.get('clear') == 'true':
        save_portfolio(default_portfolio())
        return redirect(url_for('My_Networth_blueprint.calculate_net_worth'))

    portfolio_data = load_portfolio()
    rates = get_fx_rates()
    display_currency = (request.args.get('currency') or portfolio_data.get('currency', 'USD')).upper()

    if request.method == 'POST':
        if not token_ok():
            errors.append('Unauthorized')
        else:
            new_currency = request.form.get('currency', display_currency)
            if new_currency:
                portfolio_data['currency'] = new_currency.upper()
                display_currency = new_currency.upper()
            try:
                _parse_form_lines(portfolio_data, rates, errors)
                save_portfolio(portfolio_data)
                if not errors:
                    return redirect(url_for('My_Networth_blueprint.calculate_net_worth',
                                            currency=display_currency))
            except Exception as e:
                errors.append(f"An error occurred: {e}")

    # Auto-post any due recurring transactions to real balances (idempotent;
    # each posting is recorded in the ledger). Future occurrences remain a
    # forecast via build_recurring_summary.
    try:
        if apply_due_transactions(portfolio_data):
            save_portfolio(portfolio_data)
    except Exception as e:
        errors.append(f"Error applying recurring transactions: {e}")

    # Refresh prices at most once per TTL (guarded, user-initiated navigation).
    if needs_refresh(portfolio_data.get('last_updated')):
        stocks = portfolio_data.get('investments', {}).get('stocks', [])
        cryptos = portfolio_data.get('investments', {}).get('cryptos', [])
        errors.extend(update_portfolio_prices(stocks, cryptos, rates))
        save_portfolio(portfolio_data)

    payload = build_payload(portfolio_data, display_currency, rates)
    record_snapshot(payload['totals_usd'])

    return render_template(
        'My_Networth_html.html', errors=errors,
        stocks=payload['stocks'], cryptos=payload['cryptos'], savings=payload['savings'],
        loans=payload['loans'], real_estate=payload['real_estate'], totals=payload['totals'],
        currency=display_currency, last_updated=payload['last_updated'],
        supported_currencies=SUPPORTED_CURRENCIES,
        format_currency_value=format_currency_value, get_currency_symbol=get_currency_symbol,
    )
