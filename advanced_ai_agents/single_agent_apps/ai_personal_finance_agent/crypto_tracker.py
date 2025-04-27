import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from collections import deque
import time
import threading
import queue
import functools
from concurrent.futures import ThreadPoolExecutor
import asyncio
import aiohttp

# Custom CSS for gradient styling
st.markdown("""
<style>
    .main {
        background: linear-gradient(135deg, #f5f5f5 0%, #e6e6e6 100%);
    }
    .stTabs [data-baseweb="tab-list"] {
        background: linear-gradient(90deg, #2d1b4d 0%, #4a2b7a 100%);
        border-radius: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        color: #ffffff;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(90deg, #ff7e5f 0%, #feb47b 100%);
        border-radius: 10px;
    }
    .stButton>button {
        background: linear-gradient(90deg, #2d1b4d 0%, #4a2b7a 100%);
        color: white;
        border-radius: 10px;
        border: none;
        padding: 10px 20px;
    }
    .stButton>button:hover {
        background: linear-gradient(90deg, #ff7e5f 0%, #feb47b 100%);
    }
    .stDataFrame {
        background: linear-gradient(135deg, #ffffff 0%, #f5f5f5 100%);
        border-radius: 10px;
        padding: 10px;
    }
    .stPlotlyChart {
        background: linear-gradient(135deg, #ffffff 0%, #f5f5f5 100%);
        border-radius: 10px;
        padding: 10px;
    }
    .crypto-cards {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
        gap: 10px;
        padding: 10px;
    }
    .crypto-card {
        background: linear-gradient(135deg, #ffffff 0%, #f5f5f5 100%);
        border-radius: 10px;
        padding: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        display: flex;
        flex-direction: column;
        height: 100%;
    }
    .crypto-header {
        display: flex;
        align-items: center;
        margin-bottom: 5px;
    }
    .crypto-logo {
        width: 24px;
        height: 24px;
        margin-right: 8px;
        vertical-align: middle;
    }
    .crypto-link {
        color: #2d1b4d;
        text-decoration: none;
        font-weight: bold;
        font-size: 0.9em;
    }
    .crypto-link:hover {
        color: #ff7e5f;
    }
    .crypto-details {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 5px;
        font-size: 0.8em;
    }
    .crypto-detail {
        display: flex;
        flex-direction: column;
    }
    .crypto-label {
        color: #666;
        font-size: 0.8em;
    }
    .crypto-value {
        font-weight: bold;
        color: #2d1b4d;
    }
    .positive-change {
        color: #28a745;
    }
    .negative-change {
        color: #dc3545;
    }
    @media (max-width: 768px) {
        .crypto-cards {
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        }
        .crypto-card {
            padding: 8px;
        }
        .crypto-logo {
            width: 20px;
            height: 20px;
        }
        .crypto-link {
            font-size: 0.8em;
        }
        .crypto-details {
            font-size: 0.7em;
        }
    }
</style>
""", unsafe_allow_html=True)

# Constants
PRODUCTS_URL = "https://api.pro.coinbase.com/products"
COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
WINDOW_SECONDS = 300  # 5 minutes
UPDATE_INTERVAL = 30  # seconds
MAX_WORKERS = 10  # Maximum number of concurrent API calls
MAX_DISPLAY_PRODUCTS = 10  # Maximum number of products to display

# Initialize session state
if 'price_windows' not in st.session_state:
    st.session_state.price_windows = {}
if 'last_update' not in st.session_state:
    st.session_state.last_update = datetime.now()
if 'products_cache' not in st.session_state:
    st.session_state.products_cache = None
if 'products_cache_time' not in st.session_state:
    st.session_state.products_cache_time = None
if 'crypto_logos' not in st.session_state:
    st.session_state.crypto_logos = {}

