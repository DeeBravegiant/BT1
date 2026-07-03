### Title
Minimum Deposit Requirement Permanently Locks Remaining LST Deposit Capacity Near the Cap - (File: contracts/LRTDepositPool.sol)

### Summary

In `LRTDepositPool._beforeDeposit`, a `minAmountToDeposit` floor check and a `depositLimitByAsset` ceiling check interact such that when the remaining deposit capacity for an LST asset falls below `minAmountToDeposit`, no deposit can ever succeed. The remaining capacity is permanently inaccessible to any depositor, and the protocol can never reach its configured deposit limit.

### Finding Description

`_beforeDeposit` enforces two sequential guards:

```solidity
// contracts/LRTDepositPool.sol lines 657-663
if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
    revert InvalidAmountToDeposit();
}

if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [1](#0-0) 

The ceiling check for LST assets inside `_checkIfDepositAmountExceedesCurrentLimit` is:

```solidity
// contracts/LRTDepositPool.sol lines 680-681
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
``` [2](#0-1) 

`minAmountToDeposit` is a protocol-wide floor stored in state and settable by the LRT admin:

```solidity
// contracts/LRTDepositPool.sol lines 30, 282-284
uint256 public minAmountToDeposit;
...
function setMinAmountToDeposit(uint256 minAmountToDeposit_) external onlyLRTAdmin {
    minAmountToDeposit = minAmountToDeposit_;
``` [3](#0-2) [4](#0-3) 

`depositLimitByAsset` is set per-asset in `LRTConfig`:

```solidity
// contracts/LRTConfig.sol line 23
mapping(address token => uint256 amount) public depositLimitByAsset;
``` [5](#0-4) 

**The deadlock:** Let `R = depositLimitByAsset(asset) - totalAssetDeposits` (remaining capacity). When `0 < R < minAmountToDeposit`:

- Any deposit `d < minAmountToDeposit` → reverts `InvalidAmountToDeposit` (floor check).
- Any deposit `d >= minAmountToDeposit` → `totalAssetDeposits + d > depositLimitByAsset` → reverts `MaximumDepositLimitReached` (ceiling check).

No value of `d` can satisfy both guards simultaneously. The remaining capacity `R` is permanently inaccessible.

### Impact Explanation

The protocol can never reach its configured `depositLimitByAsset` for any LST asset once the remaining capacity drops below `minAmountToDeposit`. The deposit limit is a protocol-level fundraising target; failing to reach it means the protocol fails to deliver its promised deposit capacity. No user funds are lost, but the protocol permanently under-collects relative to its configured ceiling.

**Impact:** Low — Contract fails to deliver promised returns, but doesn't lose value.

### Likelihood Explanation

This condition arises naturally and without any attacker action. As organic deposits accumulate and `totalAssetDeposits` approaches `depositLimitByAsset`, the remaining capacity shrinks. The moment it falls below `minAmountToDeposit` (which can be a non-trivial value such as 0.01 ETH or higher), the deadlock is permanent until an admin intervenes by raising the limit or lowering the minimum. The entry path is the public `depositAsset` function, callable by any user. [6](#0-5) 

### Recommendation

In `_beforeDeposit`, relax the minimum deposit check when the deposit amount exactly fills the remaining capacity:

```solidity
uint256 remainingCapacity = lrtConfig.depositLimitByAsset(asset) - getTotalAssetDeposits(asset);

if (depositAmount == 0 || (depositAmount < minAmountToDeposit && depositAmount != remainingCapacity)) {
    revert InvalidAmountToDeposit();
}
```

This mirrors the fix recommended in the reference report and allows a depositor to fill the last slot even if it is smaller than `minAmountToDeposit`.

### Proof of Concept

1. Admin sets `depositLimitByAsset(stETH) = 100_000e18` and `minAmountToDeposit = 0.01e18`.
2. Organic depositors bring `totalAssetDeposits(stETH)` to `99_999.995e18`. Remaining capacity = `0.005e18`.
3. Alice calls `depositAsset(stETH, 0.005e18, ...)`:
   - `0.005e18 < minAmountToDeposit (0.01e18)` → reverts `InvalidAmountToDeposit`.
4. Alice calls `depositAsset(stETH, 0.01e18, ...)`:
   - `99_999.995e18 + 0.01e18 = 100_000.005e18 > 100_000e18` → reverts `MaximumDepositLimitReached`.
5. No deposit amount can succeed. The remaining `0.005e18` capacity is permanently locked and the protocol never reaches its 100,000 stETH target. [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L30-30)
```text
    uint256 public minAmountToDeposit;
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

**File:** contracts/LRTDepositPool.sol (L282-284)
```text
    function setMinAmountToDeposit(uint256 minAmountToDeposit_) external onlyLRTAdmin {
        minAmountToDeposit = minAmountToDeposit_;
        emit MinAmountToDepositUpdated(minAmountToDeposit_);
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

**File:** contracts/LRTConfig.sol (L23-23)
```text
    mapping(address token => uint256 amount) public depositLimitByAsset;
```
