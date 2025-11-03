from flask import Flask, render_template, request, Blueprint, jsonify, redirect, url_for
import requests
import yfinance as yf
import json
import os
import locale
from pathlib import Path

My_Networth_blueprint = Blueprint('My_Networth_blueprint', __name__)

# Path to the data file
DATA_FILE = Path(__file__).parent.parent / 'data' / 'portfolio.json'

from datetime import datetime

def get_currency_locale(currency):
    """Map currencies to their natural locales for number formatting"""
    currency_locales = {
        'INR': 'en_IN',  # Indian format (lakhs, crores): 1,23,456.78
        'USD': 'en_US',  # US format: 123,456.78
        'EUR': 'de_DE',  # European format: 123.456,78
        'TRY': 'tr_TR'   # Turkish format: 123.456,78
    }
    return currency_locales.get(currency, 'en_US')

def format_currency_value(value, currency):
    """Format a number according to its currency's natural format"""
    try:
        # Save current locale
        old_locale = locale.getlocale()
        # Set locale based on currency
        locale.setlocale(locale.LC_ALL, get_currency_locale(currency))
        # Format number
        formatted = locale.format_string("%.2f", value, grouping=True)
        # Restore original locale
        locale.setlocale(locale.LC_ALL, old_locale)
        return formatted
    except Exception:
        # Fallback to basic formatting if locale operations fail
        return f"{value:,.2f}"


def load_portfolio():
    """Load portfolio data from JSON file"""
    default_portfolio = {
        "stocks": [], 
        "cryptos": [], 
        "savings_loans": [], 
        "currency": "USD",
        "last_updated": None
    }
    
    if not DATA_FILE.exists():
        return default_portfolio
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading portfolio: {e}")
        return default_portfolio

def save_portfolio(portfolio_data):
    """Save portfolio data to JSON file"""
    try:
        DATA_FILE.parent.mkdir(exist_ok=True)
        with open(DATA_FILE, 'w') as f:
            json.dump(portfolio_data, f, indent=4)
    except Exception as e:
        print(f"Error saving portfolio: {e}")

CURRENCY_API_URL = 'https://api.exchangerate-api.com/v4/latest/USD'
# How long (seconds) to consider stored prices fresh before refreshing (every 24 hours)
PRICE_TTL_SECONDS = 60*60*24

def needs_refresh(last_updated_str, ttl_seconds=PRICE_TTL_SECONDS):
    """Return True if last_updated_str is None or older than ttl_seconds."""
    if not last_updated_str:
        return True
    try:
        last = datetime.strptime(last_updated_str, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last).total_seconds() > ttl_seconds
    except Exception:
        return True

def get_real_time_price(symbol, is_crypto=False):
    try:
        # For crypto, append '-USD' to get the USD pair
        ticker_symbol = f"{symbol}-USD" if is_crypto else symbol
        ticker = yf.Ticker(ticker_symbol)
        # Get the current market price
        current_price = ticker.info.get('regularMarketPrice')
        if current_price is None:
            return None
        return float(current_price)
    except Exception as e:
        print(f"Error fetching price for {symbol}: {str(e)}")
        return None

def update_portfolio_prices(stocks, cryptos):
    """Update prices for all stocks and cryptos in the portfolio.
    Store values in USD as the canonical stored amount.
    Returns updated_stocks, updated_cryptos, errors
    """
    updated_stocks = []
    updated_cryptos = []
    errors = []

    # Update stock prices
    for stock in stocks:
        symbol, quantity, curr = stock[0], float(stock[1]), stock[2]
        current_price = get_real_time_price(symbol, is_crypto=False)
        if current_price is not None:
            price_in_usd = current_price * quantity
            updated_stocks.append([symbol, quantity, curr, price_in_usd])
        else:
            # Keep the old stored USD value if we can't fetch a new one
            updated_stocks.append(stock)
            errors.append(f"Could not update price for stock {symbol}")

    # Update crypto prices
    for crypto in cryptos:
        symbol, quantity, curr = crypto[0], float(crypto[1]), crypto[2]
        current_price = get_real_time_price(symbol, is_crypto=True)
        if current_price is not None:
            price_in_usd = current_price * quantity
            updated_cryptos.append([symbol, quantity, curr, price_in_usd])
        else:
            # Keep the old stored USD value if we can't fetch a new one
            updated_cryptos.append(crypto)
            errors.append(f"Could not update price for cryptocurrency {symbol}")

    return updated_stocks, updated_cryptos, errors


