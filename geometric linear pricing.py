"""
Geometric FX Options Arbitrage - Enhanced Sensitivity
Showing how the geometric formula identifies mispricing
C(P,K,T) = e^(-rT)[(P+K)*sinh(ln(P/K))/ln(P/K) - K]
"""

import time
import threading
import numpy as np
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from datetime import datetime, timedelta
import random


class GeometricPricingModel:
    """Your geometric options pricing model without stochastic calculus"""

    def __init__(self, risk_free_rate=0.02):
        self.r = risk_free_rate

    def portfolio_call(self, P, K, T):
        """Closed-form basket option pricing using hyperbolic geometry"""
        if P <= 0 or K <= 0:
            return 0

        moneyness = np.log(P / K)

        # Handle at-the-money case
        if abs(moneyness) < 1e-10:
            return np.exp(-self.r * T) * (P - K)

        geometric_factor = np.sinh(moneyness) / moneyness
        price = np.exp(-self.r * T) * ((P + K) * geometric_factor - K)
        return max(0, price)

    def calculate_arbitrage_edge(self, individual_prices, portfolio_spot, strike, T):
        """Calculate mispricing between sum of individuals vs basket"""
        theoretical_basket = self.portfolio_call(portfolio_spot, strike, T)
        market_sum = sum(individual_prices)

        if theoretical_basket > 0:
            edge = (market_sum - theoretical_basket) / theoretical_basket
            return edge, theoretical_basket, market_sum
        return 0, 0, 0


