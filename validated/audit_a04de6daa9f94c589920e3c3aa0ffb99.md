### Title
Division by Zero in `getRsETHAmountToMint()` When `rsETHPrice` Is Uninitialized - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`, which is a storage variable that starts at `0` and is never set in `LRTOracle.initialize()`. If `updateRSETHPrice()` has not been called before deposits are opened, every call to `depositETH()` and `depositAsset()` will revert with a division-by-zero panic, permanently blocking all user deposits until an operator manually calls the update function.

### Finding Description
`LRTOracle.rsETHPrice` is a plain `uint256` storage variable. Its value is `0` after contract deployment because `initialize()` does not set it:

```solidity
// LRTOracle.sol – initialize()
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
    emit UpdatedLRTConfig(lrtConfigAddr);
}
```

`rsETHPrice` is only written inside `_updateRsETHPrice()`, which is called by the permissionless `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`. Until one of those is called, `rsETHPrice == 0`.

Every user deposit flows through `_beforeDeposit()` → `getRsETHAmountToMint()`:

```solidity
// LRTDepositPool.sol line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

When `rsETHPrice == 0` this expression triggers a Solidity division-by-zero panic (revert), so both `depositETH()` and `depositAsset()` are completely blocked.

### Impact Explanation
All user deposits revert for as long as `rsETHPrice` remains `0`. No funds are lost, but the protocol fails to deliver its core promised function (accepting deposits and minting rsETH). This constitutes a **temporary freezing of funds / contract fails to deliver promised returns** — Medium impact.

### Likelihood Explanation
The window exists between contract deployment/upgrade and the first successful call to `updateRSETHPrice()`. Because `updateRSETHPrice()` is permissionless, any actor can close the window, but nothing in the contract enforces that it is called before deposits are enabled. A deployment script omission, a failed transaction, or a contract upgrade that resets state is sufficient to trigger the condition. Likelihood is **Medium**.

### Recommendation
Set a safe initial value for `rsETHPrice` inside `initialize()`:

```solidity
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
    rsETHPrice = 1 ether;          // safe bootstrap value
    highestRsethPrice = 1 ether;
    emit UpdatedLRTConfig(lrtConfigAddr);
}
```

Alternatively, add a zero-check guard in `getRsETHAmountToMint()`:

```solidity
uint256 currentRsETHPrice = lrtOracle.rsETHPrice();
if (currentRsETHPrice == 0) revert RsETHPriceNotInitialized();
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / currentRsETHPrice;
```

### Proof of Concept

1. Deploy `LRTOracle` and call `initialize()`. Observe `rsETHPrice == 0` (never written by `initialize()`).
2. Deploy `LRTDepositPool` pointing to the oracle. Do **not** call `updateRSETHPrice()`.
3. Any user calls `depositETH{value: 1 ether}(0, "")`.
4. Execution path: `depositETH` → `_beforeDeposit` → `getRsETHAmountToMint` → `(1e18 * assetPrice) / 0` → **panic revert**.
5. All deposits are blocked until an operator calls `updateRSETHPrice()`.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L648-669)
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
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L62-68)
```text
    /// @dev Initializes the contract
    /// @param lrtConfigAddr LRT config address
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L214-222)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```