@My_Networth_blueprint.route('/api/portfolio', methods=['GET'])
def api_portfolio():
    """Return the portfolio as JSON. Refresh prices if stored data is stale (TTL)."""
    errors = []
    portfolio_data = load_portfolio()
    stocks = portfolio_data.get('stocks', [])
    cryptos = portfolio_data.get('cryptos', [])
    savings_loans = portfolio_data.get('savings_loans', [])
    target_currency = portfolio_data.get('currency', 'USD')

    # Check if we should refresh prices
    if needs_refresh(portfolio_data.get('last_updated')):
        try:
            response = requests.get(CURRENCY_API_URL)
            data = response.json()
            currency_conversion = data['rates']

            updated_stocks, updated_cryptos, price_errors = update_portfolio_prices(stocks, cryptos)
            errors.extend(price_errors)

            portfolio_data['stocks'] = updated_stocks
            portfolio_data['cryptos'] = updated_cryptos
            portfolio_data['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_portfolio(portfolio_data)

            # reflect updated values
            stocks = updated_stocks
            cryptos = updated_cryptos

        except Exception as e:
            errors.append(f"Error refreshing prices: {e}")

    # Determine requested display currency (query param overrides stored currency)
    display_currency = request.args.get('currency') or portfolio_data.get('currency', 'USD')

    # Fetch latest currency rates to convert USD values into display_currency
    try:
        resp = requests.get(CURRENCY_API_URL)
        rates = resp.json().get('rates', {})
    except Exception:
        rates = {}

    def usd_to_target(value_usd, target):
        if value_usd is None:
            return 0
        if not rates or target not in rates:
            # fallback: assume 1:1
            return value_usd
        return value_usd * rates.get(target, 1)

    # Convert stored USD values to display currency for payload
    stocks_out = [[s[0], s[1], s[2], round(usd_to_target(s[3], display_currency), 6)] for s in stocks]
    cryptos_out = [[c[0], c[1], c[2], round(usd_to_target(c[3], display_currency), 6)] for c in cryptos]
    savings_out = []
    for sl in savings_loans:
        # savings_loans stored as [name, value_in_orig_currency, orig_currency, value_usd]
        converted = round(usd_to_target(sl[3], display_currency), 6)
        savings_out.append([sl[0], sl[1], sl[2], converted])

    total_stocks_worth = sum(item[3] for item in stocks_out) if stocks_out else 0
    total_crypto_worth = sum(item[3] for item in cryptos_out) if cryptos_out else 0
    # For savings, totals should sum the values shown in the Value (display_currency) column.
    # If the original currency equals the display currency, use the original entered amount; otherwise use the converted value.
    total_savings_and_loans = 0
    for sl in savings_loans:
        orig_val = sl[1]
        orig_curr = sl[2]
        if orig_curr == display_currency:
            total_savings_and_loans += orig_val
        else:
            total_savings_and_loans += usd_to_target(sl[3], display_currency)
    grand_total_worth = total_stocks_worth + total_crypto_worth + total_savings_and_loans

    # Format last_updated for display (drop seconds)
    lu = portfolio_data.get('last_updated')
    if lu:
        try:
            lu_dt = datetime.strptime(lu, "%Y-%m-%d %H:%M:%S")
            last_updated_display = lu_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_updated_display = lu
    else:
        last_updated_display = None

    payload = {
        'stocks': stocks_out,
        'cryptos': cryptos_out,
        'savings_loans': savings_out,
        'totals': {
            'stocks': total_stocks_worth,
            'cryptos': total_crypto_worth,
            'savings_loans': total_savings_and_loans,
            'grand_total': grand_total_worth
        },
        'currency': display_currency,
        'last_updated': last_updated_display,
        'errors': errors
    }
    return jsonify(payload)


@My_Networth_blueprint.route('/api/portfolio/delete', methods=['POST'])
def api_portfolio_delete():
    """Delete an entry from the portfolio. Expects JSON {category: 'stocks'|'cryptos'|'savings_loans', index: int}"""
    try:
        body = request.get_json(force=True)
        category = body.get('category')
        index = int(body.get('index'))
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400

    if category not in ('stocks', 'cryptos', 'savings_loans'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()
    items = portfolio_data.get(category, [])
    if index < 0 or index >= len(items):
        return jsonify({'error': 'Index out of range'}), 400

    # remove item
    items.pop(index)
    portfolio_data[category] = items
    portfolio_data['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_portfolio(portfolio_data)

    return jsonify({'ok': True, 'portfolio': portfolio_data})


@My_Networth_blueprint.route('/api/portfolio/refresh', methods=['POST'])
def api_portfolio_refresh():
    """Force refresh of stock and crypto prices and save to file. Returns updated portfolio payload."""
    errors = []
    portfolio_data = load_portfolio()
    stocks = portfolio_data.get('stocks', [])
    cryptos = portfolio_data.get('cryptos', [])
    savings_loans = portfolio_data.get('savings_loans', [])
    target_currency = portfolio_data.get('currency', 'USD')

    try:
        # fetch currency rates (we need it only for savings conversion if needed)
        response = requests.get(CURRENCY_API_URL)
        data = response.json()
        currency_conversion = data.get('rates', {})

        updated_stocks, updated_cryptos, price_errors = update_portfolio_prices(stocks, cryptos)
        errors.extend(price_errors)

        portfolio_data['stocks'] = updated_stocks
        portfolio_data['cryptos'] = updated_cryptos
        portfolio_data['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_portfolio(portfolio_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Return the same payload as /api/portfolio
    return api_portfolio()


@My_Networth_blueprint.route('/api/portfolio/update_entry', methods=['POST'])
def api_portfolio_update_entry():
    """Update an entry. Expects JSON with category, index and fields depending on category.
    For stocks/cryptos: {category, index, symbol, quantity, currency}
    For savings_loans: {category, index, name, value, currency}
    """
    try:
        body = request.get_json(force=True)
        category = body.get('category')
        index = int(body.get('index'))
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400

    if category not in ('stocks', 'cryptos', 'savings_loans'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()
    items = portfolio_data.get(category, [])
    if index < 0 or index >= len(items):
        return jsonify({'error': 'Index out of range'}), 400

    target_currency = portfolio_data.get('currency', 'USD')

    # fetch currency rates
    try:
        response = requests.get(CURRENCY_API_URL)
        data = response.json()
        currency_conversion = data['rates']
    except Exception as e:
        return jsonify({'error': f'Could not fetch currency rates: {e}'}), 500

    try:
        if category in ('stocks', 'cryptos'):
            symbol = body.get('symbol', '').strip()
            quantity = float(body.get('quantity'))
            # currency field not required for stocks/cryptos; keep but ignore for USD storage
            curr = body.get('currency', '').strip().upper() or 'USD'
            is_crypto = (category == 'cryptos')
            current_price = get_real_time_price(symbol, is_crypto=is_crypto)
            if current_price is None:
                return jsonify({'error': f'Could not fetch price for {symbol}'}), 500
            price_in_usd = current_price * quantity
            new_entry = [symbol, quantity, curr, price_in_usd]
        else:
            name = body.get('name', '').strip()
            value = float(body.get('value'))
            curr = body.get('currency', '').strip().upper()
            # store canonical USD value
            try:
                value_in_usd = value / currency_conversion[curr]
            except Exception:
                value_in_usd = value
            new_entry = [name, value, curr, value_in_usd]

        items[index] = new_entry
        portfolio_data[category] = items
        portfolio_data['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_portfolio(portfolio_data)

        return jsonify({'ok': True, 'portfolio': portfolio_data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@My_Networth_blueprint.route('/My_Networth_html', methods=['GET', 'POST'])
def calculate_net_worth():
    errors = []
    total_stocks_worth = 0
    total_crypto_worth = 0
    total_savings_and_loans = 0
    grand_total_worth = 0

    # Load existing portfolio
    portfolio_data = load_portfolio()
    stocks = portfolio_data.get('stocks', [])
    cryptos = portfolio_data.get('cryptos', [])
    savings_loans = portfolio_data.get('savings_loans', [])
    target_currency = portfolio_data.get('currency', 'USD')

    # Get currency conversion rates (used when adding/updating savings entries)
    currency_conversion = {}
    target_currency = portfolio_data.get('currency', 'USD')
    try:
        response = requests.get(CURRENCY_API_URL)
        data = response.json()
        currency_conversion = data['rates']
    except Exception as e:
        errors.append(f"Error fetching currency exchange rates: {str(e)}")

    if request.method == 'POST':
        stock_entries = request.form.get('stocks', '').strip()
        crypto_entries = request.form.get('cryptos', '').strip()
        savings_loans_entries = request.form.get('savings_loans', '').strip()
        new_currency = request.form.get('currency', 'USD')

        if new_currency != target_currency:
            target_currency = new_currency
            portfolio_data['currency'] = target_currency

        if not currency_conversion:
            errors.append("Could not retrieve currency conversion rates.")

        try:
            # Process new Stocks
            for stock_entry in stock_entries.splitlines():
                if not stock_entry.strip():  # Skip empty lines
                    continue
                stock = stock_entry.split(',')
                if len(stock) >= 2:
                    symbol = stock[0].strip()
                    quantity = float(stock[1].strip())
                    curr = stock[2].strip().upper() if len(stock) >= 3 else 'USD'
                    current_price = get_real_time_price(symbol, is_crypto=False)
                    if current_price is not None:
                        # store canonical USD value
                        price_in_usd = current_price * quantity
                        stocks.append([symbol, quantity, curr, price_in_usd])
                    else:
                        errors.append(f"Could not fetch price for stock {symbol}")
                else:
                    errors.append(f"Stock entry {stock_entry} is invalid.")

            # Process new Cryptos
            for crypto_entry in crypto_entries.splitlines():
                if not crypto_entry.strip():  # Skip empty lines
                    continue
                crypto = crypto_entry.split(',')
                if len(crypto) >= 2:
                    symbol = crypto[0].strip()
                    quantity = float(crypto[1].strip())
                    curr = crypto[2].strip().upper() if len(crypto) >= 3 else 'USD'
                    current_price = get_real_time_price(symbol, is_crypto=True)
                    if current_price is not None:
                        # store canonical USD value
                        price_in_usd = current_price * quantity
                        cryptos.append([symbol, quantity, curr, price_in_usd])  # Using list instead of tuple
                    else:
                        errors.append(f"Could not fetch price for cryptocurrency {symbol}")
                else:
                    errors.append(f"Crypto entry {crypto_entry} is invalid.")

            # Process new Savings and Loans
            for sl_entry in savings_loans_entries.splitlines():
                if not sl_entry.strip():  # Skip empty lines
                    continue
                sl = sl_entry.split(',')
                if len(sl) == 3:
                    name, value, curr = sl[0].strip(), float(sl[1].strip()), sl[2].strip().upper()
                    # convert original value to USD for canonical storage
                    try:
                        value_in_usd = value / currency_conversion[curr]
                    except Exception:
                        value_in_usd = value
                    savings_loans.append([name, value, curr, value_in_usd])  # Using list instead of tuple
                else:
                    errors.append(f"Savings/Loan entry {sl_entry} is invalid.")

            # Save updated portfolio data with timestamp
            portfolio_data = {
                'stocks': stocks,
                'cryptos': cryptos,
                'savings_loans': savings_loans,
                'currency': target_currency,
                'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            save_portfolio(portfolio_data)

            # Calculate totals
            total_stocks_worth = sum(stock[3] for stock in stocks)
            total_crypto_worth = sum(crypto[3] for crypto in cryptos)
            total_savings_and_loans = sum(sl[3] for sl in savings_loans)
            grand_total_worth = total_stocks_worth + total_crypto_worth + total_savings_and_loans

            # If there were no errors, use POST-Redirect-GET to avoid duplicate submissions on refresh
            if not errors:
                return redirect(url_for('My_Networth_blueprint.calculate_net_worth'))

        except Exception as e:
            errors.append(f"An error occurred: {str(e)}")

    # Add route to clear portfolio
    if request.args.get('clear') == 'true':
        portfolio_data = {"stocks": [], "cryptos": [], "savings_loans": [], "currency": "USD"}
        save_portfolio(portfolio_data)
        return render_template('My_Networth_html.html', errors=[], stocks=[], 
                           cryptos=[], savings_loans=[], total_stocks_worth=0,
                           total_crypto_worth=0, total_savings_and_loans=0,
                           grand_total_worth=0, currency="USD")

    # Calculate totals for display and prepare per-row converted values so template matches API behavior
    try:
        resp = requests.get(CURRENCY_API_URL)
        rates = resp.json().get('rates', {})
    except Exception:
        rates = currency_conversion or {}

    def usd_to_target(value_usd, target):
        if value_usd is None:
            return 0
        if not rates or target not in rates:
            return value_usd
        return value_usd * rates.get(target, 1)

    # Build display rows: stocks/cryptos converted to display currency
    stocks_display = [[s[0], s[1], s[2], round(usd_to_target(s[3], target_currency), 6)] for s in stocks]
    cryptos_display = [[c[0], c[1], c[2], round(usd_to_target(c[3], target_currency), 6)] for c in cryptos]

    # For savings, keep original value and provide converted value for display
    savings_display = []
    for sl in savings_loans:
        converted = round(usd_to_target(sl[3], target_currency), 6)
        savings_display.append([sl[0], sl[1], sl[2], converted])

    total_stocks_worth = sum(item[3] for item in stocks_display) if stocks_display else 0
    total_crypto_worth = sum(item[3] for item in cryptos_display) if cryptos_display else 0
    total_savings_and_loans = 0
    for sl in savings_loans:
        orig_val = sl[1]
        orig_curr = sl[2]
        if orig_curr == target_currency:
            total_savings_and_loans += orig_val
        else:
            total_savings_and_loans += usd_to_target(sl[3], target_currency)

    grand_total_worth = total_stocks_worth + total_crypto_worth + total_savings_and_loans

    # Format last_updated for display (drop seconds)
    lu = portfolio_data.get('last_updated')
    if lu:
        try:
            lu_dt = datetime.strptime(lu, "%Y-%m-%d %H:%M:%S")
            last_updated_display = lu_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_updated_display = lu
    else:
        last_updated_display = None

    return render_template('My_Networth_html.html', errors=errors, stocks=stocks_display, 
                           cryptos=cryptos_display, savings_loans=savings_display, total_stocks_worth=total_stocks_worth,
                           total_crypto_worth=total_crypto_worth, 
                           total_savings_and_loans=total_savings_and_loans, grand_total_worth=grand_total_worth,
                           currency=target_currency, last_updated=last_updated_display,
                           format_currency_value=format_currency_value)

app = Flask(__name__)
app.register_blueprint(My_Networth_blueprint)

# Note: When deploying, ensure to handle the API keys and endpoint URLs securely.
