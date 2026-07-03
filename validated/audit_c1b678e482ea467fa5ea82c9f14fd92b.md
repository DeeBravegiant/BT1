### Title
ETH Deposit Limit Not Enforced Due to Missing `amount` in Boundary Check - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an incorrect condition for ETH deposits. The incoming deposit `amount` is included in the limit check for ERC20 tokens but is omitted for ETH, allowing any depositor to push the ETH TVL above the configured `depositLimitByAsset` cap.

### Finding Description
In `_checkIfDepositAmountExceedesCurrentLimit` (lines 676–682), the ETH branch checks only whether the *current* total already exceeds the limit, not whether the *new* deposit would cause it to exceed the limit:

```solidity
// LRTDepositPool.sol L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← missing `+ amount`
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for ERC20
}
```

For ERC20 assets the check is `totalAssetDeposits + amount > limit`, which correctly blocks a deposit that would breach the cap. For ETH the check is `totalAssetDeposits > limit`, which only blocks deposits when the cap is *already* exceeded. A depositor whose `msg.value` would push the total from just-below-limit to well-above-limit passes the guard and the deposit is accepted.

This is structurally identical to the `BytesUtil.compare` bug: an incorrect boundary condition in a utility function causes the guard to be skipped for one class of inputs (ETH) while working correctly for another (ERC20), producing wrong results that go unnoticed.

The reachable call path is:

`depositETH()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, msg.value)` → returns `false` (no limit breach) even when `totalAssetDeposits + msg.value > limit`. [1](#0-0) 

### Impact Explanation
Any unprivileged depositor can call `depositETH` with an amount that exceeds the remaining ETH deposit cap. The protocol mints rsETH for the full amount, accepting more ETH than the admin-configured risk limit allows. The deposit limit is the primary on-chain risk-management control for ETH exposure; bypassing it means the protocol silently takes on more ETH than intended. This maps to **Low — contract fails to deliver promised returns, but doesn't lose value**: the deposit cap guarantee is broken, but no funds are directly stolen. [2](#0-1) 

### Likelihood Explanation
The entry point (`depositETH`) is public and requires no special role. Any depositor who monitors the current ETH TVL and sees it approaching the limit can send a single transaction that exceeds the cap. No front-running, governance capture, or privileged access is required. The condition is always wrong for ETH regardless of state, so the bug is triggered on every deposit that would cross the boundary. [3](#0-2) 

### Recommendation
Add `amount` to the ETH branch, matching the ERC20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [4](#0-3) 

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` returns `999 ether` (1 ETH below cap).
3. Attacker calls `depositETH{value: 100 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 100 ether)` evaluates `999 ether > 1000 ether` → `false` → no revert.
5. 100 ETH is accepted and rsETH is minted; total ETH in protocol is now 1099 ETH, 99 ETH above the configured cap.
6. The same call with an ERC20 asset would evaluate `999 + 100 > 1000` → `true` → reverts with `MaximumDepositLimitReached`. [1](#0-0) [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L648-663)
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
