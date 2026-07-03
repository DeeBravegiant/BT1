### Title
Division by Zero in `getRsETHAmountToMint` When `rsETHPrice` Is Uninitialized — (`File: contracts/LRTDepositPool.sol`)

### Summary
`LRTDepositPool.getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()` without a zero-guard. The `rsETHPrice` state variable in `LRTOracle` is initialized to `0` by default and remains `0` until `updateRSETHPrice()` is explicitly called. Any deposit attempt before that call triggers a Solidity 0.8 division-by-zero panic, reverting the transaction and temporarily freezing all user deposits.

### Finding Description
`LRTDepositPool.getRsETHAmountToMint` computes the rsETH mint amount as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.rsETHPrice()` reads the public state variable `rsETHPrice` in `LRTOracle`:

```solidity
uint256 public override rsETHPrice;
``` [2](#0-1) 

This variable is never set in `initialize`; it defaults to `0`. It is only updated by `_updateRsETHPrice()`, which is triggered by the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

Until `updateRSETHPrice()` is called at least once, `rsETHPrice == 0`. Every call to `depositETH` or `depositAsset` flows through `_beforeDeposit` → `getRsETHAmountToMint`, hitting the unguarded division:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
//                                                              ^^^^^^^^^^^^^^^^^^^^^^^^^
//                                                              panics when rsETHPrice == 0
``` [1](#0-0) 

The same unguarded pattern appears in `LRTWithdrawalManager.getExpectedAssetAmount`, which divides by `lrtOracle.getAssetPrice(asset)` with no zero-check, and in `_calculatePayoutAmount`, which divides by `assetPrice` — both reachable from user-facing withdrawal paths:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
``` [5](#0-4) 

### Impact Explanation
All calls to `depositETH` and `depositAsset` revert with a division-by-zero panic while `rsETHPrice == 0`. No user funds are lost, but the deposit interface is completely non-functional until the price is initialized. This matches the **Low** impact tier: *"Contract fails to deliver promised returns, but doesn't lose value."*

### Likelihood Explanation
The window exists from deployment until the first successful `updateRSETHPrice()` call. Because `updateRSETHPrice()` is public and permissionless, any actor (including the first depositor) can call it. The freeze is therefore short-lived in practice, but it is a guaranteed revert for any depositor who does not know to call the price-update function first. Likelihood is **Low**.

### Recommendation
Add an explicit zero-guard before each division that uses a price value:

```solidity
uint256 price = lrtOracle.rsETHPrice();
require(price > 0, "rsETHPrice not initialized");
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / price;
```

Apply the same pattern in `getExpectedAssetAmount` and `_calculatePayoutAmount`. Additionally, consider calling `updateRSETHPrice()` (or setting `rsETHPrice` to `1 ether`) inside `initialize` so the contract is safe from block zero.

### Proof of Concept
1. Deploy `LRTOracle` and `LRTDepositPool` (do **not** call `updateRSETHPrice()`).
2. Confirm `LRTOracle.rsETHPrice() == 0`.
3. Call `LRTDepositPool.depositETH{value: 1 ether}(0, "")`.
4. Transaction reverts with a Solidity panic (division by zero) because `getRsETHAmountToMint` executes `... / lrtOracle.rsETHPrice()` where `rsETHPrice == 0`.
5. Call `LRTOracle.updateRSETHPrice()` (public, no role required).
6. Repeat step 3 — deposit succeeds. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
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

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-833)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
```
