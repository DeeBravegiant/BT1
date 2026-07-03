The code confirms all the structural claims. Let me verify the key details before rendering judgment.

**Verified facts from the code:**

1. `rsETHPrice` is a cached state variable, updated only in `_updateRsETHPrice()` [1](#0-0) 

2. `getRsETHAmountToMint` mixes live `getAssetPrice()` with cached `rsETHPrice()` [2](#0-1) 

3. `getExpectedAssetAmount` does the same in the withdrawal direction [3](#0-2) 

4. `instantWithdrawal` uses `getExpectedAssetAmount` directly with no min-guard, unlike the queued path which uses `_calculatePayoutAmount` with `min(expectedAmount, currentReturn)` [4](#0-3) [5](#0-4) 

5. Neither `_beforeDeposit` nor `instantWithdrawal` calls `updateRSETHPrice()` or any equivalent live computation before use [6](#0-5) [7](#0-6) 

6. The `pricePercentageLimit` downside-pause only fires inside `_updateRsETHPrice()` — it provides no protection during the staleness window itself [8](#0-7) 

All claims check out. The finding is valid.

---

Audit Report

## Title
Stale Cached `rsETHPrice` Enables Arbitrage on Deposits and Instant Withdrawals — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTOracle.rsETHPrice` is a cached state variable updated only when `updateRSETHPrice()` is explicitly called. Both `LRTDepositPool.getRsETHAmountToMint()` and `LRTWithdrawalManager.getExpectedAssetAmount()` divide by this stale value while reading live Chainlink prices via `getAssetPrice()`. An unprivileged attacker can time deposits or instant-withdrawals within the staleness window to receive more rsETH or more underlying assets than their fair share, directly stealing value from other protocol participants.

## Finding Description
`LRTOracle` stores `rsETHPrice` as a persistent state variable updated only in `_updateRsETHPrice()`, called by the public `updateRSETHPrice()` or the manager-gated `updateRSETHPriceAsManager()`. No on-chain mechanism forces a refresh before any user-facing operation.

**Deposit path** — `LRTDepositPool.getRsETHAmountToMint()` (L520):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
`getAssetPrice(asset)` fetches a live Chainlink price on every call; `rsETHPrice()` returns the stale cached value. When underlying LST prices have risen since the last `updateRSETHPrice()` call, the true rsETH price is higher than stored. A depositor acting before the update receives more rsETH than their deposit is worth, diluting all existing holders. `_beforeDeposit` performs no price refresh before calling `getRsETHAmountToMint`.

**Instant-withdrawal path** — `LRTWithdrawalManager.getExpectedAssetAmount()` (L593):
```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
When underlying LST prices have fallen since the last update, the true rsETH price is lower than stored. An attacker calling `instantWithdrawal` before `updateRSETHPrice()` receives more underlying assets than their rsETH is truly worth. Unlike the queued-withdrawal path, which applies `min(expectedAmount, currentReturn)` via `_calculatePayoutAmount`, `instantWithdrawal` transfers the full stale-inflated amount immediately with no such guard.

The `pricePercentageLimit` downside-pause mechanism only fires inside `_updateRsETHPrice()` itself and provides no protection during the staleness window between calls.

## Impact Explanation
**Critical — direct theft of user funds.**

- **Instant-withdrawal:** When LST prices drop and `rsETHPrice` is stale-high, an attacker burns rsETH and receives more underlying LST than the rsETH is worth. The excess is drawn directly from the pool's assets, reducing the redemption value for all remaining depositors.
- **Deposit:** When LST prices rise and `rsETHPrice` is stale-low, an attacker mints more rsETH than their deposit warrants. After `updateRSETHPrice()` is called, the attacker's rsETH is worth more than deposited; the dilution is borne by all existing holders.

Both paths result in direct, quantifiable loss to other protocol participants, satisfying the Critical "direct theft of any user funds" impact class.

## Likelihood Explanation
`updateRSETHPrice()` is public and called by off-chain bots, but there is no on-chain freshness enforcement. Any block between two update calls is a valid attack window. LST prices (stETH/ETH, ETHx/ETH) fluctuate continuously via Chainlink; even small deviations (0.1–0.5%) over a multi-block window are sufficient for a profitable attack at scale. The attacker needs no special role — any rsETH holder or ETH depositor can execute this. The instant-withdrawal path additionally requires `isInstantWithdrawalEnabled[asset] == true`, which is a manager-controlled gate, but the deposit-side dilution path is always open when the protocol is unpaused. The attacker can also front-run the bot's `updateRSETHPrice()` transaction to maximize the staleness gap.

## Recommendation
1. **Atomically refresh `rsETHPrice` before every deposit and withdrawal.** Call `_updateRsETHPrice()` (or an equivalent view-only live computation) inside `_beforeDeposit` and `getExpectedAssetAmount` rather than reading the cached state variable.
2. **Alternatively, compute the share price on-the-fly** using `_getTotalEthInProtocol() / rsethSupply` at the point of use, eliminating the cached value entirely for user-facing operations.
3. **Add a staleness guard** on `rsETHPrice` (e.g., a `lastUpdatedTimestamp` that must be within N blocks) and revert deposits/withdrawals if the price is stale.
4. **Apply the `min(expectedAmount, currentReturn)` guard** from `_calculatePayoutAmount` to `instantWithdrawal` as a defense-in-depth measure.

## Proof of Concept
**Instant-withdrawal theft (requires `isInstantWithdrawalEnabled[stETH] == true`):**

1. Block B: stETH/ETH Chainlink = 1.01. `updateRSETHPrice()` called; `rsETHPrice` = 1.01. Pool holds 10,000 stETH, 9,901 rsETH outstanding.
2. Block B+10: stETH/ETH Chainlink drops to 0.99 (depeg). True rsETHPrice = `10,000 × 0.99 / 9,901` ≈ 0.99. Stored `rsETHPrice` = 1.01 (stale).
3. Attacker (holds 1,000 rsETH) calls `instantWithdrawal(stETH, 1000)`:
   - `getExpectedAssetAmount(stETH, 1000)` = `1000 × 1.01 / 0.99` ≈ **1020.2 stETH**
   - Fair value: `1000 × 0.99 / 0.99` = **1000 stETH**
   - Attacker receives **~20.2 stETH excess**, stolen from remaining depositors.
4. `updateRSETHPrice()` subsequently called; remaining depositors' rsETH is now backed by fewer assets.

**Deposit-side dilution (always available):**

1. Block B: stETH/ETH = 0.99. `rsETHPrice` = 0.99 (updated).
2. Block B+5: stETH/ETH rises to 1.01 (live Chainlink). True rsETHPrice ≈ 1.01. Stored `rsETHPrice` = 0.99 (stale).
3. Attacker deposits 1,000 stETH:
   - `getRsETHAmountToMint(stETH, 1000)` = `1000 × 1.01 / 0.99` ≈ **1020.2 rsETH**
   - Fair amount: `1000 × 1.01 / 1.01` = **1000 rsETH**
   - Attacker receives **~20.2 rsETH excess** at existing holders' expense.
4. `updateRSETHPrice()` called; attacker's rsETH is now worth more than deposited.

A Foundry fork test can reproduce both scenarios by: (1) deploying against a mainnet fork, (2) manipulating the mock Chainlink price feed between the last `updateRSETHPrice()` call and the attacker's deposit/withdrawal, and (3) asserting that the attacker's post-operation balance exceeds the fair-value amount.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
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

**File:** contracts/LRTWithdrawalManager.sol (L592-593)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
