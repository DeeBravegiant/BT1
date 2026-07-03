## Vulnerability Analysis

Let me trace the full path carefully.

**`CrossChainRateReceiver.getRate()`** — simply returns the stored `rate` with zero staleness validation: [1](#0-0) 

`lastUpdated` is recorded on every `lzReceive` call but is **never read back** anywhere in the contract: [2](#0-1) 

**`AGETHPoolV3.deposit()`** calls `viewSwapAgETHAmountAndFee`, which fetches the rate and computes:
```
agETHAmount = amountAfterFee * 1e18 / agETHToETHrate
``` [3](#0-2) 

There is no staleness guard anywhere between `deposit()` and `getRate()`. [4](#0-3) 

---

### Title
Stale Cross-Chain Rate in `CrossChainRateReceiver` Allows Depositors to Mint Excess agETH, Stealing Unclaimed Yield — (`contracts/agETH/AGETHRateReceiver.sol` / `contracts/cross-chain/CrossChainRateReceiver.sol`)

### Summary
`CrossChainRateReceiver` stores `lastUpdated` but never enforces a maximum staleness window. `AGETHPoolV3.deposit()` blindly consumes whatever rate is stored. When the L2 rate lags behind the true L1 agETH/ETH rate (which monotonically increases as staking yield accrues), any depositor can mint more agETH than their ETH warrants, extracting yield that belongs to existing holders.

### Finding Description
The agETH/ETH rate increases over time on L1 as staking rewards accrue. The L2 `CrossChainRateReceiver` is updated only when a LayerZero message arrives via `lzReceive`. If no message has arrived recently — due to network delay, keeper inactivity, or simply the normal inter-update window — the stored `rate` is lower than the true current rate.

`AGETHPoolV3.viewSwapAgETHAmountAndFee` computes:

```
agETHAmount = amountAfterFee * 1e18 / staleRate
```

Because `staleRate < trueRate`, `agETHAmount > amountAfterFee * 1e18 / trueRate`. The depositor receives surplus agETH tokens. When those tokens are eventually redeemed on L1 at the true (higher) rate, the depositor recovers more ETH than they deposited. The surplus comes directly from yield that had already accrued to existing agETH holders but had not yet been reflected in the L2 oracle.

There is no `require(block.timestamp - lastUpdated <= MAX_STALENESS)` guard in `getRate()`, `viewSwapAgETHAmountAndFee`, or `deposit()`. [1](#0-0) [5](#0-4) 

### Impact Explanation
**High — Theft of unclaimed yield.**

Existing agETH holders have accrued staking yield on L1. That yield is reflected in the rising agETH/ETH rate. A depositor who mints agETH at a stale (lower) rate receives a proportionally larger share of the total agETH supply than their ETH contribution justifies. On redemption at the true rate, they extract ETH that belongs to prior holders. The magnitude scales linearly with both the staleness duration (yield accrued since last update) and the deposit size.

### Likelihood Explanation
LayerZero rate updates are not guaranteed to arrive on any fixed schedule. The keeper/relayer can be delayed, congested, or simply not triggered for hours or days. The `lastUpdated` field is public, so an attacker can trivially monitor on-chain when the rate was last refreshed and time a large deposit to coincide with maximum staleness. No privileged access is required — `deposit()` is fully permissionless. [6](#0-5) 

### Recommendation
Add a staleness check in `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours; // configurable

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

This causes `deposit()` to revert when the oracle is stale, preventing exploitation while the rate is being refreshed. Alternatively, enforce the check inside `AGETHPoolV3.viewSwapAgETHAmountAndFee` directly.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test (local fork of L2 deployment)
// 1. Deploy AGETHPoolV3 pointing to a mock AGETHRateReceiver
// 2. Mock returns a rate 1% below the true current L1 rate (simulating ~1 day of staleness)
// 3. Attacker deposits 100 ETH
// 4. Assert minted agETH * trueRate > 100 ETH (attacker profits)

contract StaleRatePoC {
    AGETHPoolV3 pool;
    MockAGETHRateReceiver mockOracle;
    uint256 constant TRUE_RATE  = 1.05e18; // e.g. current L1 rate
    uint256 constant STALE_RATE = 1.04e18; // 1% stale

    function setUp() public {
        mockOracle = new MockAGETHRateReceiver(STALE_RATE);
        // deploy pool with mockOracle as agETHOracle
    }

    function testStaleRateExploit() public {
        uint256 deposit = 100 ether;
        uint256 agETHMinted = pool.deposit{value: deposit}("");

        // agETHMinted = 100e18 * 1e18 / 1.04e18 ≈ 96.15 agETH
        // At true rate: 96.15 * 1.05e18 / 1e18 ≈ 100.96 ETH redeemable
        // Profit ≈ 0.96 ETH extracted from existing holders per 100 ETH deposited

        uint256 redeemableETH = agETHMinted * TRUE_RATE / 1e18;
        assert(redeemableETH > deposit); // attacker profits at existing holders' expense
    }
}
```

Per basis point of staleness on a 100 ETH deposit, the attacker extracts approximately `100e18 * 1bps / 1e4 = 0.01 ETH` of yield from existing holders.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-99)
```text
        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L115-128)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-169)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
    }
```
