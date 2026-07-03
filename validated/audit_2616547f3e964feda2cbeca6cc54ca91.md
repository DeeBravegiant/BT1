### Title
ETH Deposit Limit Invariant Check Omits New Deposit Amount, Allowing Deposit Cap Bypass — (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric inequality for ETH versus LSTs. For LSTs the incoming amount is included in the comparison; for ETH it is not. This mirrors the original report's pattern: an invariant that is correctly enforced in one code path is silently omitted in a parallel path, allowing the invariant to be broken by an unprivileged caller.

---

### Finding Description

In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`:

```solidity
// contracts/LRTDepositPool.sol  L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount NOT added
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount added
}
``` [1](#0-0) 

For every LST the guard is `totalAssetDeposits + amount > limit`, which correctly rejects a deposit that would push the total over the cap. For ETH the guard is `totalAssetDeposits > limit`, which only rejects a deposit when the cap is **already** exceeded. When `totalAssetDeposits == limit` the condition evaluates to `false`, the deposit is accepted, and after `depositETH` returns `totalAssetDeposits > limit`.

The entry point is `depositETH`, which is public and callable by any user:

```solidity
// contracts/LRTDepositPool.sol  L76-L93
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
``` [2](#0-1) 

`_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit` and then `getRsETHAmountToMint`, so rsETH is minted for the over-limit deposit:

```solidity
// contracts/LRTDepositPool.sol  L648-L670
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected)
    private view returns (uint256 rsethAmountToMint)
{
    ...
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
        revert MaximumDepositLimitReached();
    }
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    ...
}
``` [3](#0-2) 

The deposit limit is the protocol's primary risk-management cap on ETH exposure. `getAssetCurrentLimit` correctly uses `>` to detect an already-exceeded state, but the deposit guard for ETH never adds the incoming amount, so the cap can be breached in a single transaction. [4](#0-3) 

---

### Impact Explanation

An unprivileged depositor can push total ETH deposits above the configured `depositLimitByAsset` cap. The deposit limit is the protocol's mechanism for bounding ETH exposure to EigenLayer strategies and NodeDelegators. Exceeding it means the protocol accepts and restakes more ETH than governance intended, breaking the invariant `totalAssetDeposits ≤ depositLimit` for ETH while the same invariant is correctly enforced for every LST. No funds are directly stolen, but the protocol fails to deliver the promised deposit-cap guarantee.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

Any unprivileged user can reach this path via `depositETH`. The precondition `totalAssetDeposits == depositLimit` can be engineered: an attacker monitors on-chain state and deposits exactly `depositLimit − totalAssetDeposits` ETH to bring the total to the boundary, then immediately deposits an arbitrary additional amount. No privileged role is required.

---

### Recommendation

Add the incoming `amount` to the ETH branch, matching the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

---

### Proof of Concept

1. Read `D = depositLimitByAsset(ETH_TOKEN)` and `T = getTotalAssetDeposits(ETH_TOKEN)`.
2. Call `depositETH{value: D − T}(...)` to bring `totalAssetDeposits` exactly to `D`.
3. Call `depositETH{value: X}(...)` for any `X > 0`.
   - Inside `_checkIfDepositAmountExceedesCurrentLimit`: `totalAssetDeposits (= D) > D` → `false` → no revert.
   - `_mintRsETH` mints rsETH for the full `X`.
4. After the call `getTotalAssetDeposits(ETH_TOKEN) = D + X > D`, breaking the deposit cap invariant while the caller holds freshly minted rsETH.

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
    }
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

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
