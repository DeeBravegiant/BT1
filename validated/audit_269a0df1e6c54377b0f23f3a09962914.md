### Title
Uninitialized `rsETHPrice` in `LRTOracle` Causes Division-by-Zero on All Deposit Calls - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.initialize()` does not set `rsETHPrice`, leaving it at the Solidity default of `0`. Every deposit path in `LRTDepositPool` divides by `rsETHPrice`, so all deposits revert with a division-by-zero panic until an explicit call to `updateRSETHPrice()` is made.

### Finding Description
`LRTOracle.initialize()` only sets `lrtConfig` and emits an event: [1](#0-0) 

`rsETHPrice` is declared as a storage variable but is never assigned a non-zero value during initialization: [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`: [3](#0-2) 

This is called unconditionally from both `depositETH()` and `depositAsset()` via `_beforeDeposit()`: [4](#0-3) [5](#0-4) 

`_updateRsETHPrice()` does set `rsETHPrice = 1 ether` when `rsethSupply == 0`, but only when explicitly called: [6](#0-5) 

`updateRSETHPrice()` is permissionless (`public whenNotPaused`), so any caller can trigger the fix: [7](#0-6) 

### Impact Explanation
Between contract deployment and the first call to `updateRSETHPrice()`, every call to `depositETH()` or `depositAsset()` reverts with a division-by-zero panic. No user funds are at risk of loss because the revert prevents any state change, but the protocol fails to deliver its core promised service (accepting deposits) during this window.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation
The window exists on every fresh deployment or proxy upgrade that resets storage. Because `updateRSETHPrice()` is permissionless, any user who encounters the revert can self-remediate by calling it directly. The window is therefore short in practice, but it is a real, reachable failure mode for any depositor who acts before the price is seeded.

### Recommendation
Initialize `rsETHPrice` to `1 ether` inside `LRTOracle.initialize()`, mirroring the logic already present in `_updateRsETHPrice()` for the zero-supply case:

```solidity
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
+   rsETHPrice = 1 ether;
+   highestRsethPrice = 1 ether;
    emit UpdatedLRTConfig(lrtConfigAddr);
}
```

### Proof of Concept
1. Deploy `LRTOracle` proxy and call `initialize(lrtConfigAddr)`. `rsETHPrice` is `0`.
2. Do **not** call `updateRSETHPrice()`.
3. Call `LRTDepositPool.depositETH{value: 1 ether}(0, "")`.
4. Execution reaches `getRsETHAmountToMint()` → `(1e18 * assetPrice) / 0` → EVM division-by-zero panic → revert.
5. Call `updateRSETHPrice()` (permissionless). `rsETHPrice` is now `1 ether`.
6. Repeat step 3 — deposit succeeds.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTDepositPool.sol (L87-87)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
```

**File:** contracts/LRTDepositPool.sol (L111-111)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
