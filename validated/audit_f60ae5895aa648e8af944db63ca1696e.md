### Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Deposits Beyond the Configured Cap - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies a different, weaker guard for ETH than for ERC-20 assets. The ETH branch omits the incoming `amount` from the comparison, so the limit is never enforced at the moment of deposit: any user can deposit ETH even after the cap has been exactly reached.

### Finding Description
The function has two branches:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For every ERC-20 asset the prospective deposit amount is added before comparing against the cap. For ETH it is not. Consequently:

- When `totalAssetDeposits == depositLimit` the ETH branch returns `false` (not exceeded), so `_beforeDeposit` does not revert and `depositETH` mints rsETH for the full `msg.value`.
- After the call `totalAssetDeposits` becomes `depositLimit + msg.value`, silently breaching the cap.
- The public view helper `getAssetCurrentLimit` already returns `0` at this point (it uses `>` on the same comparison without `amount`), creating a visible inconsistency: the UI/integrators see "limit reached" while the contract still accepts deposits.

The entry path is fully unprivileged: any caller can invoke `depositETH` with any `msg.value` once the ETH total deposits equal the configured limit. [1](#0-0) [2](#0-1) 

### Impact Explanation
The deposit limit (`depositLimitByAsset`) is the protocol's primary risk-management cap on how much ETH can be restaked. Bypassing it allows the protocol to accumulate more ETH than the governance-approved ceiling, increasing EigenLayer slashing exposure and potentially violating off-chain risk commitments. No funds are directly stolen, but the protocol fails to deliver the promised deposit cap. This maps to **Low – Contract fails to deliver promised returns, but doesn't lose value**. [3](#0-2) 

### Likelihood Explanation
The condition `totalAssetDeposits == depositLimit` is a normal operational state (the cap is reached). Any depositor who monitors on-chain state can observe this and immediately call `depositETH` with an arbitrary amount. No special role, timing, or front-running is required. Likelihood is **High**.

### Recommendation
Add `amount` to the ETH branch, mirroring the ERC-20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [4](#0-3) 

### Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = X`.
2. Legitimate deposits accumulate until `getTotalAssetDeposits(ETH_TOKEN) == X`.
3. `getAssetCurrentLimit(ETH_TOKEN)` returns `0` — the cap appears reached.
4. Attacker calls `depositETH{value: Y}(0, "")` for any `Y > 0`.
5. Inside `_beforeDeposit`, `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, Y)` evaluates `X > X` → `false` → no revert.
6. `_mintRsETH` mints rsETH for `Y` ETH; `getTotalAssetDeposits(ETH_TOKEN)` is now `X + Y`, exceeding the cap. [5](#0-4) [6](#0-5)

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
