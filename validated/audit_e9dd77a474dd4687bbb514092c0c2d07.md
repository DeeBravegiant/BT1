### Title
ETH Deposit Limit Not Enforced at Boundary Due to Missing Amount in Comparison — (`contracts/LRTDepositPool.sol`)

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies two different comparison expressions for ETH vs. ERC-20 assets. The ERC-20 path correctly adds the incoming deposit amount before comparing against the limit, but the ETH path omits the deposit amount entirely, using only the pre-deposit total. This is a direct off-by-one / missing-operand analog to the reported `<=` vs. `<` class: the boundary value (`totalAssetDeposits == depositLimit`) is handled incorrectly, allowing a deposit that should be rejected.

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` is called from `_beforeDeposit` for every ETH and ERC-20 deposit: [1](#0-0) 

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For ERC-20 tokens the check is `totalAssetDeposits + amount > limit`, which correctly blocks any deposit that would push the running total past the cap. For ETH the check is `totalAssetDeposits > limit`, which only returns `true` when the total **already** exceeds the limit. When `totalAssetDeposits == limit` the function returns `false` (not exceeded), so the deposit is accepted and the total is pushed above the configured cap by the full `msg.value`.

The call path is: [2](#0-1) 

```
depositETH(minRSETHAmountExpected, referralId)
  └─ _beforeDeposit(ETH_TOKEN, msg.value, minRSETHAmountExpected)
       └─ _checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, msg.value)
            └─ returns false when totalAssetDeposits == depositLimit  ← bug
``` [3](#0-2) 

### Impact Explanation

The ETH deposit cap (`depositLimitByAsset[ETH_TOKEN]`) is a risk-management control that bounds the protocol's EigenLayer exposure. When `totalAssetDeposits` is exactly at the limit, any depositor can call `depositETH` with an arbitrarily large `msg.value` and bypass the cap entirely. After the deposit, `totalAssetDeposits > limit`, so subsequent deposits are blocked — but the overshoot can be as large as a single transaction allows. This means the protocol silently accepts more ETH restaking exposure than the admin intended, violating the promised deposit ceiling. Impact: **Low — contract fails to deliver promised returns (deposit limit not enforced for ETH)**.

### Likelihood Explanation

`depositETH` is a public, permissionless function callable by any user. The trigger condition (`totalAssetDeposits == depositLimit`) is a realistic steady-state: the limit is designed to be reached. Any depositor who monitors on-chain state can exploit this at the exact moment the total hits the cap. Likelihood: **Medium**.

### Recommendation

Apply the same expression used for ERC-20 tokens to the ETH branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This makes both paths consistent and correctly blocks any deposit that would push the total above the configured limit.

### Proof of Concept

1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Protocol accumulates `totalAssetDeposits(ETH) = 1000 ether` through normal deposits.
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500 ether)` evaluates `1000 ether > 1000 ether` → `false` → deposit not blocked.
5. 500 ETH is accepted; `totalAssetDeposits` becomes 1500 ETH, 50% above the intended cap.
6. The ERC-20 equivalent call with the same numbers would evaluate `1000 + 500 > 1000` → `true` → correctly reverted. [1](#0-0)

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
