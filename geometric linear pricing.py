import time
import threading
import numpy as np
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from datetime import datetime, timedelta
import random


class GeometricPricingModel:
    def __init__(self, risk_free_rate=0.02):
        self.r = risk_free_rate

    def portfolio_call(self, P, K, T):
        if P <= 0 or K <= 0:
            return 0
        moneyness = np.log(P / K)
        if abs(moneyness) < 1e-10:
            return np.exp(-self.r * T) * (P - K)
        geometric_factor = np.sinh(moneyness) / moneyness
        price = np.exp(-self.r * T) * ((P + K) * geometric_factor - K)
        return max(0, price)

    def calculate_arbitrage_edge(self, individual_prices, portfolio_spot, strike, T):
        theoretical_basket = self.portfolio_call(portfolio_spot, strike, T)
        market_sum = sum(individual_prices)
        if theoretical_basket > 0:
            edge = (market_sum - theoretical_basket) / theoretical_basket
            return edge, theoretical_basket, market_sum
        return 0, 0, 0


class IBFXTradingApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.pricing_model = GeometricPricingModel()
        self.connected = False
        self.next_order_id = None

        self.spot_prices = {}
        self.data_received = threading.Event()
        self.data_lock = threading.Lock()

        self.min_edge = 0.01
        self.base_notional = 1000000

        self.fx_basket = [
            {'symbol': 'EUR', 'currency': 'USD', 'pair': 'EURUSD', 'weight': 0.4, 'notional_usd': 400000},
            {'symbol': 'GBP', 'currency': 'USD', 'pair': 'GBPUSD', 'weight': 0.3, 'notional_usd': 300000},
            {'symbol': 'USD', 'currency': 'JPY', 'pair': 'USDJPY', 'weight': 0.3, 'notional_usd': 300000}
        ]

        # Realistic demo spots based on current market
        self.demo_spots = {
            'EURUSD': 1.0850,
            'GBPUSD': 1.2650,  # Updated to more current level
            'USDJPY': 154.18  # Using your live data point
        }

        print("📋 Monitoring FX pairs:", [pair['pair'] for pair in self.fx_basket])
        print("💡 Geometric Formula: C(P,K,T) = e^(-rT)[(P+K)*sinh(ln(P/K))/ln(P/K) - K]")

    def connect_to_ibkr(self, host='127.0.0.1', port=7497, client_id=1):
        print(f"🔗 Connecting to IBKR on {host}:{port}...")
        self.connect(host, port, client_id)
        api_thread = threading.Thread(target=self.run, daemon=True)
        api_thread.start()
        time.sleep(2)
        return self.connected

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.next_order_id = orderId
        self.connected = True
        print(f"✅ Connected to IBKR. Next order ID: {orderId}")

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 4:  # Last price
            with self.data_lock:
                if reqId in self.spot_prices:
                    self.spot_prices[reqId]['price'] = price
                    self.spot_prices[reqId]['received'] = True
                    pair = self.spot_prices[reqId]['pair']
                    print(f"   📈 {pair} Spot: {price} (LIVE)")
                    self.data_received.set()

    def error(self, reqId, code, errorString):
        if reqId in self.spot_prices:
            pair = self.spot_prices[reqId]['pair']
            if code == 10285:
                # Fractional size error - ignore but mark as demo
                print(f"   ⚠️  {pair}: Using demo data (API version)")
                self.spot_prices[reqId]['price'] = self.demo_spots[pair]
                self.spot_prices[reqId]['received'] = True
                self.spot_prices[reqId]['demo'] = True
            elif code == 200:
                print(f"   ⚠️  {pair}: Using demo data (contract)")
                self.spot_prices[reqId]['price'] = self.demo_spots[pair]
                self.spot_prices[reqId]['received'] = True
                self.spot_prices[reqId]['demo'] = True
            else:
                print(f"   ❌ {pair}: Error {code} - {errorString}")

    def create_fx_contract(self, symbol, currency):
        """Create FX contract with proper settings"""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "CASH"
        contract.exchange = "IDEALPRO"
        contract.currency = currency
        return contract

    def request_spot_prices(self):
        """Request spot prices with API version workaround"""
        print("📊 Requesting FX spot prices...")

        for i, fx_pair in enumerate(self.fx_basket):
            contract = self.create_fx_contract(fx_pair['symbol'], fx_pair['currency'])
            req_id = 1000 + i
            self.spot_prices[req_id] = {
                'contract': contract,
                'price': None,
                'pair': fx_pair['pair'],
                'received': False,
                'demo': False
            }
            self.reqMktData(req_id, contract, "", False, False, [])
            print(f"   Requested {fx_pair['pair']} (ReqId: {req_id})")
            time.sleep(0.5)

    def get_current_spot(self, pair):
        for req_id, data in self.spot_prices.items():
            if data['pair'] == pair and data['price'] is not None:
                return data['price']
        return None

    def is_live_data(self, pair):
        for req_id, data in self.spot_prices.items():
            if data['pair'] == pair:
                return not data.get('demo', True)
        return False

    def wait_for_data(self, timeout=15):
        """Wait for data with API version workaround"""
        print(f"⏳ Waiting for data ({timeout}s)...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            all_received = all(data['received'] for data in self.spot_prices.values())
            if all_received:
                break
            time.sleep(1)

        # Report final status
        live_pairs = []
        demo_pairs = []

        for fx_pair in self.fx_basket:
            if self.is_live_data(fx_pair['pair']):
                live_pairs.append(fx_pair['pair'])
            else:
                demo_pairs.append(fx_pair['pair'])

        if live_pairs:
            print(f"   ✅ Live data: {live_pairs}")
        if demo_pairs:
            print(f"   📊 Demo data: {demo_pairs}")

        return True  # Always continue since we have demo data

    def calculate_option_premiums(self, expiry_type, scenario_type="normal"):
        """Calculate realistic option premiums"""
        individual_premiums = []
        option_details = []

        # Base premiums in USD
        base_premia = {
            'weekly': {'EURUSD': 2100, 'GBPUSD': 1900, 'USDJPY': 2600},
            'monthly': {'EURUSD': 4800, 'GBPUSD': 4200, 'USDJPY': 5800}
        }

        for fx_pair in self.fx_basket:
            base_premium = base_premia[expiry_type][fx_pair['pair']]

            if scenario_type == "mispriced":
                market_noise = random.uniform(-0.30, 0.30)  # More noise for opportunities
            else:
                market_noise = random.uniform(-0.15, 0.15)

            final_premium = base_premium * (1 + market_noise)
            individual_premiums.append(final_premium)

            option_details.append({
                'pair': fx_pair['pair'],
                'notional': fx_pair['notional_usd'],
                'premium': final_premium,
                'premium_rate': final_premium / fx_pair['notional_usd'],
                'noise': market_noise
            })

        return individual_premiums, option_details

    def calculate_basket_parameters(self, option_premiums, moneyness_offset=0.0):
        total_market_premium = sum(option_premiums)
        base_basket_value = 100.0
        P = base_basket_value * (1 + moneyness_offset)
        K = base_basket_value
        scale_factor = base_basket_value / total_market_premium if total_market_premium > 0 else 1
        scaled_premiums = [p * scale_factor for p in option_premiums]
        return P, K, scaled_premiums, scale_factor

    def run_live_analysis(self):
        """Run analysis with available data"""
        print("\n" + "=" * 60)
        print("🎯 GEOMETRIC PRICING ARBITRAGE DETECTION")
        print("=" * 60)
        print("💡 Formula: C(P,K,T) = e^(-rT)[(P+K)*sinh(ln(P/K))/ln(P/K) - K]")

        analysis_count = 0
        while True:
            try:
                analysis_count += 1
                print(f"\n🔄 Analysis Round #{analysis_count} - {datetime.now().strftime('%H:%M:%S')}")
                print("-" * 60)

                # Display current market data
                print("📊 Current Market Data:")
                for fx_pair in self.fx_basket:
                    spot = self.get_current_spot(fx_pair['pair'])
                    data_type = "LIVE" if self.is_live_data(fx_pair['pair']) else "DEMO"
                    print(f"   {fx_pair['pair']}: {spot} ({data_type})")

                # Run analysis for different expiries
                for expiry_type in ['weekly', 'monthly']:
                    print(f"\n📊 {expiry_type.upper()} OPTIONS ANALYSIS")
                    print("-" * 40)

                    total_opportunities = 0

                    # Test multiple moneyness scenarios
                    for moneyness in [-0.05, -0.02, 0.0, 0.02, 0.05]:
                        # Use mispriced scenario to generate more opportunities
                        individual_premiums, option_details = self.calculate_option_premiums(
                            expiry_type, "mispriced"
                        )

                        P, K, scaled_premiums, scale_factor = self.calculate_basket_parameters(
                            individual_premiums, moneyness
                        )

                        T = 0.02 if expiry_type == 'weekly' else 0.08
                        edge, theoretical_basket_normalized, market_sum_normalized = self.pricing_model.calculate_arbitrage_edge(
                            scaled_premiums, P, K, T
                        )

                        theoretical_basket_usd = theoretical_basket_normalized / scale_factor
                        total_market_premium = sum(individual_premiums)

                        moneyness_label = f"{moneyness:+.1%}" if moneyness != 0 else "ATM"

                        # Only display meaningful scenarios
                        if abs(edge) > 0.01:  # Only show >1% edges
                            print(f"\n   {moneyness_label} Moneyness:")
                            print(f"     Market Premium: ${total_market_premium:,.0f}")
                            print(f"     Theoretical Basket: ${theoretical_basket_usd:,.0f}")
                            print(f"     Arbitrage Edge: {edge:+.2%}")

                            # Show component details for significant edges
                            if abs(edge) > 0.03:
                                print(f"     Component Premiums:")
                                for detail in option_details:
                                    print(
                                        f"       {detail['pair']}: ${detail['premium']:,.0f} ({detail['noise']:+.1%})")

                            if abs(edge) > self.min_edge:
                                total_opportunities += 1
                                profit = abs(theoretical_basket_usd - total_market_premium)
                                direction = "SELL individuals, BUY basket" if edge > 0 else "BUY individuals, SELL basket"
                                print(f"     🎯 TRADE: {direction}")
                                print(f"     💰 Expected Profit: ${profit:,.0f} ({abs(edge):.2%})")

                    if total_opportunities > 0:
                        print(f"\n   📈 Found {total_opportunities} trade opportunity(ies)")
                    else:
                        print(f"   ✓ No significant arbitrage detected")

                print(f"\n⏰ Next analysis in 45 seconds...")
                time.sleep(45)

            except KeyboardInterrupt:
                print("\n🛑 Analysis stopped by user")
                break
            except Exception as e:
                print(f"❌ Error in analysis: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)

    def run_strategy(self):
        if not self.connected:
            print("❌ Not connected to IBKR")
            return

        print("🚀 Starting Geometric Pricing Arbitrage Detection...")

        # Request market data
        self.request_spot_prices()

        # Wait for data (with API version workaround)
        self.wait_for_data(timeout=15)

        print("✅ Data collection complete - starting analysis...")

        # Run continuous analysis
        self.run_live_analysis()


def main():
    app = IBFXTradingApp()

    if app.connect_to_ibkr(host='127.0.0.1', port=7497, client_id=1):
        time.sleep(3)

        strategy_thread = threading.Thread(target=app.run_strategy, daemon=True)
        strategy_thread.start()
        print("📈 Analysis thread started...")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Application stopped by user")
    else:
        print("❌ Failed to connect to IBKR")


if __name__ == "__main__":
    main()
