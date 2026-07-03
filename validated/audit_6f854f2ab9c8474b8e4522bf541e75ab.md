### Title
ETH Deposit Limit Bypass Due to Missing `amount` in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` uses an incorrect validation for ETH deposits: it checks whether the *current* total deposits already exceed the limit, but omits the incoming `amount` from the comparison. The ERC20 branch correctly includes `amount`. This asymmetry allows any user to deposit ETH beyond the protocol's configured `depositLimitByAsset`, minting excess rsETH in violation of the TVL cap.

### Finding Description
In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
``` [1](#0-0) 

For ETH, the guard only checks whether deposits *already* exceed the limit before the transaction. It does not account for the `amount` being deposited in the current call. For ERC20 assets, the check correctly adds `amount` to `totalAssetDeposits` before comparing against the limit.

This is structurally identical to the reference vulnerability: the validation uses an incorrect reference point (pre-deposit state instead of post-deposit state), allowing the limit to be bypassed by any amount.

The call path is:
1. User calls `depositETH(minRSETHAmountExpected, referralId)` with any `msg.value`
2. `_beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, ...)` is called
3. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, msg.value)` returns `false` as long as `totalAssetDeposits <= depositLimitByAsset(ETH_TOKEN)` — regardless of `msg.value`
4. `_mintRsETH(rsethAmountToMint)` mints rsETH for the full deposit [2](#0-1) [3](#0-2) 

### Impact Explanation
The `depositLimitByAsset` is a governance-set TVL cap per asset, used to bound protocol risk exposure. When the ETH deposit limit is at or near its cap, any depositor can still push total ETH deposits arbitrarily above the limit in a single transaction, minting rsETH beyond what the protocol's risk parameters allow. This violates the protocol's promised deposit ceiling and can lead to excess rsETH supply relative to the intended backing, constituting a failure to deliver promised protocol invariants.

**Impact: Low** — Contract fails to deliver promised returns (the deposit limit invariant is broken for ETH). In a worst-case scenario where the limit is a hard safety cap, this could escalate to protocol insolvency.

### Likelihood Explanation
Any unprivileged ETH depositor can trigger this. No special role, front-running, or external dependency is required. The condition is met whenever `totalAssetDeposits <= depositLimitByAsset(ETH_TOKEN)` — i.e., any time the protocol has not already exceeded its limit, a single large deposit can push it over. This is a constant, always-reachable condition for any active depositor.

### Recommendation
Add `amount` to the ETH branch of the check, mirroring the ERC20 branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [1](#0-0) 

### Proof of Concept
Assume `depositLimitByAsset(ETH_TOKEN) = 100 ether` and `totalAssetDeposits(ETH_TOKEN) = 99.9 ether`.

1. User calls `depositETH{value: 50 ether}(0, "")`.
2. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 50 ether)` evaluates `99.9 ether > 100 ether` → `false` (not exceeded).
3. The deposit proceeds; `totalAssetDeposits` becomes `149.9 ether`, 49.9 ETH above the limit.
4. rsETH is minted for the full 50 ETH deposit.

For ERC20 assets under the same conditions, step 2 would evaluate `99.9 + 50 > 100` → `true`, reverting with `MaximumDepositLimitReached`. The ETH path silently allows the bypass.

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
