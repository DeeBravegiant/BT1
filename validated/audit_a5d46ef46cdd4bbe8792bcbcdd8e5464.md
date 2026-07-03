### Title
ETH Deposit Limit Bypass Due to Missing `amount` in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit()` in `LRTDepositPool` applies an asymmetric limit check: for ERC20 LST assets it correctly tests `totalAssetDeposits + amount > depositLimit`, but for native ETH it only tests `totalAssetDeposits > depositLimit`, omitting the incoming deposit amount. Any depositor can therefore push total ETH holdings arbitrarily above the configured cap in a single transaction.

### Finding Description
The function `_checkIfDepositAmountExceedesCurrentLimit` contains a branch for `ETH_TOKEN` that does not include the incoming `amount` (i.e., `msg.value`) in the comparison:

```solidity
// contracts/LRTDepositPool.sol L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        // ❌ `amount` is ignored — only checks whether the limit is already exceeded
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    // ✅ ERC20 path correctly includes the incoming amount
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

For every ERC20 LST the guard is `totalAssetDeposits + amount > limit`, which correctly rejects a deposit that would push the total over the cap. For ETH the guard is `totalAssetDeposits > limit`, which only rejects a deposit when the cap is **already** exceeded before the deposit arrives. Any deposit that arrives while the total is still at or below the limit passes, regardless of how large `msg.value` is.

This is called from `_beforeDeposit`, which is called by the public `depositETH` function:

```solidity
// L76-L93
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
```

### Impact Explanation
The ETH deposit cap (`depositLimitByAsset[ETH_TOKEN]`) is a risk-management control that bounds how much ETH the protocol accepts before EigenLayer strategies or other downstream integrations are saturated. Bypassing it allows the protocol to accept far more ETH than intended, minting rsETH against it. If the excess ETH cannot be deployed into EigenLayer (strategy caps, queue limits), it sits idle while rsETH has already been minted, creating a backing shortfall and potentially preventing withdrawals — a temporary freeze of user funds.

**Impact: Medium — Temporary freezing of funds.**

### Likelihood Explanation
The entry point is the public, permissionless `depositETH` function. No special role or privilege is required. Any depositor who observes that `totalAssetDeposits < depositLimit` can send a single transaction with `msg.value` large enough to exceed the limit by any desired margin. The condition is trivially reachable on mainnet whenever the ETH cap has not yet been hit.

### Recommendation
Apply the same `+ amount` inclusion used for ERC20 assets to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept

**Setup:**
- `depositLimitByAsset[ETH_TOKEN]` = 1 000 ETH
- Current `getTotalAssetDeposits(ETH_TOKEN)` = 999 ETH (just under the cap)

**Attack:**
1. Attacker calls `depositETH{value: 500 ether}(0, "")`.
2. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500e18)` evaluates `999e18 > 1000e18` → `false` → check passes.
3. 500 ETH is accepted; `_mintRsETH` mints rsETH for the attacker.
4. `getTotalAssetDeposits(ETH_TOKEN)` is now 1 499 ETH — 499 ETH above the intended cap.
5. The cap is defeated; the attacker (or any depositor) can repeat until the contract's ETH balance is arbitrarily large.

**Root cause lines:** [1](#0-0) 

**Caller path:** [2](#0-1) 

**Limit configuration:** [3](#0-2)

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

**File:** contracts/LRTConfig.sol (L106-117)
```text
    function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
        UtilLib.checkNonZeroAddress(asset);
        if (depositLimit == 0) {
            revert InvalidDepositLimit();
        }
        if (isSupportedAsset[asset]) {
            revert AssetAlreadySupported();
        }
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
        depositLimitByAsset[asset] = depositLimit;
        emit AddedNewSupportedAsset(asset, depositLimit);
```