# Cache decorator
def cache_with_timeout(timeout_seconds):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}_{str(args)}_{str(kwargs)}"
            current_time = time.time()
            
            if (cache_key in st.session_state and 
                current_time - st.session_state.get(f"{cache_key}_time", 0) < timeout_seconds):
                return st.session_state[cache_key]
            
            result = func(*args, **kwargs)
            st.session_state[cache_key] = result
            st.session_state[f"{cache_key}_time"] = current_time
            return result
        return wrapper
    return decorator

@cache_with_timeout(300)  # Cache products for 5 minutes
def get_products():
    """Fetch all product listings from Coinbase Pro and filter for USD pairs."""
    try:
        response = requests.get(PRODUCTS_URL)
        products = response.json()
        return [p for p in products if p.get('quote_currency') == "USD"]
    except Exception as e:
        st.error(f"Error fetching products: {e}")
        return []

async def fetch_price_async(session, product_id):
    """Asynchronously fetch price for a product."""
    ticker_url = f"https://api.pro.coinbase.com/products/{product_id}/ticker"
    try:
        async with session.get(ticker_url) as response:
            data = await response.json()
            return product_id, datetime.now(), float(data['price'])
    except Exception as e:
        st.error(f"Error fetching price for {product_id}: {e}")
        return None

async def fetch_crypto_logo(session, symbol):
    """Fetch cryptocurrency logo from CoinGecko."""
    try:
        url = f"{COINGECKO_API_URL}/coins/{symbol.lower()}"
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return symbol, data.get('image', {}).get('small')
    except Exception as e:
        st.error(f"Error fetching logo for {symbol}: {e}")
    return symbol, None

async def update_prices_async():
    """Asynchronously update prices for all products."""
    products = get_products()
    async with aiohttp.ClientSession() as session:
        # Fetch prices
        price_tasks = [fetch_price_async(session, product['id']) for product in products]
        price_results = await asyncio.gather(*price_tasks)
        
        # Fetch logos for new products
        new_products = [p['id'].split('-')[0] for p in products if p['id'].split('-')[0] not in st.session_state.crypto_logos]
        if new_products:
            logo_tasks = [fetch_crypto_logo(session, symbol) for symbol in new_products]
            logo_results = await asyncio.gather(*logo_tasks)
            for symbol, logo_url in logo_results:
                if logo_url:
                    st.session_state.crypto_logos[symbol] = logo_url
        
        # Update price windows
        for result in price_results:
            if result:
                product_id, current_time, price = result
                if product_id not in st.session_state.price_windows:
                    st.session_state.price_windows[product_id] = deque(maxlen=100)
                update_window(st.session_state.price_windows[product_id], current_time, price)
        
        st.session_state.last_update = datetime.now()

def update_window(window, current_time, price):
    """Update the sliding window with new price data."""
    while window and (current_time - window[0][0]).total_seconds() > WINDOW_SECONDS:
        window.popleft()
    window.append((current_time, price))

def compute_pct_change(window):
    """Compute percentage change from earliest to latest price in window."""
    if not window:
        return None
    baseline_time, baseline_price = window[0]
    current_time, current_price = window[-1]
    pct_change = ((current_price - baseline_price) / baseline_price) * 100
    return baseline_time, baseline_price, current_time, current_price, pct_change

def create_price_chart(product_id, window):
    """Create a Plotly chart for price history with gradient styling."""
    if not window:
        return None
    
    times = [t[0] for t in window]
    prices = [t[1] for t in window]
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=times,
        y=prices,
        mode='lines+markers',
        name=product_id,
        line=dict(
            color='#2d1b4d',
            width=2
        ),
        marker=dict(
            color='#ff7e5f',
            size=8
        )
    ))
    
    fig.update_layout(
        title=dict(
            text=f"{product_id} Price History (Last 5 Minutes)",
            font=dict(color='#2d1b4d')
        ),
        xaxis_title="Time",
        yaxis_title="Price (USD)",
        showlegend=True,
        plot_bgcolor='rgba(245, 245, 245, 0.8)',
        paper_bgcolor='rgba(245, 245, 245, 0.8)',
        font=dict(color='#2d1b4d')
    )
    
    return fig