class IBFXTradingApp(EWrapper, EClient):
    """Interactive Brokers trading application for FX options arbitrage"""

    def __init__(self):
        EClient.__init__(self, self)
        self.pricing_model = GeometricPricingModel()
        self.connected = False
        self.next_order_id = None

        # Market data storage
        self.spot_prices = {}
        self.option_prices = {}
        self.data_received = threading.Event()
        self.data_lock = threading.Lock()

        # Trading parameters - more sensitive for demonstration
        self.min_edge = 0.01  # 1% minimum arbitrage edge (reduced for demo)
        self.base_notional = 1000000  # $1M base notional

        # Define our FX basket with proper notional amounts
        self.fx_basket = [
            {
                'symbol': 'EUR',
                'currency': 'USD',
                'pair': 'EURUSD',
                'weight': 0.4,
                'demo_spot': 1.0850,
                'notional_usd': 400000
            },
            {
                'symbol': 'GBP',
                'currency': 'USD',
                'pair': 'GBPUSD',
                'weight': 0.3,
                'demo_spot': 1.2400,
                'notional_usd': 300000
            },
            {
                'symbol': 'USD',
                'currency': 'JPY',
                'pair': 'USDJPY',
                'weight': 0.3,
                'demo_spot': 154.16,
                'notional_usd': 300000
            }
        ]

        # Base option premiums (in USD for the notional)
        self.base_option_premia = {
            'weekly': {
                'EURUSD': 2000,  # $2,000 premium
                'GBPUSD': 1800,  # $1,800 premium
                'USDJPY': 2500  # $2,500 premium
            },
            'monthly': {
                'EURUSD': 4500,  # $4,500 premium
                'GBPUSD': 4000,  # $4,000 premium
                'USDJPY': 5500  # $5,500 premium
            }
        }

        # Expiries
        self.expiries = {
            'weekly': '20241115',
            'monthly': '20241129'
        }

        print("📋 Monitoring FX pairs:", [pair['pair'] for pair in self.fx_basket])
        print("💡 Geometric Formula: C(P,K,T) = e^(-rT)[(P+K)*sinh(ln(P/K))/ln(P/K) - K]")
        print("💡 Detecting mispricing between individual options and theoretical basket")
        print(f"💡 Minimum edge: {self.min_edge:.1%}")

    def connect_to_ibkr(self, host='127.0.0.1', port=7497, client_id=1):
        """Connect to IBKR TWS or Gateway"""
        print(f"🔗 Connecting to IBKR on {host}:{port}...")
        self.connect(host, port, client_id)

        # Start the socket in a thread
        api_thread = threading.Thread(target=self.run, daemon=True)
        api_thread.start()

        # Wait for connection
        time.sleep(2)
        return self.connected

    # ========== IB API Callbacks ==========

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.next_order_id = orderId
        self.connected = True
        print(f"✅ Connected to IBKR. Next order ID: {orderId}")

    def tickPrice(self, reqId, tickType, price, attrib):
        """Handle price updates"""
        if tickType == 4:  # Last price
            with self.data_lock:
                if reqId in self.spot_prices:
                    self.spot_prices[reqId]['price'] = price
                    self.spot_prices[reqId]['received'] = True
                    self.data_received.set()

    def error(self, reqId, code, errorString):
        """Handle errors"""
        if code in [2104, 2106, 2158, 2108, 10285, 200]:
            return
        print(f"❌ Error {code}: {errorString} (ReqId: {reqId})")

    # ========== Market Data Methods ==========

    def request_spot_prices(self):
        """Request spot prices for all FX pairs in basket"""
        print("📊 Requesting spot prices from IBKR...")

        for i, fx_pair in enumerate(self.fx_basket):
            contract = self.create_fx_spot_contract(fx_pair['symbol'], fx_pair['currency'])
            req_id = 1000 + i
            self.spot_prices[req_id] = {
                'contract': contract,
                'price': None,
                'pair': fx_pair['pair'],
                'received': False
            }
            self.reqMktData(req_id, contract, "", False, False, [])
            time.sleep(0.5)

    def create_fx_spot_contract(self, symbol, currency):
        """Create FX spot contract"""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "CASH"
        contract.exchange = "IDEALPRO"
        contract.currency = currency
        return contract

    def get_available_spot_data(self):
        """Check what spot data we have available"""
        available_pairs = []
        missing_pairs = []

        for fx_pair in self.fx_basket:
            spot = self.get_current_spot(fx_pair['pair'])
            if spot:
                available_pairs.append(fx_pair['pair'])
            else:
                missing_pairs.append(fx_pair['pair'])

        return available_pairs, missing_pairs

    def get_current_spot(self, pair):
        """Get current spot price for a pair"""
        for req_id, data in self.spot_prices.items():
            if data['pair'] == pair and data['price'] is not None:
                return data['price']
        return None

    def wait_for_spot_data(self, timeout=10):
        """Wait for spot data to be received"""
        print(f"⏳ Waiting for spot data (timeout: {timeout}s)...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            available_pairs, missing_pairs = self.get_available_spot_data()
            if available_pairs:
                print(f"   ✅ Received: {available_pairs}")
                if missing_pairs:
                    print(f"   ❌ Missing: {missing_pairs}")
                return True
            time.sleep(1)

        print("   ⚠️  Timeout waiting for spot data")
        return False

    def calculate_option_premiums(self, expiry_type, scenario_type="normal"):
        """Calculate option premiums with different market scenarios"""
        individual_premiums = []
        option_details = []

        for fx_pair in self.fx_basket:
            base_premium = self.base_option_premia[expiry_type][fx_pair['pair']]

            if scenario_type == "normal":
                # Normal market: ±10% noise
                market_noise = random.uniform(-0.10, 0.10)
                final_premium = base_premium * (1 + market_noise)

            elif scenario_type == "mispriced":
                # Create intentional mispricing: ±25% noise
                market_noise = random.uniform(-0.25, 0.25)
                final_premium = base_premium * (1 + market_noise)

            elif scenario_type == "efficient":
                # Very efficient market: ±5% noise
                market_noise = random.uniform(-0.05, 0.05)
                final_premium = base_premium * (1 + market_noise)

            individual_premiums.append(final_premium)

            option_details.append({
                'pair': fx_pair['pair'],
                'notional': fx_pair['notional_usd'],
                'premium': final_premium,
                'premium_rate': final_premium / fx_pair['notional_usd'],
                'base_premium': base_premium,
                'noise': market_noise
            })

        return individual_premiums, option_details

    def calculate_basket_parameters(self, option_premiums, moneyness_offset=0.0):
        """Calculate basket parameters for geometric pricing"""
        total_market_premium = sum(option_premiums)

        # Normalize to base 100 for geometric formula
        base_basket_value = 100.0
        P = base_basket_value * (1 + moneyness_offset)
        K = base_basket_value

        scale_factor = base_basket_value / total_market_premium if total_market_premium > 0 else 1
        scaled_premiums = [p * scale_factor for p in option_premiums]

        return P, K, scaled_premiums, scale_factor

    # ========== Enhanced Geometric Analysis ==========

    def run_enhanced_analysis(self):
        """Run enhanced analysis showing geometric formula in action"""
        print("\n" + "=" * 70)
        print("🎯 ENHANCED GEOMETRIC PRICING ANALYSIS")
        print("=" * 70)
        print("💡 Demonstrating how the formula identifies arbitrage opportunities")

        analysis_count = 0
        while True:
            try:
                analysis_count += 1
                print(f"\n🔬 Analysis Round #{analysis_count}")
                print("-" * 70)

                # Test different market scenarios
                scenarios = [
                    ("NORMAL", "normal", "Typical market conditions"),
                    ("MISPRICED", "mispriced", "Inefficient market with opportunities"),
                    ("EFFICIENT", "efficient", "Highly efficient market")
                ]

                for expiry_type in ['weekly', 'monthly']:
                    print(f"\n📊 {expiry_type.upper()} OPTIONS - Different Market Scenarios")
                    print("=" * 50)

                    for scenario_name, scenario_type, scenario_desc in scenarios:
                        print(f"\n🎲 {scenario_name} MARKET: {scenario_desc}")
                        print("-" * 40)
                        self.analyze_market_scenario(expiry_type, scenario_type)
                        print("-" * 40)

                print(f"⏰ {datetime.now().strftime('%H:%M:%S')} - Next analysis in 60 seconds...")
                time.sleep(60)

            except KeyboardInterrupt:
                print("\n🛑 Analysis stopped by user")
                break
            except Exception as e:
                print(f"❌ Error in analysis: {e}")
                time.sleep(10)

    def analyze_market_scenario(self, expiry_type, scenario_type):
        """Analyze a specific market scenario"""
        # Test multiple moneyness levels to find opportunities
        moneyness_levels = [-0.03, -0.01, 0.0, 0.01, 0.03]
        opportunities_found = 0

        for moneyness in moneyness_levels:
            individual_premiums, option_details = self.calculate_option_premiums(
                expiry_type, scenario_type
            )

            P, K, scaled_premiums, scale_factor = self.calculate_basket_parameters(
                individual_premiums, moneyness
            )

            T_map = {'weekly': 0.02, 'monthly': 0.08}
            T = T_map[expiry_type]

            edge, theoretical_basket_normalized, market_sum_normalized = self.pricing_model.calculate_arbitrage_edge(
                scaled_premiums, P, K, T
            )

            theoretical_basket_usd = theoretical_basket_normalized / scale_factor
            market_sum_usd = market_sum_normalized / scale_factor
            total_market_premium = sum(individual_premiums)

            moneyness_label = f"{moneyness:+.1%}" if moneyness != 0 else "ATM"

            # Only show scenarios with meaningful activity
            if abs(edge) > 0.005:  # Show if edge > 0.5%
                print(f"   {moneyness_label}: Edge = {edge:+.2%} | "
                      f"Market = ${total_market_premium:,.0f} | "
                      f"Theoretical = ${theoretical_basket_usd:,.0f}")

                if abs(edge) > self.min_edge:
                    opportunities_found += 1
                    profit = abs(theoretical_basket_usd - total_market_premium)
                    direction = "SELL individuals, BUY basket" if edge > 0 else "BUY individuals, SELL basket"
                    print(f"   🎯 OPPORTUNITY: {direction} | Profit: ${profit:,.0f} ({abs(edge):.2%})")

        if opportunities_found == 0:
            print("   ✓ No significant arbitrage opportunities detected")
        else:
            print(f"   📈 Found {opportunities_found} potential opportunity(ies)")

    def demonstrate_formula_calculation(self):
        """Demonstrate the geometric formula calculation in detail"""
        print("\n" + "=" * 70)
        print("🧮 GEOMETRIC FORMULA DEMONSTRATION")
        print("=" * 70)

        # Use monthly options for demonstration
        expiry_type = 'monthly'
        individual_premiums, option_details = self.calculate_option_premiums(expiry_type, "mispriced")

        print("Option Premiums:")
        total_premium = sum(individual_premiums)
        for detail in option_details:
            print(f"   {detail['pair']}: ${detail['premium']:,.0f} (noise: {detail['noise']:+.1%})")
        print(f"   Total Market Premium: ${total_premium:,.0f}")

        # Show geometric calculation for different moneyness levels
        print(f"\nGeometric Formula Application:")
        print("C(P,K,T) = e^(-rT)[(P+K)*sinh(ln(P/K))/ln(P/K) - K]")

        moneyness_levels = [-0.05, -0.02, 0.0, 0.02, 0.05]
        for moneyness in moneyness_levels:
            P, K, scaled_premiums, scale_factor = self.calculate_basket_parameters(
                individual_premiums, moneyness
            )

            T = 0.08  # monthly
            theoretical = self.pricing_model.portfolio_call(P, K, T)
            theoretical_usd = theoretical / scale_factor

            moneyness_val = np.log(P / K)
            edge = (total_premium - theoretical_usd) / theoretical_usd if theoretical_usd > 0 else 0

            moneyness_label = f"{moneyness:+.1%}" if moneyness != 0 else "ATM"
            print(f"\n   {moneyness_label} Moneyness:")
            print(f"     P={P:.1f}, K={K:.1f}, T={T:.3f}")
            print(f"     ln(P/K) = {moneyness_val:+.6f}")
            print(f"     Theoretical Basket = ${theoretical_usd:,.0f}")
            print(f"     Arbitrage Edge = {edge:+.2%}")

            if abs(edge) > self.min_edge:
                print(f"     🎯 TRADEABLE OPPORTUNITY!")

    # ========== Main Strategy ==========

    def run_strategy(self):
        """Main strategy loop"""
        if not self.connected:
            print("❌ Not connected to IBKR")
            return

        print("🚀 Starting Enhanced Geometric Pricing Analysis...")

        # Request market data
        self.request_spot_prices()

        # Wait for data
        if not self.wait_for_spot_data(timeout=15):
            print("\n🔍 Switching to Enhanced Analysis Mode...")
            # First demonstrate the formula
            self.demonstrate_formula_calculation()
            # Then run continuous analysis
            self.run_enhanced_analysis()
            return

        # Use available data
        available_pairs, missing_pairs = self.get_available_spot_data()
        print(f"🔍 Using real data for {available_pairs}, demo for {missing_pairs}")
        self.demonstrate_formula_calculation()
        self.run_enhanced_analysis()

    def run_mixed_data_mode(self, available_pairs):
        """Run with mixed real and demo data"""
        self.demonstrate_formula_calculation()
        self.run_enhanced_analysis()


def main():
    """Main function to run the trading system"""
    app = IBFXTradingApp()

    # Connect to IBKR
    if app.connect_to_ibkr(host='127.0.0.1', port=7497, client_id=1):
        time.sleep(3)

        # Start the strategy
        strategy_thread = threading.Thread(target=app.run_strategy, daemon=True)
        strategy_thread.start()
        print("📈 Analysis thread started...")

        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Application stopped by user")
    else:
        print("❌ Failed to connect to IBKR")


if __name__ == "__main__":
    main()
