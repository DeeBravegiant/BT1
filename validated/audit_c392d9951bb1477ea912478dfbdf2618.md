### Title
ETH Deposit Limit Check Omits Incoming `amount`, Allowing Limit Bypass - (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` uses an asymmetric condition for ETH versus ERC20 assets. The ETH branch checks only whether the *current* total already exceeds the limit, without adding the incoming deposit `amount`. The ERC20 branch correctly adds `amount` before comparing. Any depositor can push ETH deposits past the configured `depositLimitByAsset` cap.

---

### Finding Description

In `_checkIfDepositAmountExceedesCurrentLimit`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
``` [1](#0-0) 

For ETH, the guard evaluates `totalAssetDeposits > limit`, which is `false` whenever the current total is at or below the limit — regardless of how large the incoming deposit is. The ERC20 path correctly evaluates `totalAssetDeposits + amount > limit`.

The function is called from `_beforeDeposit`, which is the sole pre-deposit validation path for both `depositETH` and `depositAsset`: [2](#0-1) 

The protocol also exposes `getAssetCurrentLimit`, which computes remaining capacity as `depositLimit - totalAssetDeposits`, confirming the limit is intended as a hard cap: [3](#0-2) 

The ETH deposit entry point `depositETH` feeds directly into `_beforeDeposit` with `msg.value` as the amount: [4](#0-3) 

---

### Impact Explanation

The `depositLimitByAsset` cap is a risk-management control. When it is bypassed for ETH, the protocol accepts and stakes more ETH in EigenLayer than the configured ceiling allows. This violates the protocol's own invariant and exposes users to more slashing or liquidity risk than the limit was designed to bound. The contract fails to deliver the promised deposit-cap guarantee for ETH depositors and the protocol operator.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The condition is triggered on every ETH deposit where `totalAssetDeposits < depositLimit` but `totalAssetDeposits + amount > depositLimit`. Any ordinary depositor calling `depositETH` with a sufficiently large `msg.value` will silently bypass the cap. No special role or privilege is required.

---

### Recommendation

Add `amount` to the ETH branch, mirroring the ERC20 branch:

```diff
- return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
+ return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
``` [5](#0-4) 

---

### Proof of Concept

```
depositLimitByAsset(ETH) = 100 ETH
totalAssetDeposits(ETH)  =  95 ETH   (current state)
msg.value (amount)       =  10 ETH   (user deposit)

ETH branch check:  95 > 100  →  false  →  deposit ALLOWED
New total:         105 ETH   →  exceeds limit by 5 ETH

ERC20 branch (same numbers):
                   95 + 10 > 100  →  true  →  deposit REJECTED
```

A depositor calling `depositETH{value: 10 ether}(...)` when `totalAssetDeposits` is 95 ETH and the limit is 100 ETH will succeed, minting rsETH and staking the ETH in EigenLayer, despite the protocol's configured cap being breached.

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
