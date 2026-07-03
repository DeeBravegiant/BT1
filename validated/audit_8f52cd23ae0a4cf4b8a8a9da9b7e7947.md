### Title
Token Decimal Mismatch in `viewSwapRsETHAmountAndFee` Causes Incorrect rsETH Minting for Non-18-Decimal Collateral Tokens - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, RSETHPoolV3.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPoolNoWrapper.sol, RSETHPool.sol)

---

### Summary

All L2 pool contracts compute the rsETH amount to mint for a deposited ERC-20 token using the formula `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate`. This formula implicitly assumes the deposited token has 18 decimals. No decimal normalization is applied, and `_addSupportedToken` does not validate that the token has 18 decimals. If a token with fewer decimals (e.g., 6 for USDC) is added as a supported collateral, the formula produces an rsETH output that is off by a factor of `10^(18 - tokenDecimals)`, causing massive over-minting and protocol insolvency.

---

### Finding Description

Every L2 pool contract exposes a `deposit(address token, uint256 amount, ...)` path that calls `viewSwapRsETHAmountAndFee(amount, token)`:

```solidity
// RSETHPoolV3ExternalBridge.sol L442-452
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;

uint256 rsETHToETHrate = getRate();
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`ChainlinkOracleForRSETHPoolCollateral.getRate()` normalises the Chainlink answer to 1e18 precision (price of **one full token** in ETH):

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L34
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

For an 18-decimal token (e.g., wstETH), `amountAfterFee` is already in 1e18 units, so the product `amountAfterFee * tokenToETHRate` is in 1e36 units and dividing by `rsETHToETHrate` (1e18) yields a correct 1e18-scaled rsETH amount.

For a 6-decimal token (e.g., USDC), `amountAfterFee` is in 1e6 units. The oracle still returns the price of **one full USDC** in ETH (e.g., 3e14 for $0.0003 ETH). The product is therefore in 1e20 units, and dividing by `rsETHToETHrate` (1e18) yields an rsETH amount that is `10^12` times larger than correct.

The `_addSupportedToken` function performs no decimal check:

```solidity
// RSETHPoolV3ExternalBridge.sol L882-900
function _addSupportedToken(address token, address oracle, address bridge) internal {
    UtilLib.checkNonZeroAddress(token);
    UtilLib.checkNonZeroAddress(oracle);
    UtilLib.checkNonZeroAddress(bridge);
    if (supportedTokenOracle[token] != address(0)) revert AlreadySupportedToken();
    if (tokenBridge[token] != address(0)) revert AlreadySupportedToken();
    if (IOracle(oracle).getRate() == 0) revert UnsupportedOracle();
    supportedTokenList.push(token);
    ...
}
```

The same unchecked formula and the same `_addSupportedToken` pattern appear identically in `RSETHPoolV3.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, `RSETHPoolNoWrapper.sol`, and `RSETHPool.sol`.

The reverse-swap path `viewSwapAssetToPremintedRsETH` is symmetrically broken:

```solidity
// RSETHPoolV3ExternalBridge.sol L531
tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
```

For a 6-decimal token this returns `10^12` times more tokens than the pool holds, draining the pool.

---

### Impact Explanation

**Critical – Protocol insolvency / direct theft of funds.**

A depositor who deposits 1 USDC (1e6 raw units, tokenToETHRate ≈ 3e14, rsETHToETHrate ≈ 1.05e18) receives:

```
rsETHAmount = 1e6 * 3e14 / 1.05e18 ≈ 285 rsETH
```

Correct value: `0.000286 rsETH` (≈ 2.86e14 raw units).

The attacker receives ~1,000,000× more rsETH than the deposited value warrants. Repeating this with any non-trivial USDC amount drains the entire rsETH supply, leaving legitimate holders with unbacked tokens and making the protocol insolvent.

---

### Likelihood Explanation

**Medium.** The protocol already supports multiple L2 chains and is actively expanding its collateral set via `addSupportedToken`. USDC, USDT, and other 6-decimal stablecoins are natural candidates for future collateral on L2s. The admin adding such a token is a plausible operational step, not a malicious act; the code provides no guard to prevent it. No private-key compromise or governance capture is required—only a routine admin call to `addSupportedToken`.

---

### Recommendation

1. In `_addSupportedToken`, assert that the token has exactly 18 decimals:
   ```solidity
   require(IERC20Metadata(token).decimals() == 18, "Only 18-decimal tokens supported");
   ```
2. Alternatively, store each token's decimal count at registration time and normalise `amountAfterFee` before the rate multiplication:
   ```solidity
   uint8 dec = tokenDecimals[token];
   uint256 normalizedAmount = amountAfterFee * 10**(18 - dec);
   rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
   ```
3. Apply the same fix to `viewSwapAssetToPremintedRsETH`.

---

### Proof of Concept

**Setup:** Deploy `RSETHPoolV3ExternalBridge` on a testnet. Call `addSupportedToken(USDC, chainlinkUSDCOracle, bridge)`. USDC has 6 decimals; the Chainlink USDC/ETH feed returns ~3e14 (0.0003 ETH per USDC).

**Attack:**
1. Attacker calls `deposit(USDC, 1_000_000 /* 1 USDC */, "")`.
2. `viewSwapRsETHAmountAndFee(1_000_000, USDC)` computes:
   - `fee = 0` (feeBps = 0 for simplicity)
   - `tokenToETHRate = 3e14`
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1e6 * 3e14 / 1.05e18 ≈ 285e18` → **285 rsETH**
3. Pool mints 285 wrsETH to the attacker for a $1 deposit.
4. Attacker redeems wrsETH on L1 for ~285 ETH worth of assets.
5. Protocol is insolvent.

**Affected lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L442-452)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L882-900)
```text
    function _addSupportedToken(address token, address oracle, address bridge) internal {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);
        UtilLib.checkNonZeroAddress(bridge);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (tokenBridge[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;
        tokenBridge[token] = bridge;

        emit AddSupportedToken(token, oracle, bridge);
```

**File:** contracts/pools/RSETHPoolV3.sol (L324-334)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L360-370)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L301-311)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L336-346)
```text
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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
