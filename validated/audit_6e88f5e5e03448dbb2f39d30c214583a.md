### Title
Withdrawal Payout Capped at Initiation-Time Rate, Stripping Yield Accrued During Delay — (`File: contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_calculatePayoutAmount()` in `LRTWithdrawalManager` returns `min(expectedAssetAmount, currentReturn)`. When rsETH appreciates between `initiateWithdrawal` and `unlockQueue`, the withdrawing user receives only the asset amount calculated at initiation time, not the current market value of their rsETH. The excess appreciation is silently captured by the protocol, constituting theft of unclaimed yield from every withdrawer over the mandatory 8-day delay.

---

### Finding Description

The withdrawal lifecycle has three steps:

**Step 1 — `initiateWithdrawal` (line 168):**
The user transfers `rsETHUnstaked` to the contract. `expectedAssetAmount` is computed at the current oracle price:

```
expectedAssetAmount = rsETHUnstaked * rsETHPrice_T0 / assetPrice_T0
```

This value is stored in `WithdrawalRequest.expectedAssetAmount` and `assetsCommitted[asset]` is incremented by it.

**Step 2 — `unlockQueue` → `_calculatePayoutAmount` (lines 798, 824–835):**

```solidity
function _calculatePayoutAmount(
    WithdrawalRequest storage request,
    uint256 rsETHPrice,
    uint256 assetPrice
) private view returns (uint256) {
    uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
    return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
}
```

`currentReturn` is the current market value of the user's rsETH. The function returns `min(expectedAssetAmount, currentReturn)`. When rsETH has appreciated (`rsETHPrice_T1 > rsETHPrice_T0`), `currentReturn > expectedAssetAmount`, so `payoutAmount = expectedAssetAmount`. The updated `request.expectedAssetAmount` is set to this lower value (line 804).

**Step 3 — `completeWithdrawal` → `_processWithdrawalCompletion` (line 734):**
The user receives `request.expectedAssetAmount`, which is the initiation-time amount, not the current value.

The full `rsETHUnstaked` is burned (line 305), but the user only receives the asset equivalent of the initiation-time rsETH price. The delta — `currentReturn - expectedAssetAmount` — remains in the protocol's unstaking vault, accruing to remaining rsETH holders rather than the withdrawing user.

This is structurally identical to H05: a `min()` is applied where the user should receive the higher value. In H05, `min(tx.gasprice, user_specified_price)` was wrong because the user should pay their specified price. Here, `min(expectedAssetAmount, currentReturn)` is wrong because the user should receive the current value of the rsETH they surrendered.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every user who initiates a withdrawal and waits through the mandatory delay loses the staking yield that accrued on their rsETH during that period. rsETH is a yield-bearing token that appreciates continuously (~4–5% APY from staking rewards). Over the default 8-day delay (`withdrawalDelayBlocks = 8 days / 12 seconds`, line 94), rsETH appreciates by approximately 0.09–0.11%. For a 100 ETH withdrawal, this is ~0.1 ETH of yield silently stripped from the user. This loss is systematic and affects every withdrawal processed after any rsETH price increase.

The stolen yield is not destroyed — it remains in the protocol's asset pool, effectively redistributed to remaining rsETH holders. The withdrawing user, having already surrendered their rsETH at `initiateWithdrawal`, cannot benefit from this redistribution.

---

### Likelihood Explanation

**High.** rsETH accrues staking yield continuously. Any `unlockQueue` call made after even a single oracle price update following `initiateWithdrawal` will trigger this condition. The withdrawal delay is 8 days by default (up to 16 days maximum), making it virtually certain that rsETH will have appreciated between initiation and unlock for every withdrawal. No special conditions, attacker actions, or external dependencies are required — the loss occurs automatically for every normal user withdrawal.

---

### Recommendation

Replace the `min()` with `currentReturn` so users receive the full current value of their rsETH at unlock time:

```solidity
function _calculatePayoutAmount(
    WithdrawalRequest storage request,
    uint256 rsETHPrice,
    uint256 assetPrice
) private view returns (uint256) {
    return (request.rsETHUnstaked * rsETHPrice) / assetPrice;
}
```

If the protocol intentionally caps payouts at `expectedAssetAmount` to protect against insolvency (i.e., the vault may not hold enough assets to cover the appreciated value), the excess rsETH should be returned to the user rather than burned, or the design rationale should be explicitly documented and the excess should not be silently captured.

---

### Proof of Concept

1. rsETH price at T0: `1.050 ETH/rsETH`. Asset (stETH) price: `1.000 ETH/stETH`.
2. User calls `initiateWithdrawal(stETH, 100e18)`.
   - `expectedAssetAmount = 100e18 * 1.050e18 / 1.000e18 = 105e18` stETH.
   - 100 rsETH transferred to contract.
3. 8 days pass. rsETH price updates to `1.051 ETH/rsETH` (one day of ~4.5% APY yield).
4. Operator calls `unlockQueue(stETH, ...)`.
   - `currentReturn = 100e18 * 1.051e18 / 1.000e18 = 105.1e18` stETH.
   - `_calculatePayoutAmount` returns `min(105e18, 105.1e18) = 105e18`.
   - `request.expectedAssetAmount` updated to `105e18`.
   - 100 rsETH burned.
5. User calls `completeWithdrawal(stETH)`.
   - User receives `105e18` stETH.
   - **Lost yield: `0.1e18` stETH (~0.095% of withdrawal, ~$250 on a $250k withdrawal).**
   - The `0.1e18` stETH remains in the vault, benefiting other rsETH holders. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-176)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L730-737)
```text
                }
            }
        }

        _transferAsset(asset, user, request.expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L797-808)
```text
            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

```

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```
