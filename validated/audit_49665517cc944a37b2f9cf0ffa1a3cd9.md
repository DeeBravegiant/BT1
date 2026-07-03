The code confirms this is a real vulnerability. Let me trace through the exact execution path.

**Key facts from the code:**

1. `LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` / `updateRSETHPriceAsManager()` is explicitly called — there is no automatic update on every transaction. [1](#0-0) [2](#0-1) 

2. `getExpectedAssetAmount` reads the stored (potentially stale) `rsETHPrice` directly: [3](#0-2) 

3. `instantWithdrawal` uses this stale price to compute `assetAmountUnlocked`, then burns the full `rsETHUnstaked` from the user with no freshness check or price-bounds guard: [4](#0-3) 

4. By contrast, `unlockQueue` enforces `minimumRsEthPrice` / `maximumRsEthPrice` bounds before processing — `instantWithdrawal` has no equivalent protection: [5](#0-4) 

---

### Title
Stale `rsETHPrice` in `instantWithdrawal` causes users to receive fewer assets than fair value — (`contracts/LRTWithdrawalManager.sol`)

### Summary
`LRTWithdrawalManager.instantWithdrawal` computes the asset payout using `LRTOracle.rsETHPrice`, a stored value that is only updated on explicit calls to `updateRSETHPrice()`. When yield accrues and the stored price lags behind the true price, users burn rsETH at the stale (lower) rate and receive fewer underlying assets than their rsETH is currently worth.

### Finding Description
`getExpectedAssetAmount` at line 593 of `LRTWithdrawalManager.sol` computes:

```
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)
```

`lrtOracle.rsETHPrice()` returns the stored `rsETHPrice` state variable from `LRTOracle.sol` (line 28), which is only refreshed when `_updateRsETHPrice()` is called. `instantWithdrawal` performs no staleness check and no price-bounds validation before burning the user's rsETH and computing their payout. If the stored price is lower than the true current price (because yield has accrued since the last update), the user receives:

```
assetAmountUnlocked = rsETHUnstaked * staleRsETHPrice / assetPrice
                    < rsETHUnstaked * trueRsETHPrice  / assetPrice
```

The user's rsETH is burned in full at line 229, but the payout is computed against the stale price. The shortfall remains in the `LRTUnstakingVault` — no funds are stolen, but the user is not made whole.

Note that `unlockQueue` (the non-instant withdrawal path) is protected by explicit `minimumRsEthPrice` / `maximumRsEthPrice` bounds passed by the operator, preventing processing at a stale price. `instantWithdrawal` has no equivalent guard.

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The user burns rsETH worth `X * truePrice` ETH but receives assets worth only `X * stalePrice` ETH. The difference is not extracted by an attacker; it stays in the protocol. No principal is permanently lost, but the user receives less than the fair redemption value of their rsETH at the time of the call.

### Likelihood Explanation
`updateRSETHPrice()` is public and permissionless, so any user could call it before withdrawing. However, the protocol does not enforce this, and users performing instant withdrawals have no on-chain prompt to do so. The staleness window depends on operator/bot update frequency. During periods of active yield accrual (e.g., after EigenLayer rewards are distributed), the gap between stored and true price can be material. No privileged role or external compromise is required — the condition arises naturally from normal protocol operation.

### Recommendation
Before computing `assetAmountUnlocked` in `instantWithdrawal`, call `updateRSETHPrice()` (or its internal equivalent) to ensure the price is fresh, or add a caller-supplied `minimumRsEthPrice` bound (mirroring the `unlockQueue` pattern) so users can protect themselves against receiving a payout computed from a stale price.

### Proof of Concept
```solidity
// Fork test sketch (no mainnet execution)
// 1. Deploy/fork with stale rsETHPrice (e.g., 1.00 ETH) while true price is 1.05 ETH
//    (simulate by skipping updateRSETHPrice() after yield accrual)
// 2. User holds 1e18 rsETH; asset is stETH with assetPrice = 1e18
// 3. Call instantWithdrawal(stETH, 1e18, "")
//    assetAmountUnlocked = 1e18 * 1.00e18 / 1e18 = 1.00e18 stETH
// 4. Call updateRSETHPrice() to refresh; rsETHPrice becomes 1.05e18
// 5. Fair payout would have been: 1e18 * 1.05e18 / 1e18 = 1.05e18 stETH
// 6. User received 1.00e18 instead of 1.05e18 — shortfall of 0.05e18 stETH
//    Invariant broken: assetAmountUnlocked < rsETHUnstaked * trueRsETHPrice / assetPrice
```

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L268-295)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
    {
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
