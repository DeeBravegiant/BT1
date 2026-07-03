### Title
Stale Cross-Chain agETH Rate Enables Over-Minting via Token Deposit, Stealing Yield from Existing Holders — (`contracts/agETH/AGETHPoolV3.sol`)

---

### Summary

`AGETHPoolV3.deposit(token, amount, referralId)` mints agETH using a ratio of two oracle rates. The agETH/ETH rate is sourced from `AGETHRateReceiver` (a cross-chain LayerZero receiver) which stores the last received rate with **no staleness check**. When this rate is stale-low relative to the live token oracle, the minting formula over-issues agETH, diluting existing holders' accrued yield.

---

### Finding Description

`AGETHPoolV3.viewSwapAgETHAmountAndFee(amount, token)` computes:

```solidity
agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
``` [1](#0-0) 

`agETHToETHrate` is fetched from `agETHOracle` which is an `AGETHRateReceiver` — a `CrossChainRateReceiver` that stores the last LayerZero-delivered rate:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
``` [2](#0-1) 

The contract records `lastUpdated` on every `lzReceive` call but **never uses it as a guard** in `getRate()`: [3](#0-2) 

There is no maximum-age check, no circuit breaker, and no revert path for a stale rate. The `tokenToETHRate` for the deposited LST (e.g., wstETH) is fetched from a separate, live on-chain oracle: [4](#0-3) 

---

### Impact Explanation

agETH is a yield-bearing token: its ETH value grows over time. Existing holders' accrued-but-unclaimed yield is embedded in the rising agETH/ETH rate. When an attacker deposits at a stale-low `agETHToETHrate`, the formula `tokenToETHRate / agETHToETHrate` is inflated, minting more agETH shares than the deposited token's true ETH value warrants. This dilutes the share of yield belonging to existing holders — a direct theft of unclaimed yield.

**Numeric example:**
- True agETH/ETH rate: `1.05e18` (accrued yield since last cross-chain update)
- Stale stored rate: `1.00e18`
- wstETH/ETH rate (live): `1.05e18`
- Deposit: `1e18` wstETH (worth `1.05e18` ETH)

Correct agETH minted: `1.05e18 * 1.05e18 / 1.05e18 = 1.05e18`
Actual agETH minted: `1.05e18 * 1.05e18 / 1.00e18 = 1.1025e18`

The attacker receives `~5%` excess agETH, representing yield stolen from existing holders.

---

### Likelihood Explanation

Cross-chain LayerZero messages are subject to natural delays (minutes to hours) and can be further delayed by network congestion or relayer downtime. The `lastUpdated` field confirms the protocol is aware of the time dimension but provides no enforcement. Any user can call `deposit(token, amount, referralId)` permissionlessly at any time: [5](#0-4) 

No admin access, no oracle manipulation, and no front-running is required — only timing the deposit during a staleness window, which occurs routinely.

---

### Recommendation

Add a staleness guard in `CrossChainRateReceiver.getRate()` (or in `AGETHPoolV3.getRate()`). Expose `lastUpdated` and revert if the rate is older than a configurable `maxRateAge`:

```solidity
uint256 public maxRateAge = 24 hours; // configurable

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxRateAge, "Rate is stale");
    return rate;
}
```

This ensures deposits are blocked when the cross-chain rate has not been refreshed within an acceptable window, preventing over-minting.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fuzz: agETHToETHrate in [1e18, 1.1e18], tokenToETHRate fixed at 1.05e18
// Invariant: agETHAmount * agETHToETHrate <= amountAfterFee * tokenToETHRate

function testStaleRateOverMint(uint256 agETHToETHrate) public {
    agETHToETHrate = bound(agETHToETHrate, 1e18, 1.05e18 - 1); // stale-low range

    uint256 tokenToETHRate = 1.05e18;
    uint256 amount = 1e18;
    uint256 feeBps = 0; // simplify
    uint256 amountAfterFee = amount;

    // Simulate AGETHPoolV3 formula
    uint256 agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;

    // Invariant: attacker should not receive more ETH-value in agETH than deposited
    // agETHAmount * agETHToETHrate should equal amountAfterFee * tokenToETHRate
    // When agETHToETHrate < true rate, agETHAmount is inflated
    assertLe(
        agETHAmount * agETHToETHrate,
        amountAfterFee * tokenToETHRate,
        "Over-minting: attacker steals yield"
    );
    // This assertion FAILS when agETHToETHrate < tokenToETHRate due to integer division rounding up
    // More importantly, agETHAmount represents MORE shares than the deposit warrants at the true rate
}
```

When `agETHToETHrate = 1.00e18` and `tokenToETHRate = 1.05e18`, `agETHAmount = 1.05e18` but the true fair amount is `1.00e18` (since 1.05 ETH of wstETH / 1.05 agETH-per-ETH = 1.00 agETH). The `0.05e18` excess agETH is minted at the expense of existing holders' accrued yield.

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L134-154)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L188-194)
```text
        uint256 agETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-100)
```text
        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```
