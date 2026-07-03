### Title
Insufficient Deposit Limit Check for ETH Omits the Deposit Amount, Allowing Any Depositor to Bypass the ETH Cap - (File: contracts/LRTDepositPool.sol)

---

### Summary

`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool` applies an asymmetric bounds check: the ERC-20 branch correctly includes the incoming `amount` in the comparison, but the ETH branch silently drops it. Any unprivileged depositor can call `depositETH` and push the protocol's ETH holdings arbitrarily above the configured `depositLimitByAsset` cap.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` is the sole gate that enforces the per-asset deposit cap before rsETH is minted:

```solidity
// contracts/LRTDepositPool.sol  L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For every ERC-20 LST the check is `totalAssetDeposits + amount > limit`, which correctly rejects a deposit that would push the total over the cap. For ETH the check is only `totalAssetDeposits > limit`, which:

1. **Ignores the incoming `amount` entirely.** A deposit of any size passes as long as the current total has not already exceeded the limit.
2. **Misses the equality boundary.** When `totalAssetDeposits == limit` the check returns `false`, so one more deposit of any size is still accepted.

The function is called unconditionally from `_beforeDeposit` (L661), which is called by the public `depositETH` entry point (L87). No other guard exists for the ETH cap. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The `depositLimitByAsset` cap for ETH is the protocol's primary safety bound on how much native ETH it will accept and restake through EigenLayer. Bypassing it means:

- The protocol mints rsETH and accepts ETH beyond the intended ceiling, violating the invariant that `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset(ETH)`.
- The `getAssetCurrentLimit` view function (L402-L409) will return 0 or underflow once the limit is exceeded, giving integrators and front-ends incorrect data.
- No direct fund theft or freeze occurs in isolation; the excess ETH is restaked normally. The impact is that the protocol does not enforce the cap it promises to enforce for ETH, while correctly enforcing it for all LSTs. [4](#0-3) 

---

### Likelihood Explanation

**High.** `depositETH` is a public, permissionless function callable by any address. No role, whitelist, or additional condition stands between a depositor and the flawed check. The attacker-controlled entry path is:

1. Observe `totalAssetDeposits(ETH) ≤ depositLimitByAsset(ETH)` (the check will return `false`).
2. Call `depositETH{value: largeAmount}(0, "")`.
3. `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` returns `false` (limit not exceeded per the broken check) → rsETH is minted → ETH is accepted.

No special timing, front-running, or privileged access is required. [3](#0-2) 

---

### Recommendation

Apply the same combined check used for ERC-20 tokens to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    // unified check: include the incoming amount for both ETH and ERC-20
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This mirrors the fix recommended in the external report (changing `offset > dest.len()` to `offset + source.len() >= dest.len()`): include the new quantity in the bounds comparison so the boundary and over-limit cases are both caught.

---

### Proof of Concept

Assume `depositLimitByAsset(ETH) = 100 ether` and `getTotalAssetDeposits(ETH) = 99 ether`.

1. Attacker calls `depositETH{value: 500 ether}(0, "")`.
2. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 500 ether)`:
   - `totalAssetDeposits = 99 ether`
   - ETH branch: `99 ether > 100 ether` → `false` → limit not exceeded.
3. `_beforeDeposit` does not revert; rsETH is minted for 500 ETH.
4. `getTotalAssetDeposits(ETH)` is now 599 ETH — nearly 6× the intended cap — with no revert.

For ERC-20 (e.g., stETH) the same attempt with `depositAmount = 500 ether` would evaluate `99 + 500 > 100` → `true` → `MaximumDepositLimitReached` revert, confirming the asymmetry. [1](#0-0) [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
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

**File:** contracts/LRTConfig.sol (L23-23)
```text
    mapping(address token => uint256 amount) public depositLimitByAsset;
```
