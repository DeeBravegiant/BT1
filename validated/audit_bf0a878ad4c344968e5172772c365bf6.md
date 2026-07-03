### Title
ETH Deposit Limit Bypass Due to Missing New Amount in Limit Check - (`contracts/LRTDepositPool.sol`)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` enforces the deposit cap for LST assets correctly by including the incoming deposit amount in the comparison, but omits the incoming amount for ETH. This allows any depositor to bypass the ETH deposit limit entirely in a single transaction, depositing an arbitrarily large amount of ETH as long as the current total has not already exceeded the cap.

### Finding Description
The function at lines 676–682 of `contracts/LRTDepositPool.sol` contains an asymmetric limit check:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount not included
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for LSTs
}
```

For LST assets the check is `totalAssetDeposits + amount > limit`, which correctly accounts for the incoming deposit. For ETH the check is only `totalAssetDeposits > limit`, which ignores the incoming `amount` entirely.

Consequently, the check only reverts when the total has *already* exceeded the limit from a prior deposit. Any deposit that arrives while `totalAssetDeposits ≤ limit` will pass, regardless of how large `amount` is.

**Attack scenario**:
1. Deposit limit for ETH is set to 1 000 ETH; `totalAssetDeposits = 0`.
2. Attacker calls `depositETH` with `msg.value = 100 000 ETH`.
3. Check: `0 > 1 000` → `false` → deposit is accepted.
4. `totalAssetDeposits` becomes 100 000 ETH — 100× the intended cap.
5. The attacker receives proportional rsETH; the protocol is now exposed to 100× the intended EigenLayer risk.

The same bypass works for any depositor, not just a sophisticated attacker, because the check is simply wrong for ETH.

### Impact Explanation
The ETH deposit limit is a risk-management control that caps the protocol's exposure to EigenLayer slashing and liquidity risk. Bypassing it allows the protocol to accumulate ETH far beyond the intended ceiling, diluting all rsETH holders' safety margin and potentially causing protocol insolvency if the excess ETH is slashed or becomes illiquid. At minimum, the contract fails to deliver its promised deposit-cap invariant.

**Impact**: Low (contract fails to deliver promised returns — deposit limit is not enforced for ETH). Escalates toward protocol insolvency if the limit was sized to bound EigenLayer slashing exposure.

### Likelihood Explanation
The entry path is the public, permissionless `depositETH` function. No special role, front-running, or multi-step setup is required. Any depositor with sufficient ETH can trigger this in a single transaction whenever `totalAssetDeposits ≤ depositLimit`.

### Recommendation
Include the incoming `amount` in the ETH branch, mirroring the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Assume `depositLimitByAsset[ETH_TOKEN] = 1_000e18` and `getTotalAssetDeposits(ETH_TOKEN) = 0`.
2. Call `LRTDepositPool.depositETH{value: 100_000e18}(0, "")`.
3. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 100_000e18)`.
4. Branch taken: `return (0 > 1_000e18)` → `false` → no revert.
5. `_mintRsETH` mints rsETH proportional to 100 000 ETH.
6. `getTotalAssetDeposits(ETH_TOKEN)` now returns 100 000 ETH, 100× the limit. [1](#0-0) [2](#0-1) [3](#0-2)

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
