### Title
`rsETHPrice` Defaults to Zero Causing Division-by-Zero Revert That Blocks All Deposits - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTOracle.rsETHPrice` is a `uint256` state variable that is never set during `initialize()`, leaving it at its Solidity default of `0`. `LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`, so any deposit attempted before `updateRSETHPrice()` is called reverts with a division-by-zero panic, blocking all user deposits.

### Finding Description
`LRTOracle` declares `rsETHPrice` as a plain storage variable:

```solidity
uint256 public override rsETHPrice;   // defaults to 0
```

Its `initialize()` function sets only `lrtConfig` and emits an event — it never writes `rsETHPrice`:

```solidity
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
    emit UpdatedLRTConfig(lrtConfigAddr);
}
```

`rsETHPrice` is only written inside `_updateRsETHPrice()`, which is reached via the public `updateRSETHPrice()`. Until that call is made, `rsETHPrice == 0`.

Every user deposit path calls `_beforeDeposit()` → `getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Dividing by `0` causes an unconditional revert, so `depositETH()` and `depositAsset()` both revert for every caller until `updateRSETHPrice()` is invoked.

`updateRSETHPrice()` does handle the bootstrap case correctly — when `rsethSupply == 0` it sets `rsETHPrice = 1 ether` and returns early — but there is no code-level guarantee that this call is made before the first deposit attempt.

### Impact Explanation
All user deposits (`depositETH`, `depositAsset`) revert with a division-by-zero panic during the window between contract deployment and the first successful `updateRSETHPrice()` call. No funds are lost, but the contract fails to deliver its core promised function (accepting deposits) during that window. Impact: **Low — contract fails to deliver promised returns, but doesn't lose value**.

### Likelihood Explanation
`updateRSETHPrice()` is public and permissionless; any account can call it. When `rsethSupply == 0` the function always succeeds and sets `rsETHPrice = 1 ether`. In practice the window is short, but there is no on-chain enforcement that prevents a deposit from being attempted before the first price update. Likelihood: **Low**.

### Recommendation
Initialize `rsETHPrice` to `1 ether` inside `initialize()`, mirroring the bootstrap logic already present in `_updateRsETHPrice()`:

```solidity
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
    rsETHPrice = 1 ether;          // ← add this
    highestRsethPrice = 1 ether;   // ← add this
    emit UpdatedLRTConfig(lrtConfigAddr);
}
```

Alternatively, add a guard in `getRsETHAmountToMint()` that reverts with a descriptive error when `rsETHPrice == 0`, rather than silently panicking.

### Proof of Concept
1. Deploy `LRTConfig`, `LRTOracle` (call `initialize()`), `LRTDepositPool` (call `initialize()`).
2. Confirm `LRTOracle.rsETHPrice()` returns `0` — no `updateRSETHPrice()` has been called.
3. Call `LRTDepositPool.depositETH{value: 1 ether}(0, "")`.
4. Execution reaches `getRsETHAmountToMint()` at line 520: `(1e18 * assetPrice) / 0` → EVM division-by-zero panic → revert.
5. Deposit fails. Repeat for `depositAsset()` — same result.
6. Call `LRTOracle.updateRSETHPrice()` (public, no role required). Because `rsethSupply == 0`, it sets `rsETHPrice = 1 ether` and returns.
7. Repeat step 3 — deposit now succeeds.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
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
