### Title
Deposit-Inflate-Commit Pattern Allows Bypassing Withdrawal Availability Limit - (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.getAvailableAssetAmount()` computes available withdrawal capacity using `LRTDepositPool.getTotalAssetDeposits()`, which includes assets freshly deposited in the same block. An attacker holding rsETH can deposit LST into the deposit pool to inflate the apparent available amount, immediately initiate a withdrawal that commits more assets than were previously available, and leave the pool at 100% commitment — blocking all other users from initiating new withdrawal requests until the attacker's withdrawal is processed (up to 8 days).

---

### Finding Description

`getAvailableAssetAmount` is defined as:

```solidity
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
}
``` [1](#0-0) 

`getTotalAssetDeposits` sums all asset locations including `assetLyingInDepositPool` — the live ERC-20 balance of the deposit pool contract:

```solidity
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
``` [2](#0-1) 

The full sum:

```solidity
return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
        + assetLyingUnstakingVault);
``` [3](#0-2) 

`initiateWithdrawal` enforces the limit and then increments `assetsCommitted`:

```solidity
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
``` [4](#0-3) 

Because `getTotalAssetDeposits` reflects the deposit pool balance in real time, a deposit made in the same transaction immediately raises `getAvailableAssetAmount`. The attacker can then commit the full inflated amount to a withdrawal request, driving `assetsCommitted` up to equal `totalAssets` and leaving `getAvailableAssetAmount == 0` for every subsequent caller.

---

### Impact Explanation

Once `assetsCommitted[asset] == getTotalAssetDeposits(asset)`, every call to `initiateWithdrawal` for that asset reverts with `ExceedAmountToWithdraw`. The withdrawal delay is hardcoded to 8 days:

```solidity
withdrawalDelayBlocks = 8 days / 12 seconds;
``` [5](#0-4) 

No new withdrawal requests can be queued until the attacker's request is unlocked and processed, or until a new depositor adds assets. This constitutes a **temporary freezing of funds** (Medium severity) — users holding rsETH cannot enter the withdrawal queue for up to 8 days.

---

### Likelihood Explanation

Any unprivileged user who holds rsETH and the corresponding LST (or ETH) can execute this in a single transaction. No flash loan is required — the attacker simply deposits LST to mint rsETH_A, then calls `initiateWithdrawal` using rsETH_A plus any pre-held rsETH_B to consume the full inflated available amount. The attack is cheap to execute and can be repeated each time the pool recovers.

---

### Recommendation

Exclude freshly deposited (uncommitted) assets from the available withdrawal calculation. One approach: track a separate `totalAssetsCommittedToDeposit` counter that is incremented on `depositAsset`/`depositETH` and decremented when assets are moved to a NodeDelegator, then subtract it from `getTotalAssetDeposits` inside `getAvailableAssetAmount`. Alternatively, base the available amount only on assets that have already been moved out of the deposit pool (NDCs + EigenLayer + unstaking vault), excluding `assetLyingInDepositPool` from the withdrawal availability calculation.

---

### Proof of Concept

**Setup:**
- 100 stETH total in protocol, 80 stETH already committed to pending withdrawals.
- `getAvailableAssetAmount(stETH)` = 100 − 80 = **20 stETH**.
- Alice holds rsETH_B worth 20 stETH.

**Attack:**

1. Alice calls `LRTDepositPool.depositAsset(stETH, 25e18, ...)`.
   - `assetLyingInDepositPool` increases by 25 stETH.
   - Alice receives rsETH_A worth 25 stETH.
   - `getTotalAssetDeposits(stETH)` = 125 stETH.
   - `getAvailableAssetAmount(stETH)` = 125 − 80 = **45 stETH**.

2. Alice calls `LRTWithdrawalManager.initiateWithdrawal(stETH, rsETH_A + rsETH_B)` committing 45 stETH.
   - Check passes: 45 ≤ 45.
   - `assetsCommitted[stETH]` = 80 + 45 = **125 stETH**.

3. `getAvailableAssetAmount(stETH)` = 125 − 125 = **0**.

4. Any other user calling `initiateWithdrawal(stETH, any_amount)` reverts with `ExceedAmountToWithdraw` for up to 8 days. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L395-396)
```text
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```
