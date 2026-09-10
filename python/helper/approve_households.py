#!/usr/bin/env python3
import json
import os
import sys
import dotenv
from web3 import Web3

dotenv.load_dotenv()

# 1. Load config
config_path = os.getenv("FRONTEND_CONFIG_PATH", "config.json")
try:
    with open(config_path, "r") as f:
        config = json.load(f)
except Exception as e:
    print(f"Error loading {config_path}: {e}")
    sys.exit(1)

bc = config.get("blockchain", {})
rpc_url = bc.get("rpc_url")
stablecoin_address = bc.get("stablecoin_address")
p2p_market_address = bc.get("p2p_market_address")

if not rpc_url or not stablecoin_address or not p2p_market_address:
    print("Error: Missing rpc_url, stablecoin_address, or p2p_market_address in config.")
    sys.exit(1)

# 2. Connect Web3
w3 = Web3(Web3.HTTPProvider(rpc_url))
if not w3.is_connected():
    print(f"Error: Could not connect to RPC endpoint: {rpc_url}")
    sys.exit(1)

# 3. ERC-20 Minimal ABI
erc20_abi = [
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

stablecoin = w3.eth.contract(
    address=Web3.to_checksum_address(stablecoin_address), abi=erc20_abi
)
p2p_market_checksum = Web3.to_checksum_address(p2p_market_address)

# Helper function to send transactions
def send_tx(contract_function, signer, gas: int = 100_000):
    nonce = w3.eth.get_transaction_count(signer.address, "pending")
    tx = contract_function.build_transaction({
        "from": signer.address,
        "nonce": nonce,
        "gas": gas,
        "chainId": w3.eth.chain_id,
    })
    signed = signer.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    status = "OK" if receipt.status == 1 else "REVERTED"
    print(f"{status} | tx {tx_hash.hex()} | gasUsed {receipt.gasUsed}")
    return receipt

MAX_UINT256 = 2**256 - 1
ALREADY_APPROVED_THRESHOLD = 2**200

# 4. Iterate over households and execute approvals
households = config.get("households", [])
if not households:
    print("No households found in config.")
    sys.exit(0)

print(f"Starting approval check for {len(households)} households...")

for i, household in enumerate(households, start=1):
    label = household.get("name", household.get("id", f"Household {i}"))
    addr = Web3.to_checksum_address(household["address"])
    env_var = f"HOUSEHOLD{i}_PRIVATE_KEY"

    private_key = os.getenv(env_var)
    if not private_key:
        print(f"✗ {label}: {env_var} not set in .env, skipping")
        continue

    hh_account = w3.eth.account.from_key(private_key)
    if hh_account.address.lower() != addr.lower():
        print(
            f"✗ {label}: {env_var} resolves to {hh_account.address}, "
            f"but config has {addr} — skipping"
        )
        continue

    current_allowance = stablecoin.functions.allowance(
        addr, p2p_market_checksum
    ).call()
    if current_allowance >= ALREADY_APPROVED_THRESHOLD:
        print(f"✓ {label}: already approved ({current_allowance})")
        continue

    print(f"→ {label}: approving P2P market contract...")
    send_tx(
        stablecoin.functions.approve(p2p_market_checksum, MAX_UINT256),
        signer=hh_account,
        gas=100_000,
    )

print("All household approvals complete.")