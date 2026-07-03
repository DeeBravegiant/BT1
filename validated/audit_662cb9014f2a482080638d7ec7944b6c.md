### Title
`LRTDepositPool::getRsETHAmountToMint()` Uses Stale `rsETHPrice`, Allowing Depositors to Receive Excess rsETH — (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTOracle` stores `rsETHPrice` as a cached state variable that is only updated when `updateRSETHPrice()` is explicitly called. `LRTDepositPool::getRsETHAmountToMint()` reads this cached value directly without triggering a refresh. Every user deposit (`depositETH`, `depositAsset`) computes the rsETH mint amount using this potentially stale price, allowing depositors to receive more rsETH than they are entitled to when the price has not been recently updated.

---

### Finding Description

`LRTOracle` maintains `rsETHPrice` as a persistent state variable: [1](#0-0) 

This value is only updated when `updateRSETHPrice()` (or `updateRSETHPriceAsManager()`) is explicitly called — typically by an off-chain keeper: [2](#0-1) 

`LRTDepositPool::getRsETHAmountToMint()` reads this cached value directly without refreshing it: [3](#0-2) 

This function is called by `_beforeDeposit()`, which is invoked by both `depositETH()` and `depositAsset()`: [4](#0-3) 

Since rsETH is a yield-bearing token, `rsETHPrice` increases monotonically over time as staking rewards accrue. Between keeper updates, the stored `rsETHPrice` is lower than the true current price. The mint formula is:

```
rsethAmountToMint = (depositAmount × assetPrice) / rsETHPrice
```

A stale (lower) `rsETHPrice` in the denominator produces a larger `rsethAmountToMint` than the depositor deserves.

The same stale value is also consumed by `LRTWithdrawalManager::getExpectedAssetAmount()` (used in `initiateWithdrawal` and `instantWithdrawal`): [5](#0-4) 

And by `RSETHPriceFeed::latestRoundData()`, which exposes the stale price to external lending protocols: [6](#0-5) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When `rsETHPrice` is stale (lower than the true current price), a depositor receives more rsETH than their deposit warrants. The excess rsETH represents a claim on protocol TVL that was not backed by the depositor's contribution — it is effectively extracted from the yield that had accrued to existing rsETH holders. Every deposit made while the price is stale dilutes the real yield of all current holders. [7](#0-6) 

---

### Likelihood Explanation

**Medium.**

`updateRSETHPrice()` is a public function callable by anyone, but it is not called atomically within the deposit flow. The protocol relies on an off-chain keeper to call it periodically. There is always a non-zero window between keeper updates during which `rsETHPrice` is stale. Any depositor — including one who deliberately monitors the staleness window — can exploit this without any special privileges. The attack requires no front-running, no admin compromise, and no external dependency: simply calling `depositETH()` or `depositAsset()` when the price has not been refreshed is sufficient. [8](#0-7) 

---

### Recommendation

Call `lrtOracle.updateRSETHPrice()` inside `getRsETHAmountToMint()` before reading `rsETHPrice`, or restructure the deposit flow to refresh the price atomically:

```diff
function getRsETHAmountToMint(address asset, uint256 amount) public view override returns (uint256 rsethAmountToMint) {
    address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
    ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);
+   lrtOracle.updateRSETHPrice();
    rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
}
```

Note: because `updateRSETHPrice()` is `whenNotPaused` and state-mutating, `getRsETHAmountToMint()` must be changed from `view` to a regular function, and the call chain through `_beforeDeposit()` must be updated accordingly. Alternatively, expose a pure computation path in `LRTOracle` that calculates the current price on-the-fly (analogous to `ReserveLibrary::getNormalizedIncome()` in the reference report) without requiring a state write.

---

### Proof of Concept

1. Note the current `rsETHPrice` stored in `LRTOracle` (e.g., `1.001e18` after some yield has accrued since the last keeper update).
2. The true current price, if `updateRSETHPrice()` were called now, would be `1.002e18` (additional yield has accrued).
3. Alice calls `depositETH{value: 10 ether}()`.
4. `getRsETHAmountToMint` computes: `10e18 * 1e18 / 1.001e18 ≈ 9.990 rsETH` (using stale price).
5. The correct amount at the true price would be: `10e18 * 1e18 / 1.002e18 ≈ 9.980 rsETH`.
6. Alice receives ~0.010 rsETH more than she is entitled to, extracted from the yield of existing holders.
7. Alice immediately calls `updateRSETHPrice()` (or waits for the keeper), locking in her diluted share at the now-correct price. [2](#0-1) [9](#0-8)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L648-665)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```
