### Title
ETH Deposit Limit Bypass Due to Missing `amount` in Limit Check - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit()` applies an asymmetric validation: for ERC-20 assets it correctly checks `totalAssetDeposits + amount > depositLimit`, but for ETH it omits the incoming `amount`, checking only `totalAssetDeposits > depositLimit`. Any depositor can therefore push ETH deposits arbitrarily beyond the configured `depositLimitByAsset` cap in a single transaction.

---

### Finding Description

In `contracts/LRTDepositPool.sol`, the internal guard function is:

```solidity
// lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount NOT included
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for ERC-20
}
```

For ETH the function returns `true` (i.e., "limit exceeded") only when the **pre-deposit** total already exceeds the cap. The `amount` being deposited is never added. Consequently, as long as `totalAssetDeposits ≤ depositLimit` at the moment of the call, the check passes regardless of how large `msg.value` is.

This guard is the sole enforcement point called from `_beforeDeposit` (line 661), which is itself called by the public `depositETH` entry point (line 87). [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

The `depositLimitByAsset` cap is the protocol's primary risk-management control over how much of each asset can be accepted. Bypassing it for ETH means:

1. The protocol accepts more ETH than its risk parameters allow.
2. `getRsETHAmountToMint` mints rsETH proportional to the full deposited amount, so rsETH supply grows beyond the intended ceiling.
3. The invariant `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset(ETH)` is silently violated, and `getAssetCurrentLimit` will subsequently return `0` — permanently reporting no remaining capacity even though the limit was already breached.

**Impact: Low** — the contract fails to deliver its promised deposit-limit guarantee for ETH, but no funds are directly stolen or frozen. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

The entry point `depositETH` is public and payable, requiring no special role. Any depositor who observes that `totalAssetDeposits` is at or near the limit can send a single large ETH deposit to breach it. No front-running, governance access, or external dependency is required. [3](#0-2) 

---

### Recommendation

Apply the same `+ amount` pattern used for ERC-20 assets to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This unifies the logic and ensures the post-deposit total is always validated against the cap, regardless of asset type. [1](#0-0) 

---

### Proof of Concept

**Setup:**
- `depositLimitByAsset[ETH_TOKEN]` = 1 000 ETH
- `getTotalAssetDeposits(ETH_TOKEN)` = 999 ETH (one ETH below the cap)

**Attack:**
1. Attacker calls `depositETH{value: 500 ether}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500 ether)`.
3. ETH branch evaluates `999e18 > 1000e18` → `false` → limit **not** exceeded.
4. `getRsETHAmountToMint` mints rsETH for the full 500 ETH.
5. `getTotalAssetDeposits(ETH_TOKEN)` is now 1 499 ETH — 499 ETH above the configured cap.
6. `getAssetCurrentLimit(ETH_TOKEN)` now returns `0`, permanently signalling the pool is full even though the limit was silently breached.

For ERC-20 assets the same scenario would correctly revert at step 3 because `999e18 + 500e18 > 1000e18` → `true`. [1](#0-0) [4](#0-3)

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

**File:** contracts/LRTDepositPool.sol (L399-409)
```text
    /// @notice gets the current limit of asset deposit
    /// @param asset Asset address
    /// @return currentLimit Current limit of asset deposit
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
