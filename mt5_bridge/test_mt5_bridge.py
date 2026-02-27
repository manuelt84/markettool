"""
Test script for MT5 HTTP Bridge connection.
Run this to verify the EA is working correctly.

Usage:
    python test_mt5_bridge.py
"""

import httpx
import json


MT5_BRIDGE_URL = "http://170.239.86.106:8889"


def test_status():
    """Test status endpoint."""
    print("\n=== Testing /status ===")
    try:
        response = httpx.get(f"{MT5_BRIDGE_URL}/status", timeout=5.0)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_account_info():
    """Test account_info endpoint."""
    print("\n=== Testing /account_info ===")
    try:
        response = httpx.get(f"{MT5_BRIDGE_URL}/account_info", timeout=5.0)
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_symbol_info():
    """Test symbol_info endpoint."""
    print("\n=== Testing /symbol_info ===")
    try:
        payload = {"symbol": "EURUSD"}
        response = httpx.post(
            f"{MT5_BRIDGE_URL}/symbol_info",
            json=payload,
            timeout=5.0
        )
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_place_order_demo():
    """Test place_order endpoint (with demo data - won't actually execute)."""
    print("\n=== Testing /place_order (DEMO - not executing) ===")
    print("⚠️  This test shows the request format, but won't execute the order.")
    
    payload = {
        "symbol": "EURUSD",
        "volume": 0.01,  # Minimum lot size
        "side": "BUY",
        "price": 1.0950,
        "sl": 1.0900,
        "tp": 1.1000,
        "deviation": 20,
        "comment": "Test from Python script"
    }
    
    print(f"Request payload: {json.dumps(payload, indent=2)}")
    print("\nTo actually execute, uncomment the code below:")
    print("# response = httpx.post(f'{MT5_BRIDGE_URL}/place_order', json=payload, timeout=5.0)")
    print("# print(f'Response: {response.json()}')")
    
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("MT5 HTTP Bridge Test Suite")
    print("=" * 60)
    print(f"Bridge URL: {MT5_BRIDGE_URL}")
    print("=" * 60)
    
    results = []
    
    # Test 1: Status
    results.append(("Status Check", test_status()))
    
    # Test 2: Account Info
    results.append(("Account Info", test_account_info()))
    
    # Test 3: Symbol Info
    results.append(("Symbol Info", test_symbol_info()))
    
    # Test 4: Place Order (demo)
    results.append(("Place Order Format", test_place_order_demo()))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    
    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {name}")
    
    all_passed = all(result[1] for result in results)
    
    print("=" * 60)
    if all_passed:
        print("✅ All tests passed! MT5 Bridge is working correctly.")
        print("\nNext steps:")
        print("1. Restart your Python backend")
        print("2. Test from React Native app")
        print("3. Create a real order from the app")
    else:
        print("❌ Some tests failed. Check the errors above.")
        print("\nTroubleshooting:")
        print("1. Verify MT5 terminal is running")
        print("2. Verify EA is attached to a chart (😊 icon)")
        print("3. Check MT5 Experts tab for EA logs")
        print("4. Verify port 8889 is open in firewall")
        print("5. Try: telnet 170.239.86.106 8889")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
