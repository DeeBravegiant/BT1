### Title
Missing ETH Deposit Amount in Limit Check Allows Deposit Cap Bypass - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit()` omits the incoming ETH `amount` from the deposit-limit comparison for native ETH, while correctly including it for ERC-20 assets. Any depositor can therefore push the protocol's total ETH holdings above the configured cap in a single transaction.

### Finding Description
The function `_checkIfDepositAmountExceedesCurrentLimit` applies two different range-check formulas depending on the asset type:

```solidity
// contracts/LRTDepositPool.sol L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← `amount` absent
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For ERC-20 assets the prospective deposit `amount` is added to the running total before comparing against the cap, so the check correctly rejects any deposit that would breach the limit. For native ETH the `amount` parameter (`msg.value`) is silently dropped; only the pre-deposit total is tested. Consequently, when `totalAssetDeposits == depositLimit` the function returns `false` (not exceeded) and the deposit proceeds, pushing the actual total to `depositLimit + msg.value`.

This is structurally identical to the reported Plonk scalar-field bug: a value that should be bounded (`s < r_mod` / `totalDeposits + amount ≤ limit`) is accepted without the full range check, allowing the bound to be silently exceeded.

The vulnerable path flows through `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit`:

```solidity
// contracts/LRTDepositPool.sol L76-L93
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
```

```solidity
// contracts/LRTDepositPool.sol L648-L670
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected)
    private view returns (uint256 rsethAmountToMint)
{
    if (depositAmount == 0 || depositAmount < minAmountToDeposit) revert InvalidAmountToDeposit();
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) revert MaximumDepositLimitReached();
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
}
```

### Impact Explanation
The deposit limit is the protocol's primary on-chain risk-management gate for ETH exposure. Bypassing it allows the total ETH under management to grow beyond the intended cap, undermining the risk controls set by the manager. The protocol will accept and mint rsETH for deposits it was designed to reject, causing it to fail to deliver the promised deposit-cap guarantee. No direct fund theft occurs from the bypass alone, placing this in the **Low** tier: *Contract fails to deliver promised returns, but doesn't lose value*.

### Likelihood Explanation
The entry point `depositETH` is permissionless and payable. Any depositor can observe `getTotalAssetDeposits(ETH_TOKEN)` and `depositLimitByAsset(ETH_TOKEN)` on-chain and call `depositETH` with an arbitrarily large `msg.value` the moment the current total is at or below the cap. No special role, flash loan, or oracle manipulation is required.

### Recommendation
Include the incoming `amount` in the ETH branch of the limit check, matching the ERC-20 logic:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Assume `depositLimitByAsset(ETH_TOKEN) = 1000 ether` and `getTotalAssetDeposits(ETH_TOKEN) = 1000 ether` (exactly at the cap).
2. Attacker calls `depositETH{value: 500 ether}(0, "")`.
3. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `1000 ether > 1000 ether` → `false` → no revert.
4. `_mintRsETH` mints rsETH for the attacker; the protocol now holds 1500 ETH against a 1000 ETH cap.
5. The same attack works for any `msg.value > 0` whenever `totalAssetDeposits <= depositLimit`. [1](#0-0) [2](#0-1) [3](#0-2)

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
