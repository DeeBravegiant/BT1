### Title
Token Decimal Mismatch in rsETH Swap Calculation Causes Massive User Fund Loss - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, RSETHPoolV3.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV3WithNativeChainBridge.sol)

### Summary
Every L2 pool contract's `viewSwapRsETHAmountAndFee(amount, token)` function computes the rsETH output as `amountAfterFee * tokenToETHRate / rsETHToETHrate` without normalizing `amountAfterFee` to 18 decimals first. Both `tokenToETHRate` and `rsETHToETHrate` are 18-decimal WAD values, so the result inherits the raw decimal precision of the deposited token. For a non-18-decimal token (e.g., USDC with 6 decimals or WBTC with 8 decimals), the minted rsETH amount is off by a factor of `10^(18 - tokenDecimals)`, causing the depositor to receive essentially zero rsETH while their full token balance is transferred into the pool and bridged to L1.

### Finding Description
`ChainlinkOracleForRSETHPoolCollateral.getRate()` correctly normalizes the Chainlink answer to 18 decimals:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [1](#0-0) 

However, the pool swap formula does not apply a corresponding normalization to the raw token `amount`:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

The same pattern is replicated verbatim in every pool variant: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The `addSupportedToken` function in each pool accepts any ERC-20 token and any oracle that returns a non-zero rate — there is no enforcement that the token must have 18 decimals: [7](#0-6) [8](#0-7) 

### Impact Explanation
When a non-18-decimal token (e.g., USDC, 6 decimals) is added as a supported collateral and a user calls `deposit(token, amount, referralId)`:

1. The full `amount` of tokens is transferred from the user to the pool.
2. `viewSwapRsETHAmountAndFee` returns an `rsETHAmount` that is `10^(18-6) = 10^12` times smaller than the correct value.
3. The user receives near-zero rsETH/wrsETH.
4. The tokens are subsequently bridged to L1 and deposited into the protocol — permanently lost to the depositor.

This constitutes **direct theft of user funds** (Critical impact under the allowed scope).

### Likelihood Explanation
The `addSupportedToken` function is gated behind `TIMELOCK_ROLE`, so the bug is latent until a non-18-decimal token is legitimately added. This is not a compromise — it is a normal protocol governance action. The protocol's `ChainlinkOracleForRSETHPoolCollateral` wrapper is explicitly designed to handle oracles of any decimal precision, signaling that the protocol intends to support arbitrary tokens. USDC and WBTC are natural candidates for future collateral expansion on L2s. Likelihood is **Low-to-Medium** (requires a governance action that has not yet occurred but is architecturally anticipated).

### Recommendation
Normalize `amountAfterFee` to 18 decimals before applying the rate formula. Fetch the token's `decimals()` and scale accordingly:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 1e18 / 10 ** tokenDecimals;
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

Apply this fix consistently across all five pool contracts.

### Proof of Concept
**Setup:** USDC (6 decimals) is added as a supported token with a `ChainlinkOracleForRSETHPoolCollateral` wrapping the USDC/ETH Chainlink feed. Assume:
- USDC/ETH rate: `0.0003e18` (i.e., 1 USDC ≈ 0.0003 ETH)
- rsETH/ETH rate: `1.05e18`
- User deposits `1000 USDC` → `amount = 1000e6`

**Actual calculation (buggy):**
```
amountAfterFee ≈ 1000e6  (assuming 0 fee for simplicity)
tokenToETHRate = 0.0003e18
rsETHToETHrate = 1.05e18
rsETHAmount = 1000e6 * 0.0003e18 / 1.05e18
            = 3e26 / 1.05e18
            ≈ 285_714_285   (≈ 285.7e6 raw units)
```

rsETH has 18 decimals, so the user receives `285.7e6 / 1e18 ≈ 2.857 × 10^-10 rsETH`.

**Expected calculation (correct):**
```
normalizedAmount = 1000e6 * 1e18 / 1e6 = 1000e18
rsETHAmount = 1000e18 * 0.0003e18 / 1.05e18 ≈ 285.7e18
```

The user should receive `≈ 285.7 rsETH` but instead receives `≈ 2.857 × 10^-10 rsETH`. The `1000 USDC` (≈ 0.3 ETH in value) is transferred to the pool and bridged to L1, permanently lost to the depositor.

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-34)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L762-764)
```text
    function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
        _addSupportedToken(token, oracle, bridge);
    }
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

**File:** contracts/pools/RSETHPoolV3.sol (L541-554)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
```

**File:** contracts/pools/RSETHPool.sol (L335-346)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
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