def update_prices():
    """Background task to update prices using asyncio."""
    while True:
        asyncio.run(update_prices_async())
        time.sleep(UPDATE_INTERVAL)

# Start the background update thread
if 'update_thread' not in st.session_state:
    st.session_state.update_thread = threading.Thread(target=update_prices, daemon=True)
    st.session_state.update_thread.start()

# Streamlit UI
st.title("Cryptocurrency Price Tracker 📈")
st.caption("Real-time tracking of cryptocurrency prices from Coinbase Pro")

# Display last update time with gradient styling
st.sidebar.markdown("""
<div style='background: linear-gradient(90deg, #2d1b4d 0%, #4a2b7a 100%); padding: 10px; border-radius: 10px; color: white;'>
    Last Update: {}</div>
""".format(st.session_state.last_update.strftime('%H:%M:%S')), unsafe_allow_html=True)

# Create tabs for different views
tab1, tab2 = st.tabs(["Price Changes", "Price History"])

with tab1:
    st.header("Top 10 Cryptocurrencies")
    
    # Create a DataFrame for price changes
    changes_data = []
    for product_id, window in st.session_state.price_windows.items():
        computed = compute_pct_change(window)
        if computed:
            base_time, base_price, now_time, now_price, change = computed
            symbol = product_id.split('-')[0]
            logo_url = st.session_state.crypto_logos.get(symbol, '')
            coinbase_url = f"https://www.coinbase.com/price/{symbol.lower()}"
            
            changes_data.append({
                'Product': product_id,
                'Logo': logo_url,
                'Baseline Price': f"${base_price:.2f}",
                'Current Price': f"${now_price:.2f}",
                'Change %': f"{change:.2f}%",
                'Last Update': now_time.strftime('%H:%M:%S'),
                'Link': coinbase_url,
                'Change': change  # For sorting
            })
    
    if changes_data:
        # Sort by absolute change and take top 10
        changes_data.sort(key=lambda x: abs(x['Change']), reverse=True)
        changes_data = changes_data[:MAX_DISPLAY_PRODUCTS]
        
        # Create custom HTML for the table
        html = """
        <div class="crypto-cards">
        """
        for data in changes_data:
            change_class = "positive-change" if float(data['Change %'].strip('%')) > 0 else "negative-change"
            html += f"""
            <div class="crypto-card">
                <div class="crypto-header">
                    <img src="{data['Logo']}" class="crypto-logo" onerror="this.style.display='none'">
                    <a href="{data['Link']}" target="_blank" class="crypto-link">{data['Product']}</a>
                </div>
                <div class="crypto-details">
                    <div class="crypto-detail">
                        <span class="crypto-label">Current</span>
                        <span class="crypto-value">{data['Current Price']}</span>
                    </div>
                    <div class="crypto-detail">
                        <span class="crypto-label">Change</span>
                        <span class="crypto-value {change_class}">{data['Change %']}</span>
                    </div>
                    <div class="crypto-detail">
                        <span class="crypto-label">Baseline</span>
                        <span class="crypto-value">{data['Baseline Price']}</span>
                    </div>
                    <div class="crypto-detail">
                        <span class="crypto-label">Updated</span>
                        <span class="crypto-value">{data['Last Update']}</span>
                    </div>
                </div>
            </div>
            """
        html += "</div>"
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.info("Waiting for price data to accumulate...")

with tab2:
    st.header("Price History Charts")
    
    # Create a grid of price charts
    cols = st.columns(2)
    for idx, (product_id, window) in enumerate(st.session_state.price_windows.items()):
        fig = create_price_chart(product_id, window)
        if fig:
            cols[idx % 2].plotly_chart(fig, use_container_width=True)

# Add a refresh button with gradient styling
if st.button("Refresh Data"):
    st.session_state.last_update = datetime.now()
    st.experimental_rerun() 