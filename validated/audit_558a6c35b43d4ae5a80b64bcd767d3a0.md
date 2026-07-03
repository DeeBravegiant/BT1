### Title
Incomplete ETH Deposit Limit Check Missing Incoming Amount — (`File: contracts/LRTDepositPool.sol`)

---

### Summary

`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol` omits the incoming `amount` from the limit comparison when the asset is ETH, while correctly including it for ERC20 assets. This allows any depositor to push total ETH deposits above the configured cap.

---

### Finding Description

In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`, the limit check is asymmetric:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount included
}
```

For every ERC20 asset the check is `totalAssetDeposits + amount > limit`, which correctly rejects a deposit that would push the total over the cap. For ETH the check is `totalAssetDeposits > limit`, which only rejects a deposit when the cap is **already exceeded**. A deposit that would bring the total from `limit` to `limit + amount` passes the check and is accepted.

The function is called unconditionally from `_beforeDeposit` (line 661), which is the sole pre-flight guard for both `depositETH` (line 87) and `depositAsset` (line 111).

---

### Impact Explanation

**Low — Contract fails to deliver promised returns.**

The ETH deposit cap (`depositLimitByAsset[ETH_TOKEN]`) is a protocol-level risk parameter set by the admin. Because the incoming `amount` is excluded from the comparison, any depositor can call `depositETH` when `totalAssetDeposits == depositLimit` and the transaction will succeed, minting rsETH and accepting ETH beyond the intended ceiling. The protocol therefore fails to enforce its own deposit limit invariant for ETH, violating the guarantee that total ETH exposure stays within the configured bound. No direct fund theft or permanent freeze results, but the protocol silently accepts more ETH than it is configured to handle.

---

### Likelihood Explanation

**Medium.** The condition `totalAssetDeposits == depositLimit` is a normal operational state (the cap is reached). Any unprivileged depositor who monitors on-chain state can call `depositETH` at that moment. No special privileges, front-running, or brute-force are required.

---

### Recommendation

Add `amount` to the ETH branch, mirroring the ERC20 branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

---

### Proof of Concept

1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Protocol accumulates `getTotalAssetDeposits(ETH_TOKEN) == 1000 ether` (cap exactly reached).
3. Attacker calls `depositETH{value: 100 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `1000 ether > 1000 ether` → `false` → limit not exceeded.
5. `_mintRsETH` mints rsETH for the attacker; total ETH deposits become `1100 ether`, 10 % above the configured cap.
6. Repeat for additional depositors; the cap is effectively unenforced for ETH. [1](#0-0) [2](#0-1) [3](#0-2)

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
