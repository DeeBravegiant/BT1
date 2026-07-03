### Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Unlimited ETH Deposits Beyond the Configured Cap - (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check: for ERC-20 assets it correctly adds the incoming `amount` to `totalAssetDeposits` before comparing against the limit, but for ETH it omits `amount` entirely. As a result, the ETH deposit cap is never enforced on the incoming deposit, and any unprivileged depositor can push the protocol's ETH TVL arbitrarily beyond the configured limit in a single transaction.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` (called from `_beforeDeposit`, which is called from the public `depositETH`) contains the following branch:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount NOT added
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount added correctly
}
```

For ETH the guard only asks "has the limit already been exceeded?" — it never asks "will this deposit exceed the limit?" The ERC-20 branch correctly asks the latter question.

Concrete example:
- `depositLimitByAsset(ETH) = 100 ETH`
- `totalAssetDeposits(ETH) = 99 ETH`
- User calls `depositETH` with `msg.value = 1000 ETH`
- Check: `99 > 100` → `false` → deposit is allowed
- After deposit: `totalAssetDeposits = 1099 ETH`, 10× the intended cap

The entry path is fully unprivileged: `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit`.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The deposit limit is the protocol's primary risk-management gate for ETH exposure. With the check broken, any depositor can bypass it and push ETH TVL to an arbitrary multiple of the configured cap. The protocol does not immediately lose funds, but it silently violates its own invariant: the cap is advertised as a hard ceiling yet is never enforced for ETH.

---

### Likelihood Explanation

**High.** The condition is trivially reachable by any depositor whenever `totalAssetDeposits < depositLimit` (i.e., during normal operation before the cap is hit). No special role, timing, or front-running is required. A single large ETH deposit is sufficient.

---

### Recommendation

Add `amount` to the ETH branch, mirroring the ERC-20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

---

### Proof of Concept

1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 100 ether`.
2. Protocol accumulates `99 ether` in ETH TVL through normal deposits.
3. Attacker calls `depositETH{value: 1000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 1000 ether)` evaluates `99 ether > 100 ether` → `false`.
5. `_beforeDeposit` does not revert; `_mintRsETH` mints rsETH for the attacker.
6. Protocol now holds `1099 ether` of ETH TVL — 10× the configured cap — with no revert. [1](#0-0) [2](#0-1) [3](#0-2)

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
