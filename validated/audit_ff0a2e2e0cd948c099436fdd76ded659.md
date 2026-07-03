### Title
Oracle Precision Mismatch After `setRSETHOracle` / `setSupportedTokenOracle` — (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol)

---

### Summary

Every L2 pool contract exposes `setRSETHOracle` and `setSupportedTokenOracle` to update the oracle addresses used for swap pricing. Neither function validates that the new oracle returns values in the 1e18 precision that all `viewSwapRsETHAmountAndFee` calculations unconditionally assume. If a replacement oracle returns values at a different scale (e.g., 1e8 as raw Chainlink feeds do), every subsequent deposit will mint a wildly incorrect amount of wrsETH, enabling any depositor to drain the pool's pre-minted wrsETH reserves.

---

### Finding Description

All pool variants share the same swap pricing formula. For ETH deposits:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

For token deposits:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

Both branches hard-code the assumption that every oracle's `getRate()` returns a value scaled to 1e18.

The functions that update these oracles perform only a non-zero address check and a non-zero rate check:

```solidity
// RSETHPoolV3.sol – setRSETHOracle
function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(_rsETHOracle);
    rsETHOracle = _rsETHOracle;
    emit OracleSet(_rsETHOracle);
}
```

```solidity
// RSETHPoolV3.sol – setSupportedTokenOracle
function setSupportedTokenOracle(address token, address oracle)
    external onlyRole(TIMELOCK_ROLE) onlySupportedToken(token)
{
    UtilLib.checkNonZeroAddress(oracle);
    if (IOracle(oracle).getRate() == 0) revert UnsupportedOracle();
    supportedTokenOracle[token] = oracle;
    emit TokenOracleSet(token, oracle);
}
```

Neither function checks that `getRate()` falls within the 1e18-scaled range the arithmetic requires. By contrast, the L1 `LRTOracle.updatePriceOracleForValidated` does enforce a sanity bound (`price > 1e19 || price < 1e16` → revert), but this guard is absent from every L2 pool oracle setter.

The same pattern is replicated verbatim across:
- `RSETHPoolV3ExternalBridge.setRSETHOracle` / `setSupportedTokenOracle`
- `RSETHPoolV3WithNativeChainBridge.setRSETHOracle` / `setSupportedTokenOracle`
- `RSETHPool.setRSETHOracle` / `setSupportedTokenOracle`
- `RSETHPoolNoWrapper.setRSETHOracle` / `setSupportedTokenOracle`

---

### Impact Explanation

**Scenario A – rsETH oracle replaced with one returning 1e8 precision (e.g., a raw Chainlink feed):**

`rsETHToETHrate ≈ 1.05 × 10⁸` instead of `1.05 × 10¹⁸`.

```
rsETHAmount = 1e18 * 1e18 / 1.05e8 ≈ 9.52e27
```

A depositor sending 1 ETH receives ~9.52 × 10²⁷ wrsETH — approximately 10¹⁰ times the correct amount. The pool's entire pre-minted wrsETH reserve is drained in a single transaction. This is **direct theft of funds** (Critical).

**Scenario B – token oracle replaced with one returning 1e8 precision while rsETH oracle remains at 1e18:**

```
rsETHAmount = amountAfterFee * 1e8 / 1.05e18 ≈ amountAfterFee * 9.52e-11
```

Depositors receive effectively zero wrsETH for their tokens — **temporary freezing of user funds** (Medium).

---

### Likelihood Explanation

The TIMELOCK_ROLE must call `setRSETHOracle` or `setSupportedTokenOracle` with an oracle whose `getRate()` is not 1e18-scaled. This is a realistic misconfiguration during oracle migrations or upgrades (e.g., switching from a wrapper oracle to a raw Chainlink feed, or deploying a new oracle contract that omits the normalization step). The protocol's own `ChainlinkOracleForRSETHPoolCollateral` wrapper normalises correctly, but nothing in the setter enforces that only such wrappers are used. Likelihood is **Low** (requires privileged misconfiguration), but the consequence is immediate and exploitable by any depositor the moment the wrong oracle is live.

---

### Recommendation

Add a precision sanity check to every oracle setter, mirroring the guard already present in `LRTOracle.updatePriceOracleForValidated`:

```solidity
function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(_rsETHOracle);
    uint256 rate = IOracle(_rsETHOracle).getRate();
    if (rate < 1e16 || rate > 1e19) revert InvalidOraclePrecision();
    rsETHOracle = _rsETHOracle;
    emit OracleSet(_rsETHOracle);
}
```

Apply the same guard to `setSupportedTokenOracle` and `addSupportedToken` across all pool variants.

---

### Proof of Concept

1. Deploy a mock oracle whose `getRate()` returns `1.05e8` (raw Chainlink 8-decimal scale).
2. TIMELOCK_ROLE calls `RSETHPoolV3.setRSETHOracle(mockOracle)`. The call succeeds — `getRate() != 0` passes.
3. Attacker calls `deposit{value: 1 ether}("ref")`.
4. `viewSwapRsETHAmountAndFee(1e18)` computes:
   - `fee = 0` (feeBps = 0 for simplicity)
   - `rsETHToETHrate = 1.05e8`
   - `rsETHAmount = 1e18 * 1e18 / 1.05e8 ≈ 9.52e27`
5. `wrsETH.mint(attacker, 9.52e27)` — attacker receives ~10¹⁰× the correct wrsETH amount, draining the pool. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L531-537)
```text
    /// @dev Sets the rsETHOracle address
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L575-589)
```text
    function setSupportedTokenOracle(
        address token,
        address oracle
    )
        external
        onlyRole(TIMELOCK_ROLE)
        onlySupportedToken(token)
    {
        UtilLib.checkNonZeroAddress(oracle);
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenOracle[token] = oracle;
        emit TokenOracleSet(token, oracle);
    }
```

**File:** contracts/LRTOracle.sol (L101-108)
```text
    function updatePriceOracleForValidated(address asset, address priceOracle) external onlyLRTAdmin {
        // Sanity check: oracle price must have precision between 1e16 and 1e19
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
        }
        updatePriceOracleFor(asset, priceOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L751-756)
```text
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L812-826)
```text
    function setSupportedTokenOracle(
        address token,
        address oracle
    )
        external
        onlyRole(TIMELOCK_ROLE)
        onlySupportedToken(token)
    {
        UtilLib.checkNonZeroAddress(oracle);
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenOracle[token] = oracle;
        emit TokenOracleSet(token, oracle);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L589-593)
```text
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L631-645)
```text
    function setSupportedTokenOracle(
        address token,
        address oracle
    )
        external
        onlyRole(TIMELOCK_ROLE)
        onlySupportedToken(token)
    {
        UtilLib.checkNonZeroAddress(oracle);
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenOracle[token] = oracle;
        emit TokenOracleSet(token, oracle);
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
