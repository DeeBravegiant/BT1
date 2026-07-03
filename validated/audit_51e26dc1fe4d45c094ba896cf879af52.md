Looking at the exact code flow in `L1VaultV2.sol`:

### Title
Strict exact-equality fee check in `bridgeRsETHToL2UsingCCIP` prevents overpayment, causing revert on fee spikes and delaying rsETH delivery to L2 - (`contracts/L1VaultV2.sol`)

---

### Summary

`L1VaultV2.bridgeRsETHToL2UsingCCIP` enforces `msg.value == getCCIPFee(amount)` (exact equality). Because the manager must quote the fee off-chain before submitting the transaction, any increase in the CCIP fee between the off-chain quote and on-chain execution causes an `IncorrectCCIPFee` revert. The underlying CCIP router explicitly accepts overpayment, so the restriction is entirely self-imposed by the contract.

---

### Finding Description

In `bridgeRsETHToL2UsingCCIP`, the fee is computed on-chain at execution time:

```solidity
uint256 fee = getCCIPFee(amount);   // line 354 — live router query
if (msg.value != fee) {             // line 356 — exact equality
    revert IncorrectCCIPFee();
}
``` [1](#0-0) 

`getCCIPFee` delegates to `ccipRouter.getFee(...)`, which is a live, mutable view that reflects current network conditions: [2](#0-1) 

The CCIP message always uses `feeToken: address(0)`, meaning the fee is paid in native ETH via `msg.value`: [3](#0-2) 

The CCIP router's own interface explicitly documents that overpayment is accepted:

```
/// @dev Note if msg.value is larger than the required fee (from getFee) we accept
/// the overpayment with no refund.
``` [4](#0-3) 

The manager's only option is to:
1. Call `getCCIPFee(amount)` off-chain to determine `msg.value`
2. Submit the transaction

If the CCIP fee increases between step 1 and step 2 (e.g., due to destination-chain gas price movement), the on-chain `getCCIPFee` returns a higher value than `msg.value`, and the transaction reverts. The manager **cannot defensively overpay** — the `!=` check rejects any `msg.value` that is not exactly equal to the current fee.

---

### Impact Explanation

rsETH held in `L1VaultV2` cannot be bridged to L2 during periods of fee volatility. Each attempt requires a fresh off-chain quote and a new transaction. Under sustained fee volatility, multiple consecutive attempts may fail, delaying rsETH delivery to L2 users. No funds are lost or locked permanently — the manager can always retry — but the contract fails to deliver timely rsETH transfers as intended.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

CCIP fees are denominated in native ETH and fluctuate with destination-chain gas prices. Fee changes between a mempool quote and block inclusion are routine, especially during periods of network congestion. The manager role is a normal operational actor, not an attacker. No adversarial action is required — ordinary network conditions trigger the revert.

---

### Recommendation

Replace the exact-equality check with a minimum-fee check, and forward only the required fee to the router (which accepts overpayment):

```solidity
uint256 fee = getCCIPFee(amount);
if (msg.value < fee) {
    revert IncorrectCCIPFee();
}
// ccipRouter accepts overpayment; forward msg.value directly
bytes32 messageId = ccipRouter.ccipSend{ value: msg.value }(destinationChainSelector, message);
```

This allows the manager to send a small buffer above the quoted fee, ensuring the call succeeds even if the fee increases slightly between quote and execution. Since the router keeps any overpayment (no refund), the manager bears only the cost of the buffer.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// MockRouter that returns a higher fee on the second call (simulating a mid-block spike)
contract MockRouter {
    uint256 public callCount;
    uint256 public baseFee = 0.01 ether;

    function getFee(uint64, Client.EVM2AnyMessage memory) external returns (uint256) {
        callCount++;
        // Simulate fee spike: second call (inside bridgeRsETHToL2UsingCCIP) returns higher fee
        return callCount == 1 ? baseFee : baseFee + 0.001 ether;
    }

    function ccipSend(uint64, Client.EVM2AnyMessage calldata) external payable returns (bytes32) {
        return bytes32(0);
    }
}

// Test:
// 1. Manager calls getCCIPFee off-chain → returns 0.01 ether (callCount=1)
// 2. Manager submits bridgeRsETHToL2UsingCCIP with msg.value = 0.01 ether
// 3. Inside the tx, getCCIPFee is called again → returns 0.011 ether (callCount=2)
// 4. msg.value (0.01e18) != fee (0.011e18) → IncorrectCCIPFee revert
```

The fuzz variant: vary `MockRouter.getFee` return value between the off-chain quote call and the on-chain execution call. Any positive delta causes a revert, confirming the invariant break.

### Citations

**File:** contracts/L1VaultV2.sol (L354-358)
```text
        uint256 fee = getCCIPFee(amount);

        if (msg.value != fee) {
            revert IncorrectCCIPFee();
        }
```

**File:** contracts/L1VaultV2.sol (L400-404)
```text
    function getCCIPFee(uint256 amount) public view returns (uint256) {
        Client.EVM2AnyMessage memory message = getCCIPMessage(amount);

        return ccipRouter.getFee(destinationChainSelector, message);
    }
```

**File:** contracts/L1VaultV2.sol (L425-431)
```text
        return Client.EVM2AnyMessage({
            receiver: abi.encodePacked(getReceiver()),
            data: bytes(""),
            tokenAmounts: tokenAmounts,
            feeToken: address(0), // address(0) means we will send msg.value (i.e. pay fee in native currency)
            extraArgs: Client._argsToBytes(extraArgs)
        });
```

**File:** contracts/external/chainlink/IRouterClient.sol (L39-40)
```text
    /// @dev Note if msg.value is larger than the required fee (from getFee) we accept
    /// the overpayment with no refund.
```
