### Title
ETH Deposit Limit Bypass Due to Missing `amount` in Limit Check — (`contracts/LRTDepositPool.sol`)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric condition for ETH versus ERC-20 assets. For ERC-20 tokens it correctly tests `totalAssetDeposits + amount > depositLimit`, but for ETH it tests only `totalAssetDeposits > depositLimit`, omitting the incoming deposit amount. Any depositor can therefore push ETH holdings arbitrarily beyond the configured cap in a single transaction.

### Finding Description
In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` (lines 676–682):

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));  // ← amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The ETH branch evaluates whether the **current** total already exceeds the limit, not whether the **post-deposit** total would. As long as `totalAssetDeposits ≤ depositLimit`, the function returns `false` regardless of how large `amount` is, so `_beforeDeposit` never reverts with `MaximumDepositLimitReached` for ETH.

This is structurally identical to the reference vulnerability: a limit-checking function uses an incomplete condition (missing one necessary operand), causing the guard to be silently bypassed.

### Impact Explanation
`depositLimitByAsset[ETH_TOKEN]` is the protocol's primary risk-management cap on ETH exposure. Bypassing it allows:
- Unbounded rsETH minting beyond the intended ceiling, diluting the exchange rate if excess ETH cannot be fully restaked.
- EigenLayer strategy capacity to be exceeded, leaving ETH idle in the deposit pool and not earning restaking rewards.
- The protocol to hold more ETH than operators have planned for, undermining the invariant that `getTotalAssetDeposits(ETH) ≤ depositLimit`.

Impact: **Low — Contract fails to deliver promised returns** (deposit cap is a protocol invariant; its violation does not directly steal funds but breaks the accounting guarantee and can degrade yield for all rsETH holders).

### Likelihood Explanation
The entry point is the public, payable `depositETH` function — no special role or privilege is required. Any depositor who observes that `totalAssetDeposits ≤ depositLimit` can send an arbitrarily large ETH deposit in a single call. The condition is trivially satisfiable whenever the protocol is not already over-limit.

### Recommendation
Add `amount` to the ETH branch, matching the ERC-20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
Assume `depositLimitByAsset[ETH_TOKEN] = 1000 ETH` and `totalAssetDeposits = 999 ETH`.

1. Attacker calls `depositETH{value: 500 ETH}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500e18)`.
3. ETH branch evaluates `999e18 > 1000e18` → `false`. Guard does not revert.
4. `_mintRsETH` mints rsETH for 500 ETH, pushing total ETH deposits to 1499 ETH — 49.9 % above the configured limit.
5. The same attacker (or any other depositor) can repeat this indefinitely as long as `totalAssetDeposits ≤ depositLimit` at the start of each transaction. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
