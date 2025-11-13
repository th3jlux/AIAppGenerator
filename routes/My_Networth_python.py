from flask import Flask, render_template, request, Blueprint, jsonify, redirect, url_for
import requests
import yfinance as yf
import json
import os
import locale
from pathlib import Path

My_Networth_blueprint = Blueprint('My_Networth_blueprint', __name__)

# Path to the data file
DATA_FILE = Path(__file__).parent.parent / 'data' / 'networth.json'

from datetime import datetime

def get_currency_symbol(currency):
    """Map currency codes to their symbols"""
    currency_symbols = {
        'USD': '$',
        'EUR': '€',
        'INR': '₹',
        'TRY': '₺'
    }
    return currency_symbols.get(currency, currency)

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
    """Load networth data from JSON file"""
    default_portfolio = {
        "schema_version": "1.0",
        "currency": "USD",
        "last_updated": None,
        "savings": [],
        "loans": [],
        "real_estate": [],
        "investments": {
            "stocks": [],
            "cryptos": []
        }
    }
    
    if not DATA_FILE.exists():
        return default_portfolio
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading networth data: {e}")
        return default_portfolio

def save_portfolio(portfolio_data):
    """Save networth data to JSON file"""
    try:
        DATA_FILE.parent.mkdir(exist_ok=True)
        portfolio_data['last_updated'] = datetime.now().isoformat() + "Z"
        with open(DATA_FILE, 'w') as f:
            json.dump(portfolio_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving networth data: {e}")

def get_next_id(category: str, existing_items: list) -> str:
    """Generate next sequential ID for a category"""
    existing_ids = [item.get('id', '') for item in existing_items]
    counter = 1
    while True:
        new_id = f"{category}_{counter:03d}"
        if new_id not in existing_ids:
            return new_id
        counter += 1

def usd_to_target(value_usd, target, rates=None, debug=False):
    """Convert a USD value to target currency using provided rates.
    
    Args:
        value_usd: Value in USD to convert
        target: Target currency code (e.g., 'EUR', 'INR')
        rates: Dictionary of currency conversion rates from USD
        debug: Whether to print debug info about conversions
    
    Returns:
        Converted value in target currency
    """
    if value_usd is None:
        return 0
    if not rates or target not in rates:
        if debug:
            print(f"[Currency Rates] Warning: No conversion rate found for {target}, using 1:1 ratio")
        return value_usd
    rate = rates.get(target, 1)
    if debug:
        print(f"[Currency Rates] Converting USD to {target} with rate {rate}")
    return value_usd * rate

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
        symbol = stock['symbol']
        quantity = stock['shares']
        current_price = get_real_time_price(symbol, is_crypto=False)
        if current_price is not None:
            market_value = current_price * quantity
            updated_stock = stock.copy()
            updated_stock['market_value'] = market_value
            updated_stock['last_updated'] = datetime.now().isoformat() + "Z"
            updated_stocks.append(updated_stock)
        else:
            # Keep the old stored value if we can't fetch a new one
            updated_stocks.append(stock)
            errors.append(f"Could not update price for stock {symbol}")

    # Update crypto prices
    for crypto in cryptos:
        symbol = crypto['symbol']
        quantity = crypto['amount']
        current_price = get_real_time_price(symbol, is_crypto=True)
        if current_price is not None:
            market_value = current_price * quantity
            updated_crypto = crypto.copy()
            updated_crypto['market_value'] = market_value
            updated_crypto['last_updated'] = datetime.now().isoformat() + "Z"
            updated_cryptos.append(updated_crypto)
        else:
            # Keep the old stored value if we can't fetch a new one
            updated_cryptos.append(crypto)
            errors.append(f"Could not update price for cryptocurrency {symbol}")

    return updated_stocks, updated_cryptos, errors


@My_Networth_blueprint.route('/api/portfolio', methods=['GET'])
def api_portfolio():
    """Return the networth data as JSON. Refresh prices if stored data is stale (TTL)."""
    errors = []
    portfolio_data = load_portfolio()
    stocks = portfolio_data.get('investments', {}).get('stocks', [])
    cryptos = portfolio_data.get('investments', {}).get('cryptos', [])
    savings = portfolio_data.get('savings', [])
    loans = portfolio_data.get('loans', [])
    real_estate = portfolio_data.get('real_estate', [])
    target_currency = portfolio_data.get('currency', 'USD')

    # Check if we should refresh prices
    if needs_refresh(portfolio_data.get('last_updated')):
        try:
            response = requests.get(CURRENCY_API_URL)
            data = response.json()
            currency_conversion = data['rates']

            updated_stocks, updated_cryptos, price_errors = update_portfolio_prices(stocks, cryptos)
            errors.extend(price_errors)

            portfolio_data['investments']['stocks'] = updated_stocks
            portfolio_data['investments']['cryptos'] = updated_cryptos
            save_portfolio(portfolio_data)

            # reflect updated values
            stocks = updated_stocks
            cryptos = updated_cryptos

        except Exception as e:
            errors.append(f"Error refreshing prices: {e}")

    # Determine requested display currency (query param overrides stored currency)
    display_currency = request.args.get('currency') or portfolio_data.get('currency', 'USD')

    # Fetch latest currency rates to convert values into display_currency
    try:
        resp = requests.get(CURRENCY_API_URL)
        rates = resp.json().get('rates', {})
    except Exception:
        rates = {}

    # Convert values to display currency for payload
    stocks_out = []
    for stock in stocks:
        converted_value = usd_to_target(stock['market_value'], display_currency, rates)
        stocks_out.append({
            'id': stock['id'],
            'symbol': stock['symbol'],
            'shares': stock['shares'],
            'currency': stock['currency'],
            'market_value': round(converted_value, 2)
        })

    cryptos_out = []
    for crypto in cryptos:
        converted_value = usd_to_target(crypto['market_value'], display_currency, rates)
        cryptos_out.append({
            'id': crypto['id'],
            'symbol': crypto['symbol'],
            'amount': crypto['amount'],
            'currency': crypto['currency'],
            'market_value': round(converted_value, 2)
        })

    savings_out = []
    for saving in savings:
        if saving['currency'] == display_currency:
            display_value = saving['balance']
        else:
            display_value = usd_to_target(saving['balance_usd'], display_currency, rates)
        savings_out.append({
            'id': saving['id'],
            'name': saving['name'],
            'balance': saving['balance'],
            'currency': saving['currency'],
            'display_value': round(display_value, 2),
            'institution': saving.get('institution', ''),
            'account_type': saving.get('account_type', 'checking')
        })

    loans_out = []
    for loan in loans:
        if loan['currency'] == display_currency:
            display_value = -loan['outstanding_principal']  # Negative for display
        else:
            display_value = -usd_to_target(loan['outstanding_usd'], display_currency, rates)
        loans_out.append({
            'id': loan['id'],
            'name': loan['name'],
            'outstanding_principal': loan['outstanding_principal'],
            'currency': loan['currency'],
            'display_value': round(display_value, 2),
            'interest_rate': loan.get('interest_rate', 0),
            'loan_type': loan.get('loan_type', ''),
            'linked_property_id': loan.get('linked_property_id'),
            'monthly_payment': loan.get('monthly_payment', 0)
        })

    real_estate_out = []
    for property in real_estate:
        if property['currency'] == display_currency:
            display_value = property['market_value']
        else:
            display_value = usd_to_target(property['market_value_usd'], display_currency, rates)
        
        # Calculate equity in display currency
        equity_display = 0
        if 'computed_equity' in property:
            if property['currency'] == display_currency:
                equity_display = property['computed_equity']
            else:
                equity_display = usd_to_target(property['computed_equity_usd'], display_currency, rates)
        
        real_estate_out.append({
            'id': property['id'],
            'name': property['name'],
            'market_value': property['market_value'],
            'currency': property['currency'],
            'display_value': round(display_value, 2),
            'equity': round(equity_display, 2),
            'property_type': property.get('property_type', ''),
            'address': property.get('address', ''),
            'mortgage_loan_ids': property.get('mortgage_loan_ids', [])
        })

    # Calculate totals
    total_stocks_worth = sum(item['market_value'] for item in stocks_out)
    total_crypto_worth = sum(item['market_value'] for item in cryptos_out)
    total_savings = sum(item['display_value'] for item in savings_out)
    total_loans = sum(item['display_value'] for item in loans_out)  # Already negative
    total_real_estate = sum(item['display_value'] for item in real_estate_out)
    
    net_cash = total_savings + total_loans  # loans are negative
    grand_total_worth = total_stocks_worth + total_crypto_worth + net_cash + total_real_estate

    # Format last_updated for display
    lu = portfolio_data.get('last_updated')
    if lu:
        try:
            # Handle both old format and new ISO format
            if 'T' in lu and lu.endswith('Z'):
                lu_dt = datetime.fromisoformat(lu.replace('Z', '+00:00'))
            else:
                lu_dt = datetime.strptime(lu, "%Y-%m-%d %H:%M:%S")
            last_updated_display = lu_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_updated_display = lu
    else:
        last_updated_display = None

    payload = {
        'stocks': stocks_out,
        'cryptos': cryptos_out,
        'savings': savings_out,
        'loans': loans_out,
        'real_estate': real_estate_out,
        'totals': {
            'stocks': total_stocks_worth,
            'cryptos': total_crypto_worth,
            'savings': total_savings,
            'loans': total_loans,
            'real_estate': total_real_estate,
            'net_cash': net_cash,
            'grand_total': grand_total_worth
        },
        'currency': display_currency,
        'last_updated': last_updated_display,
        'errors': errors
    }
    return jsonify(payload)


@My_Networth_blueprint.route('/api/portfolio/delete', methods=['POST'])
def api_portfolio_delete():
    """Delete an entry from the portfolio. Expects JSON {category: 'stocks'|'cryptos'|'savings'|'loans'|'real_estate', id: str}"""
    try:
        body = request.get_json(force=True)
        category = body.get('category')
        item_id = body.get('id')
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400

    if category not in ('stocks', 'cryptos', 'savings', 'loans', 'real_estate'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()
    
    # Find the correct array based on category
    if category in ('stocks', 'cryptos'):
        items = portfolio_data.get('investments', {}).get(category, [])
        items_updated = [item for item in items if item.get('id') != item_id]
        if len(items_updated) == len(items):
            return jsonify({'error': 'Item not found'}), 404
        portfolio_data['investments'][category] = items_updated
    else:
        items = portfolio_data.get(category, [])
        items_updated = [item for item in items if item.get('id') != item_id]
        if len(items_updated) == len(items):
            return jsonify({'error': 'Item not found'}), 404
        portfolio_data[category] = items_updated

    save_portfolio(portfolio_data)
    return jsonify({'ok': True, 'portfolio': portfolio_data})


@My_Networth_blueprint.route('/api/portfolio/refresh', methods=['POST'])
def api_portfolio_refresh():
    """Force refresh of stock and crypto prices and save to file. Returns updated portfolio payload."""
    errors = []
    portfolio_data = load_portfolio()
    stocks = portfolio_data.get('investments', {}).get('stocks', [])
    cryptos = portfolio_data.get('investments', {}).get('cryptos', [])

    try:
        updated_stocks, updated_cryptos, price_errors = update_portfolio_prices(stocks, cryptos)
        errors.extend(price_errors)

        portfolio_data['investments']['stocks'] = updated_stocks
        portfolio_data['investments']['cryptos'] = updated_cryptos
        save_portfolio(portfolio_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Return the same payload as /api/portfolio
    return api_portfolio()


@My_Networth_blueprint.route('/api/portfolio/add', methods=['POST'])
def api_portfolio_add():
    """Add a new entry to the portfolio. Expects JSON with category and entry data."""
    try:
        body = request.get_json(force=True)
        category = body.get('category')
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400

    if category not in ('stocks', 'cryptos', 'savings', 'loans', 'real_estate'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()

    # Get currency conversion rates
    try:
        response = requests.get(CURRENCY_API_URL)
        rates = response.json().get('rates', {})
    except Exception:
        rates = {}

    try:
        if category == 'stocks':
            symbol = body.get('symbol', '').strip().upper()
            shares = float(body.get('shares'))
            currency = body.get('currency', 'USD').strip().upper()
            
            current_price = get_real_time_price(symbol, is_crypto=False)
            if current_price is None:
                return jsonify({'error': f'Could not fetch price for {symbol}'}), 500
            
            market_value = current_price * shares
            new_entry = {
                'id': get_next_id('stock', portfolio_data.get('investments', {}).get('stocks', [])),
                'symbol': symbol,
                'shares': shares,
                'currency': currency,
                'market_value': market_value,
                'last_updated': datetime.now().isoformat() + "Z"
            }
            
            if 'investments' not in portfolio_data:
                portfolio_data['investments'] = {'stocks': [], 'cryptos': []}
            portfolio_data['investments']['stocks'].append(new_entry)
            
        elif category == 'cryptos':
            symbol = body.get('symbol', '').strip().upper()
            amount = float(body.get('amount'))
            currency = body.get('currency', 'USD').strip().upper()
            
            current_price = get_real_time_price(symbol, is_crypto=True)
            if current_price is None:
                return jsonify({'error': f'Could not fetch price for {symbol}'}), 500
            
            market_value = current_price * amount
            new_entry = {
                'id': get_next_id('crypto', portfolio_data.get('investments', {}).get('cryptos', [])),
                'symbol': symbol,
                'amount': amount,
                'currency': currency,
                'market_value': market_value,
                'last_updated': datetime.now().isoformat() + "Z"
            }
            
            if 'investments' not in portfolio_data:
                portfolio_data['investments'] = {'stocks': [], 'cryptos': []}
            portfolio_data['investments']['cryptos'].append(new_entry)
            
        elif category == 'savings':
            name = body.get('name', '').strip()
            balance = float(body.get('balance'))
            currency = body.get('currency', 'USD').strip().upper()
            institution = body.get('institution', name).strip()
            account_type = body.get('account_type', 'checking').strip()
            
            # Convert to USD for storage
            try:
                balance_usd = balance / rates.get(currency, 1) if currency != 'USD' else balance
            except (ZeroDivisionError, TypeError):
                balance_usd = balance
            
            new_entry = {
                'id': get_next_id('saving', portfolio_data.get('savings', [])),
                'name': name,
                'balance': balance,
                'currency': currency,
                'balance_usd': balance_usd,
                'institution': institution,
                'account_type': account_type,
                'last_updated': datetime.now().isoformat() + "Z"
            }
            
            if 'savings' not in portfolio_data:
                portfolio_data['savings'] = []
            portfolio_data['savings'].append(new_entry)
            
        elif category == 'loans':
            name = body.get('name', '').strip()
            outstanding_principal = float(body.get('outstanding_principal'))
            currency = body.get('currency', 'USD').strip().upper()
            interest_rate = float(body.get('interest_rate', 0))
            loan_type = body.get('loan_type', 'personal').strip()
            lender = body.get('lender', 'Bank').strip()
            monthly_payment = float(body.get('monthly_payment', 0))
            
            # Convert to USD for storage
            try:
                outstanding_usd = outstanding_principal / rates.get(currency, 1) if currency != 'USD' else outstanding_principal
            except (ZeroDivisionError, TypeError):
                outstanding_usd = outstanding_principal
                
            new_entry = {
                'id': get_next_id('loan', portfolio_data.get('loans', [])),
                'name': name,
                'principal_amount': outstanding_principal * 1.2,  # Estimate
                'outstanding_principal': outstanding_principal,
                'currency': currency,
                'outstanding_usd': outstanding_usd,
                'interest_rate': interest_rate,
                'lender': lender,
                'loan_type': loan_type,
                'monthly_payment': monthly_payment,
                'start_date': body.get('start_date', '2023-01-01'),
                'term_months': int(body.get('term_months', 360)),
                'last_updated': datetime.now().isoformat() + "Z"
            }
            
            if 'loans' not in portfolio_data:
                portfolio_data['loans'] = []
            portfolio_data['loans'].append(new_entry)
            
        elif category == 'real_estate':
            name = body.get('name', '').strip()
            market_value = float(body.get('market_value'))
            currency = body.get('currency', 'USD').strip().upper()
            address = body.get('address', '').strip()
            property_type = body.get('property_type', 'residential').strip()
            purchase_price = float(body.get('purchase_price', market_value))
            
            # Convert to USD for storage
            try:
                market_value_usd = market_value / rates.get(currency, 1) if currency != 'USD' else market_value
            except (ZeroDivisionError, TypeError):
                market_value_usd = market_value
                
            new_entry = {
                'id': get_next_id('realestate', portfolio_data.get('real_estate', [])),
                'name': name,
                'market_value': market_value,
                'currency': currency,
                'market_value_usd': market_value_usd,
                'address': address,
                'purchase_price': purchase_price,
                'purchase_date': body.get('purchase_date', '2023-01-01'),
                'property_type': property_type,
                'last_updated': datetime.now().isoformat() + "Z"
            }
            
            if 'real_estate' not in portfolio_data:
                portfolio_data['real_estate'] = []
            portfolio_data['real_estate'].append(new_entry)

        save_portfolio(portfolio_data)
        return jsonify({'ok': True, 'entry': new_entry})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@My_Networth_blueprint.route('/api/portfolio/update', methods=['POST'])
def api_portfolio_update():
    """Update an existing entry in the portfolio. Expects JSON with category, id, and updated fields."""
    try:
        body = request.get_json(force=True)
        category = body.get('category')
        item_id = body.get('id')
    except Exception as e:
        return jsonify({'error': f'Invalid request: {e}'}), 400

    if category not in ('stocks', 'cryptos', 'savings', 'loans', 'real_estate'):
        return jsonify({'error': 'Invalid category'}), 400

    portfolio_data = load_portfolio()

    # Get currency conversion rates
    try:
        response = requests.get(CURRENCY_API_URL)
        rates = response.json().get('rates', {})
    except Exception:
        rates = {}

    # Find the item to update
    items = None
    if category in ('stocks', 'cryptos'):
        items = portfolio_data.get('investments', {}).get(category, [])
    else:
        items = portfolio_data.get(category, [])
    
    item_index = next((i for i, item in enumerate(items) if item.get('id') == item_id), None)
    if item_index is None:
        return jsonify({'error': 'Item not found'}), 404

    try:
        item = items[item_index]
        
        if category == 'stocks':
            if 'symbol' in body:
                item['symbol'] = body['symbol'].strip().upper()
            if 'shares' in body:
                item['shares'] = float(body['shares'])
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
            
            # Refresh price if symbol or shares changed
            if 'symbol' in body or 'shares' in body:
                current_price = get_real_time_price(item['symbol'], is_crypto=False)
                if current_price is not None:
                    item['market_value'] = current_price * item['shares']
                    
        elif category == 'cryptos':
            if 'symbol' in body:
                item['symbol'] = body['symbol'].strip().upper()
            if 'amount' in body:
                item['amount'] = float(body['amount'])
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
                
            # Refresh price if symbol or amount changed
            if 'symbol' in body or 'amount' in body:
                current_price = get_real_time_price(item['symbol'], is_crypto=True)
                if current_price is not None:
                    item['market_value'] = current_price * item['amount']
                    
        elif category == 'savings':
            if 'name' in body:
                item['name'] = body['name'].strip()
            if 'balance' in body:
                item['balance'] = float(body['balance'])
                # Recalculate USD value
                currency = item.get('currency', 'USD')
                try:
                    item['balance_usd'] = item['balance'] / rates.get(currency, 1) if currency != 'USD' else item['balance']
                except (ZeroDivisionError, TypeError):
                    item['balance_usd'] = item['balance']
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
            if 'institution' in body:
                item['institution'] = body['institution'].strip()
            if 'account_type' in body:
                item['account_type'] = body['account_type'].strip()
                
        elif category == 'loans':
            if 'name' in body:
                item['name'] = body['name'].strip()
            if 'outstanding_principal' in body:
                item['outstanding_principal'] = float(body['outstanding_principal'])
                # Recalculate USD value
                currency = item.get('currency', 'USD')
                try:
                    item['outstanding_usd'] = item['outstanding_principal'] / rates.get(currency, 1) if currency != 'USD' else item['outstanding_principal']
                except (ZeroDivisionError, TypeError):
                    item['outstanding_usd'] = item['outstanding_principal']
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
            if 'interest_rate' in body:
                item['interest_rate'] = float(body['interest_rate'])
            if 'monthly_payment' in body:
                item['monthly_payment'] = float(body['monthly_payment'])
            if 'loan_type' in body:
                item['loan_type'] = body['loan_type'].strip()
                
        elif category == 'real_estate':
            if 'name' in body:
                item['name'] = body['name'].strip()
            if 'market_value' in body:
                item['market_value'] = float(body['market_value'])
                # Recalculate USD value
                currency = item.get('currency', 'USD')
                try:
                    item['market_value_usd'] = item['market_value'] / rates.get(currency, 1) if currency != 'USD' else item['market_value']
                except (ZeroDivisionError, TypeError):
                    item['market_value_usd'] = item['market_value']
            if 'currency' in body:
                item['currency'] = body['currency'].strip().upper()
            if 'address' in body:
                item['address'] = body['address'].strip()
            if 'property_type' in body:
                item['property_type'] = body['property_type'].strip()

        # Update timestamp
        item['last_updated'] = datetime.now().isoformat() + "Z"
        
        save_portfolio(portfolio_data)
        return jsonify({'ok': True, 'entry': item})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@My_Networth_blueprint.route('/My_Networth_html', methods=['GET', 'POST'])
def calculate_net_worth():
    errors = []
    
    # Load existing networth data
    portfolio_data = load_portfolio()
    
    # Get display currency from URL parameter or use stored currency as fallback
    display_currency = request.args.get('currency') or portfolio_data.get('currency', 'USD')
    target_currency = portfolio_data.get('currency', 'USD')

    # Get currency conversion rates
    currency_conversion = {}
    try:
        response = requests.get(CURRENCY_API_URL)
        data = response.json()
        currency_conversion = data['rates']
    except Exception as e:
        errors.append(f"Error fetching currency exchange rates: {str(e)}")

    if request.method == 'POST':
        new_currency = request.form.get('currency', 'USD')
        if new_currency != target_currency:
            target_currency = new_currency
            portfolio_data['currency'] = target_currency

        # Process form submissions for different categories
        try:
            # Process stocks
            stock_entries = request.form.get('stocks', '').strip()
            for stock_entry in stock_entries.splitlines():
                if not stock_entry.strip():
                    continue
                parts = stock_entry.split(',')
                if len(parts) >= 2:
                    symbol = parts[0].strip().upper()
                    shares = float(parts[1].strip())
                    currency = parts[2].strip().upper() if len(parts) >= 3 else 'USD'
                    
                    current_price = get_real_time_price(symbol, is_crypto=False)
                    if current_price is not None:
                        market_value = current_price * shares
                        new_entry = {
                            'id': get_next_id('stock', portfolio_data.get('investments', {}).get('stocks', [])),
                            'symbol': symbol,
                            'shares': shares,
                            'currency': currency,
                            'market_value': market_value,
                            'last_updated': datetime.now().isoformat() + "Z"
                        }
                        if 'investments' not in portfolio_data:
                            portfolio_data['investments'] = {'stocks': [], 'cryptos': []}
                        portfolio_data['investments']['stocks'].append(new_entry)
                    else:
                        errors.append(f"Could not fetch price for stock {symbol}")

            # Process cryptos
            crypto_entries = request.form.get('cryptos', '').strip()
            for crypto_entry in crypto_entries.splitlines():
                if not crypto_entry.strip():
                    continue
                parts = crypto_entry.split(',')
                if len(parts) >= 2:
                    symbol = parts[0].strip().upper()
                    amount = float(parts[1].strip())
                    currency = parts[2].strip().upper() if len(parts) >= 3 else 'USD'
                    
                    current_price = get_real_time_price(symbol, is_crypto=True)
                    if current_price is not None:
                        market_value = current_price * amount
                        new_entry = {
                            'id': get_next_id('crypto', portfolio_data.get('investments', {}).get('cryptos', [])),
                            'symbol': symbol,
                            'amount': amount,
                            'currency': currency,
                            'market_value': market_value,
                            'last_updated': datetime.now().isoformat() + "Z"
                        }
                        if 'investments' not in portfolio_data:
                            portfolio_data['investments'] = {'stocks': [], 'cryptos': []}
                        portfolio_data['investments']['cryptos'].append(new_entry)
                    else:
                        errors.append(f"Could not fetch price for cryptocurrency {symbol}")

            # Process savings
            savings_entries = request.form.get('savings', '').strip()
            for savings_entry in savings_entries.splitlines():
                if not savings_entry.strip():
                    continue
                parts = savings_entry.split(',')
                if len(parts) >= 3:
                    name = parts[0].strip()
                    balance = float(parts[1].strip())
                    currency = parts[2].strip().upper()
                    institution = parts[3].strip() if len(parts) >= 4 else name
                    
                    # Convert to USD
                    try:
                        balance_usd = balance / currency_conversion.get(currency, 1) if currency != 'USD' else balance
                    except (ZeroDivisionError, TypeError):
                        balance_usd = balance
                        
                    new_entry = {
                        'id': get_next_id('saving', portfolio_data.get('savings', [])),
                        'name': name,
                        'balance': balance,
                        'currency': currency,
                        'balance_usd': balance_usd,
                        'institution': institution,
                        'account_type': 'checking',
                        'last_updated': datetime.now().isoformat() + "Z"
                    }
                    if 'savings' not in portfolio_data:
                        portfolio_data['savings'] = []
                    portfolio_data['savings'].append(new_entry)

            # Process loans
            loan_entries = request.form.get('loans', '').strip()
            for loan_entry in loan_entries.splitlines():
                if not loan_entry.strip():
                    continue
                parts = loan_entry.split(',')
                if len(parts) >= 3:
                    name = parts[0].strip()
                    outstanding = float(parts[1].strip())
                    currency = parts[2].strip().upper()
                    interest_rate = float(parts[3].strip()) if len(parts) >= 4 else 3.5
                    
                    # Convert to USD
                    try:
                        outstanding_usd = outstanding / currency_conversion.get(currency, 1) if currency != 'USD' else outstanding
                    except (ZeroDivisionError, TypeError):
                        outstanding_usd = outstanding
                        
                    new_entry = {
                        'id': get_next_id('loan', portfolio_data.get('loans', [])),
                        'name': name,
                        'principal_amount': outstanding * 1.2,  # Estimate
                        'outstanding_principal': outstanding,
                        'currency': currency,
                        'outstanding_usd': outstanding_usd,
                        'interest_rate': interest_rate,
                        'lender': 'Bank',
                        'loan_type': 'personal',
                        'monthly_payment': outstanding * 0.01,  # Estimate
                        'start_date': '2023-01-01',
                        'term_months': 360,
                        'last_updated': datetime.now().isoformat() + "Z"
                    }
                    if 'loans' not in portfolio_data:
                        portfolio_data['loans'] = []
                    portfolio_data['loans'].append(new_entry)

            # Process real estate
            realestate_entries = request.form.get('real_estate', '').strip()
            for re_entry in realestate_entries.splitlines():
                if not re_entry.strip():
                    continue
                parts = re_entry.split(',')
                if len(parts) >= 3:
                    name = parts[0].strip()
                    market_value = float(parts[1].strip())
                    currency = parts[2].strip().upper()
                    address = parts[3].strip() if len(parts) >= 4 else 'Not specified'
                    
                    # Convert to USD
                    try:
                        market_value_usd = market_value / currency_conversion.get(currency, 1) if currency != 'USD' else market_value
                    except (ZeroDivisionError, TypeError):
                        market_value_usd = market_value
                        
                    new_entry = {
                        'id': get_next_id('realestate', portfolio_data.get('real_estate', [])),
                        'name': name,
                        'market_value': market_value,
                        'currency': currency,
                        'market_value_usd': market_value_usd,
                        'address': address,
                        'purchase_price': market_value * 0.9,  # Estimate
                        'purchase_date': '2023-01-01',
                        'property_type': 'residential',
                        'last_updated': datetime.now().isoformat() + "Z"
                    }
                    if 'real_estate' not in portfolio_data:
                        portfolio_data['real_estate'] = []
                    portfolio_data['real_estate'].append(new_entry)

            # Save updated data
            save_portfolio(portfolio_data)

            # If no errors, redirect to avoid duplicate submissions
            if not errors:
                return redirect(url_for('My_Networth_blueprint.calculate_net_worth'))

        except Exception as e:
            errors.append(f"An error occurred: {str(e)}")

    # Handle clear request
    if request.args.get('clear') == 'true':
        portfolio_data = load_portfolio()  # Reset to default
        save_portfolio(portfolio_data)
        return render_template('My_Networth_html.html', errors=[], 
                             stocks=[], cryptos=[], savings=[], loans=[], real_estate=[],
                             totals={'stocks': 0, 'cryptos': 0, 'savings': 0, 'loans': 0, 
                                   'real_estate': 0, 'net_cash': 0, 'grand_total': 0},
                             currency="USD", last_updated=None, 
                             format_currency_value=format_currency_value,
                             get_currency_symbol=get_currency_symbol)

    # Prepare display data with currency conversion
    try:
        resp = requests.get(CURRENCY_API_URL)
        rates = resp.json().get('rates', {})
    except Exception:
        rates = currency_conversion or {}

    # Get data for display
    stocks = portfolio_data.get('investments', {}).get('stocks', [])
    cryptos = portfolio_data.get('investments', {}).get('cryptos', [])
    savings = portfolio_data.get('savings', [])
    loans = portfolio_data.get('loans', [])
    real_estate = portfolio_data.get('real_estate', [])

    # Convert for display
    stocks_display = []
    for stock in stocks:
        converted_value = usd_to_target(stock['market_value'], display_currency, rates)
        stocks_display.append({
            'id': stock['id'],
            'symbol': stock['symbol'],
            'shares': stock['shares'],
            'currency': stock['currency'],
            'market_value': round(converted_value, 2)
        })

    cryptos_display = []
    for crypto in cryptos:
        converted_value = usd_to_target(crypto['market_value'], display_currency, rates)
        cryptos_display.append({
            'id': crypto['id'],
            'symbol': crypto['symbol'],
            'amount': crypto['amount'],
            'currency': crypto['currency'],
            'market_value': round(converted_value, 2)
        })

    savings_display = []
    for saving in savings:
        if saving['currency'] == display_currency:
            display_value = saving['balance']
        else:
            display_value = usd_to_target(saving['balance_usd'], display_currency, rates)
        savings_display.append({
            'id': saving['id'],
            'name': saving['name'],
            'balance': saving['balance'],
            'currency': saving['currency'],
            'display_value': round(display_value, 2),
            'institution': saving.get('institution', '')
        })

    loans_display = []
    for loan in loans:
        if loan['currency'] == display_currency:
            display_value = -loan['outstanding_principal']  # Negative for display
        else:
            display_value = -usd_to_target(loan['outstanding_usd'], display_currency, rates)
        loans_display.append({
            'id': loan['id'],
            'name': loan['name'],
            'outstanding_principal': loan['outstanding_principal'],
            'currency': loan['currency'],
            'display_value': round(display_value, 2),
            'interest_rate': loan.get('interest_rate', 0)
        })

    real_estate_display = []
    for property in real_estate:
        if property['currency'] == display_currency:
            display_value = property['market_value']
        else:
            display_value = usd_to_target(property['market_value_usd'], display_currency, rates)
        
        # Calculate equity in display currency - handle missing equity gracefully
        equity_display = 0
        if 'computed_equity' in property and property['computed_equity'] is not None:
            if property['currency'] == display_currency:
                equity_display = property['computed_equity']
            else:
                equity_usd = property.get('computed_equity_usd', 0)
                equity_display = usd_to_target(equity_usd, display_currency, rates)
        else:
            # No mortgage, so equity equals market value
            equity_display = display_value
            
        real_estate_display.append({
            'id': property['id'],
            'name': property['name'],
            'market_value': property['market_value'],
            'currency': property['currency'],
            'display_value': round(display_value, 2),
            'equity': round(equity_display, 2),
            'address': property.get('address', '')
        })

    # Calculate totals
    total_stocks_worth = sum(item['market_value'] for item in stocks_display)
    total_crypto_worth = sum(item['market_value'] for item in cryptos_display)
    total_savings = sum(item['display_value'] for item in savings_display)
    total_loans = sum(item['display_value'] for item in loans_display)  # Already negative
    total_real_estate = sum(item['display_value'] for item in real_estate_display)
    
    net_cash = total_savings + total_loans
    grand_total_worth = total_stocks_worth + total_crypto_worth + net_cash + total_real_estate

    totals = {
        'stocks': total_stocks_worth,
        'cryptos': total_crypto_worth,
        'savings': total_savings,
        'loans': total_loans,
        'real_estate': total_real_estate,
        'net_cash': net_cash,
        'grand_total': grand_total_worth
    }

    # Format last_updated for display
    lu = portfolio_data.get('last_updated')
    if lu:
        try:
            if 'T' in lu and lu.endswith('Z'):
                lu_dt = datetime.fromisoformat(lu.replace('Z', '+00:00'))
            else:
                lu_dt = datetime.strptime(lu, "%Y-%m-%d %H:%M:%S")
            last_updated_display = lu_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_updated_display = lu
    else:
        last_updated_display = None

    return render_template('My_Networth_html.html', errors=errors, 
                         stocks=stocks_display, cryptos=cryptos_display, 
                         savings=savings_display, loans=loans_display, 
                         real_estate=real_estate_display, totals=totals,
                         currency=display_currency, last_updated=last_updated_display,
                         format_currency_value=format_currency_value,
                         get_currency_symbol=get_currency_symbol)

app = Flask(__name__)
app.register_blueprint(My_Networth_blueprint)

# Note: When deploying, ensure to handle the API keys and endpoint URLs securely.
