### Title
ETH Deposit Limit Bypass via Missing Amount in Limit Check — (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an inconsistent deposit-cap check: for ERC20 tokens the incoming `amount` is correctly added to `totalAssetDeposits` before comparing against the limit, but for ETH the `amount` is silently omitted. A single depositor can therefore bypass the ETH deposit limit entirely by sending any amount in one transaction, as long as the running total has not yet crossed the cap.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount absent
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount present
}
``` [1](#0-0) 

For ETH the predicate is "has the limit *already* been exceeded?" rather than "would *this* deposit exceed the limit?" The public entry point `depositETH` calls `_beforeDeposit`, which calls this function: [2](#0-1) [3](#0-2) 

Because `msg.value` is never added to `totalAssetDeposits` inside the check, a depositor can send an arbitrarily large ETH amount in a single call and the guard returns `false` (not exceeded) as long as the pre-call total is ≤ the limit.

The ERC20 path (`depositAsset`) is correct and is not affected. [4](#0-3) 

---

### Impact Explanation

The deposit limit is a risk-management parameter set by the admin, typically to cap protocol exposure to EigenLayer strategy capacity. A single unprivileged depositor can bypass it entirely, depositing an arbitrarily large amount of ETH in one transaction. Excess ETH that cannot be deployed to EigenLayer strategies sits idle in the deposit pool earning no yield, diluting returns for all rsETH holders.

**Impact: Low — Contract fails to deliver promised returns, but does not lose value.**

---

### Likelihood Explanation

The entry path is fully public (`depositETH` is `external payable`). No special role, flash loan, or multi-step setup is required. Any depositor with sufficient ETH can trigger this in a single transaction. The condition is trivially met whenever the protocol is below its cap.

---

### Recommendation

Include the deposit `amount` in the ETH branch, consistent with the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

---

### Proof of Concept

1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100 ether`.
2. `getTotalAssetDeposits(ETH_TOKEN)` returns `0`.
3. Attacker calls `depositETH{value: 10_000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `0 > 100 ether` → `false` → deposit proceeds.
5. Protocol now holds 10,000 ETH — 100× the intended cap. All subsequent ETH deposits are blocked, but the excess is already accepted and rsETH minted. [1](#0-0)

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
