Audit Report

## Title
Stale Oracle Rate Enables Yield Theft via Deposit-then-InstantWithdrawal - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is publicly callable with no access restriction. An attacker can deposit ETH at the stale (lower) `rsETHPrice`, self-trigger the oracle update, and immediately redeem via `instantWithdrawal` at the newly updated (higher) price, extracting accrued yield that belongs to existing rsETH holders. The queued withdrawal path is protected by a `min(expected, current)` cap; `instantWithdrawal` has no equivalent guard.

## Finding Description
`updateRSETHPrice()` is declared `public` with only a `whenNotPaused` modifier and no role restriction: [1](#0-0) 

Deposit minting uses the stored `rsETHPrice` at the moment of the call: [2](#0-1) 

`instantWithdrawal` computes the payout via `getExpectedAssetAmount`, which reads the live `rsETHPrice` at execution time with no snapshot or cap: [3](#0-2) [4](#0-3) 

The queued path is protected: `_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)`, so a price increase after initiation does not benefit the withdrawer: [5](#0-4) 

`instantWithdrawal` has no equivalent protection. The exploit flow is:
1. **Deposit at stale price**: `depositETH{value: 1000 ether}` mints rsETH at `rsETHPrice = 1.000e18`, giving 1000 rsETH for 1000 ETH.
2. **Advance oracle**: call `updateRSETHPrice()`. The attacker's 1000 ETH is now included in `_getTotalEthInProtocol()`, so the new price reflects the accrued rewards. For a large protocol TVL (S rsETH, true ETH = 1.001·S), the new price ≈ 1.001e18.
3. **Instant redeem**: `instantWithdrawal(ETH, 1000e18)` computes `assetAmountUnlocked = 1000 * 1.001e18 / 1e18 ≈ 1001 ETH`. After fee, the attacker recovers more than the 1000 ETH deposited.

The `pricePercentageLimit` guard only blocks price increases exceeding the configured threshold; normal daily yield accrual (~0.01%) is well within any reasonable limit and does not trigger the revert path: [6](#0-5) 

## Impact Explanation
This is **High — Theft of unclaimed yield**. The profit `≈ (newPrice − oldPrice) × depositAmount / newPrice − fee` is extracted directly from the yield that should have been distributed pro-rata to all existing rsETH holders. The attack is repeatable on every oracle update cycle, draining accrued yield continuously.

## Likelihood Explanation
- `updateRSETHPrice()` requires no role; any EOA can call it.
- `isInstantWithdrawalEnabled[asset]` must be `true` — a manager-controlled toggle, but the feature exists and is designed to be enabled.
- `instantWithdrawalFee` is capped at 10% and can be set to 0; at typical LRT daily yields (~1–2 bps), any fee below the daily yield percentage makes the attack profitable.
- The oracle is updated periodically, not on every block, so stale windows exist regularly.
- No front-running is required; the attacker controls all three steps atomically across consecutive transactions. [7](#0-6) [8](#0-7) 

## Recommendation
1. **Snapshot the oracle price at deposit time** and use that snapshot as the ceiling for `instantWithdrawal` payout for rsETH minted in the same oracle epoch, mirroring the `min(expected, current)` logic in `_calculatePayoutAmount`.
2. **Impose a minimum holding period** (e.g., one oracle update cycle) before rsETH minted via `depositETH` is eligible for `instantWithdrawal`.
3. **Restrict `updateRSETHPrice()` to a privileged role**, reducing the attack to a front-running scenario that is harder to execute reliably.
4. **Set `instantWithdrawalFee` to a value that always exceeds the maximum possible inter-update yield** and enforce this invariant on-chain.

## Proof of Concept
Preconditions: `isInstantWithdrawalEnabled[ETH] = true`, `instantWithdrawalFee = 5 bps`, protocol TVL = 1,000,000 ETH staked (S = 1,000,000 rsETH), rewards have accrued so true ETH = 1,001,000 ETH but stored `rsETHPrice = 1.000e18`.

```
Step 1 — Deposit at stale rate:
  LRTDepositPool.depositETH{value: 1_000 ether}(minRSETH=999e18, "")
  rsethAmountToMint = 1000e18 * 1e18 / 1.000e18 = 1000e18 rsETH minted

Step 2 — Advance oracle:
  LRTOracle.updateRSETHPrice()
  totalETHInProtocol = 1_001_000 + 1_000 = 1_002_000 ETH
  rsethSupply        = 1_000_000 + 1_000 = 1_001_000 rsETH
  newRsETHPrice      = 1_002_000e18 / 1_001_000 ≈ 1.000999e18

Step 3 — Instant withdrawal:
  LRTWithdrawalManager.instantWithdrawal(ETH, 1000e18, "")
  assetAmountUnlocked = 1000 * 1.000999e18 / 1e18 ≈ 1000.999 ETH
  fee (5 bps)         = 1000.999 * 5 / 10_000 ≈ 0.500 ETH
  userAmount          ≈ 1000.499 ETH

Net result: attacker spent 1000 ETH, received ≈1000.499 ETH → profit ≈ 0.499 ETH
extracted from existing rsETH holders' accrued yield.
At fee = 0 bps: profit ≈ 0.999 ETH.
Attack is profitable whenever instantWithdrawalFee (bps) < oracle_lag_yield_bps.
```

Foundry fork test plan: fork mainnet, set `isInstantWithdrawalEnabled[ETH] = true`, set `instantWithdrawalFee = 0`, warp time to accumulate rewards without calling `updateRSETHPrice`, execute the three-step sequence above, assert `address(attacker).balance > 1000 ether` after the round-trip.

### Citations

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L55-56)
```text
    mapping(address asset => bool) public isInstantWithdrawalEnabled;
    uint256 public instantWithdrawalFee; // Fee in basis points (1 = 0.01%)
```

**File:** contracts/LRTWithdrawalManager.sol (L78-81)
```text
    modifier onlyInstantWithdrawalAllowed(address asset) {
        if (!isInstantWithdrawalEnabled[asset]) revert InstantWithdrawalNotEnabled();
        _;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L833-835)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```
