### Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` uses two different comparison formulas depending on whether the asset is ETH or an ERC20 token. The ETH branch omits the incoming deposit `amount` from the comparison, while the ERC20 branch correctly includes it. This asymmetric conditional logic — analogous to H-06's wrong-branch-taken pattern — allows ETH deposits to exceed the configured deposit limit by exactly one deposit amount.

### Finding Description
In `_checkIfDepositAmountExceedesCurrentLimit`, the function branches on `asset == LRTConstants.ETH_TOKEN`:

```solidity
// contracts/LRTDepositPool.sol lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For ERC20 assets, the check correctly asks: "would this deposit push total deposits past the limit?" For ETH, the check only asks: "are total deposits already past the limit?" — it never adds `amount`. Consequently, when `totalAssetDeposits == depositLimit`, the ETH branch returns `false` (deposit allowed), and the deposit proceeds, pushing total ETH deposits to `depositLimit + amount`.

This function is called from `_beforeDeposit`, which is the sole guard invoked by the public `depositETH` entry point:

```solidity
// contracts/LRTDepositPool.sol lines 86-93
function depositETH(...) external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN) {
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    ...
}
```

```solidity
// contracts/LRTDepositPool.sol lines 648-670
function _beforeDeposit(...) private view returns (uint256 rsethAmountToMint) {
    ...
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
        revert MaximumDepositLimitReached();
    }
    ...
}
```

### Impact Explanation
The deposit limit is a protocol-level safety cap on ETH concentration. When `totalAssetDeposits` reaches exactly `depositLimitByAsset(ETH_TOKEN)`, any subsequent ETH deposit bypasses the guard and is accepted. The protocol mints rsETH proportional to the deposited ETH (via the oracle price), so no direct fund theft occurs, but the safety invariant is violated: the protocol holds more ETH than the admin-configured ceiling allows. This matches **Low: Contract fails to deliver promised returns, but doesn't lose value** — the deposit limit promise is broken without direct asset loss.

### Likelihood Explanation
As the protocol accumulates ETH deposits over time, `totalAssetDeposits` naturally approaches `depositLimitByAsset`. Any unprivileged depositor calling `depositETH` at the exact moment `totalAssetDeposits == limit` triggers the bypass. No special permissions, front-running, or external compromise are required — only a standard ETH deposit at the boundary condition.

### Recommendation
Apply the same formula used for ERC20 assets to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // include amount
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets ETH deposit limit to 1 000 ETH via `lrtConfig.depositLimitByAsset(ETH_TOKEN)`.
2. Cumulative ETH deposits reach exactly 1 000 ETH (`totalAssetDeposits == 1000e18`).
3. Depositor calls `depositETH{value: 10 ether}(...)`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 10e18)` evaluates `1000e18 > 1000e18` → `false`.
5. `_beforeDeposit` does **not** revert; rsETH is minted for the depositor.
6. Total ETH in protocol is now 1 010 ETH — 10 ETH above the configured limit.
7. Repeat for every subsequent depositor until the admin raises or resets the limit. [1](#0-0) [2](#0-1) [3](#0-2)

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
