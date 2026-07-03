### Title
ETH Deposit Limit Bypass via Incorrect Conditional Logic in `_checkIfDepositAmountExceedesCurrentLimit` - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies asymmetric logic between ETH and ERC20 token branches. The ETH branch omits the incoming deposit `amount` from the limit comparison, allowing any depositor to push total ETH deposits past the admin-configured cap.

### Finding Description
The function at lines 676–682 reads:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← missing + amount
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [1](#0-0) 

For ERC20 assets the check is `totalAssetDeposits + amount > limit` — correct. For ETH the check is `totalAssetDeposits > limit` — the incoming `amount` (`msg.value`) is never added. The function therefore returns `false` (i.e., "limit not exceeded") whenever the running total has not yet crossed the cap, regardless of how large the new deposit is.

This is called from `_beforeDeposit`, which is called by the public `depositETH` entry point: [2](#0-1) [3](#0-2) 

### Impact Explanation
The ETH deposit limit (`depositLimitByAsset`) is a protocol-level risk-management cap that controls how much ETH can be restaked into EigenLayer strategies. Because the cap is not enforced for ETH, any depositor can push total ETH deposits arbitrarily above the configured limit in a single transaction. This exposes the protocol to more EigenLayer slashing risk than the admin intended and violates the protocol's stated deposit ceiling — a "contract fails to deliver promised returns" impact.

**Impact: Low** — the protocol does not enforce its own ETH deposit limit; no direct fund theft, but the safety invariant is broken.

### Likelihood Explanation
The entry path is `depositETH`, a public `payable` function with no role restriction. Any depositor can trigger this when `totalAssetDeposits` is at or near (but not yet past) the limit. No special conditions or privileges are required.

### Recommendation
Add `amount` to the ETH branch, matching the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets ETH deposit limit to 1 000 ETH via `lrtConfig.depositLimitByAsset(ETH_TOKEN)`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` = 999 ETH.
3. Attacker calls `depositETH{value: 10 ether}(...)`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 > 1000` → `false` → deposit is allowed.
5. Total ETH deposits become 1 009 ETH — 9 ETH above the configured limit — with no revert. [1](#0-0)

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
