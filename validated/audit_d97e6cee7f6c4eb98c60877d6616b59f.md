### Title
`_depositToAave` Does Not Check Aave Supply Cap Before Depositing Full Amount - (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager._depositToAave()` attempts to deposit the full unlocked ETH amount into Aave without first checking the pool's remaining supply capacity. When Aave's WETH supply cap is partially consumed by other depositors, the entire deposit reverts, and the protocol earns zero yield on ETH that could have been partially deposited up to the available cap.

---

### Finding Description

The `unlockQueue()` function, after redeeming ETH from the unstaking vault, attempts to deposit the full `assetAmountUnlocked` into Aave:

```solidity
// contracts/LRTWithdrawalManager.sol:310-316
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
    try this.depositToAaveExternal(assetAmountUnlocked) { }
    catch (bytes memory reason) {
        emit AaveDepositFailed(assetAmountUnlocked, reason);
        // Silently fail if Aave deposit fails (e.g., pool at max capacity)
        // Funds remain in contract for withdrawals
    }
}
```

`depositToAaveExternal` calls `_depositToAave`, which deposits the full amount with no capacity pre-check:

```solidity
// contracts/LRTWithdrawalManager.sol:894-901
function _depositToAave(uint256 amount) internal {
    if (amount == 0) return;
    aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
    totalETHDepositedToAave += amount;
    emit ETHDepositedToAave(amount, totalETHDepositedToAave);
}
```

The contract already has a `getAaveAvailableCapacity()` view function that correctly computes remaining Aave supply cap space:

```solidity
// contracts/LRTWithdrawalManager.sol:659-679
function getAaveAvailableCapacity() external view returns (uint256 availableCapacity) {
    ...
    (, uint256 supplyCap) = aaveDataProvider.getReserveCaps(WETH_ADDRESS);
    if (supplyCap == 0) return type(uint256).max;
    uint256 totalSupply = aaveAWETH.totalSupply();
    uint256 supplyCapWei = supplyCap * 1e18;
    if (supplyCapWei > totalSupply) {
        availableCapacity = supplyCapWei - totalSupply;
    }
}
```

This function is **never called** inside `_depositToAave`. If `assetAmountUnlocked` exceeds the remaining Aave supply cap (e.g., 500 ETH to deposit but only 400 ETH of cap remains), Aave reverts the entire deposit. The `try/catch` silently swallows the failure, leaving all 500 ETH in the contract earning zero yield — even though 400 ETH could have been deposited successfully.

This is structurally identical to the referenced SuperPool bug: the code checks only an internal limit (or no limit at all) rather than the actual remaining capacity of the external pool, causing a full deposit failure when a partial deposit would have succeeded.

---

### Impact Explanation

The protocol fails to deposit ETH into Aave even when partial capacity exists, causing the Aave yield integration to silently produce zero yield. User funds are not lost (they remain in the contract for withdrawals), but the protocol fails to deliver the promised Aave yield returns on ETH that could have been deposited. This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

Aave WETH supply caps are a real, governance-set parameter on mainnet. As the protocol scales and more ETH is unlocked in large batches, the probability of `assetAmountUnlocked` exceeding the remaining Aave supply cap increases. Any normal market participant depositing WETH into Aave can reduce the available capacity, making this condition reachable without any attacker action.

---

### Recommendation

Before depositing, cap the deposit amount at the available Aave capacity:

```solidity
function _depositToAave(uint256 amount) internal {
    if (amount == 0) return;

    // Check available Aave supply cap
    if (address(aaveDataProvider) != address(0)) {
        (, uint256 supplyCap) = aaveDataProvider.getReserveCaps(WETH_ADDRESS);
        if (supplyCap != 0) {
            uint256 totalSupply = aaveAWETH.totalSupply();
            uint256 supplyCapWei = supplyCap * 1e18;
            uint256 available = supplyCapWei > totalSupply ? supplyCapWei - totalSupply : 0;
            if (amount > available) amount = available;
        }
    }

    if (amount == 0) return;
    aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
    totalETHDepositedToAave += amount;
    emit ETHDepositedToAave(amount, totalETHDepositedToAave);
}
```

---

### Proof of Concept

1. Aave WETH supply cap is 10,000 ETH; current total supply is 9,700 ETH → 300 ETH of capacity remains.
2. Operator calls `unlockQueue(ETH, ...)` which unlocks 500 ETH from the unstaking vault.
3. `unlockQueue` calls `this.depositToAaveExternal(500 ETH)`.
4. `_depositToAave(500 ETH)` calls `aaveWETHGateway.depositETH{value: 500 ETH}(...)`.
5. Aave reverts with `SupplyCapExceeded` because 500 > 300.
6. The `try/catch` catches the revert; 500 ETH sits idle in `LRTWithdrawalManager`, earning zero yield.
7. Had the code capped the deposit at `min(500, 300) = 300 ETH`, 300 ETH would have been deposited and earned yield, with 200 ETH remaining in the contract. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L309-317)
```text
        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L656-679)
```text
    /// @notice Get available capacity in Aave for WETH deposits
    /// @return availableCapacity The amount of WETH that can still be deposited to Aave
    /// @dev Returns 0 if Aave is not configured or if supply cap is reached
    function getAaveAvailableCapacity() external view returns (uint256 availableCapacity) {
        if (address(aaveAWETH) == address(0) || address(aaveDataProvider) == address(0)) {
            revert InvalidAaveConfiguration();
        }

        // Get supply cap from Aave Data Provider
        (, uint256 supplyCap) = aaveDataProvider.getReserveCaps(WETH_ADDRESS);

        // If supply cap is 0, it means unlimited capacity
        if (supplyCap == 0) return type(uint256).max;

        // Get current total supply of aWETH
        uint256 totalSupply = aaveAWETH.totalSupply();

        // Convert supply cap to wei and calculate available capacity
        uint256 supplyCapWei = supplyCap * 1e18;
        if (supplyCapWei > totalSupply) {
            availableCapacity = supplyCapWei - totalSupply;
        }
        // else returns 0 (pool is at or over capacity)
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L892-901)
```text
    /// @dev Deposit ETH to Aave v3
    /// @param amount The amount of ETH to deposit
    function _depositToAave(uint256 amount) internal {
        if (amount == 0) return;

        aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
        totalETHDepositedToAave += amount;

        emit ETHDepositedToAave(amount, totalETHDepositedToAave);
    }
```

**File:** contracts/interfaces/aave/IPoolDataProvider.sol (L40-46)
```text
    /**
     * @notice Returns the caps parameters of the reserve
     * @param asset The address of the underlying asset of the reserve
     * @return borrowCap The borrow cap of the reserve
     * @return supplyCap The supply cap of the reserve
     */
    function getReserveCaps(address asset) external view returns (uint256 borrowCap, uint256 supplyCap);
```
