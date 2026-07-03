### Title
Token Decimal Assumption in `viewSwapRsETHAmountAndFee` Causes Incorrect rsETH Minting for Non-18-Decimal Collateral Tokens - (File: contracts/pools/RSETHPoolV3.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol)

### Summary
Every L2 pool contract's `viewSwapRsETHAmountAndFee(uint256 amount, address token)` computes the rsETH output as `amountAfterFee * tokenToETHRate / rsETHToETHrate` without normalising `amountAfterFee` to 18 decimals first. The oracle always returns a 1e18-normalised price for **one full token**, but `amountAfterFee` is expressed in the token's raw units. For any supported token whose decimals differ from 18 the formula silently produces a wildly wrong result: a 6-decimal token (e.g. USDC) causes users to receive ~1e12× fewer rsETH than owed; a >18-decimal token causes users to receive orders-of-magnitude more rsETH than owed, draining the pool.

### Finding Description
`viewSwapRsETHAmountAndFee` (token overload) is present identically in at least five production pool contracts:

```
contracts/pools/RSETHPool.sol          line 346
contracts/pools/RSETHPoolNoWrapper.sol line 311
contracts/pools/RSETHPoolV3.sol        line 334
contracts/pools/RSETHPoolV3ExternalBridge.sol
contracts/pools/RSETHPoolV3WithNativeChainBridge.sol
```

The shared formula is:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`tokenToETHRate` is fetched from `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
uint256 normalizedPrice =
    uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

This normalises the **Chainlink feed answer** to 1e18 and represents the ETH value of **one full token** (e.g. 1 USDC = 4e14, 1 wstETH ≈ 1.05e18). `rsETHToETHrate` is similarly 1e18-normalised.

`amountAfterFee`, however, is the raw ERC-20 unit amount supplied by the caller. For a token with `d` decimals, one full token = `1e(d)` raw units. The formula is only correct when `d == 18`. For any other value:

| Token decimals | Error factor | Direction |
|---|---|---|
| 6 (USDC/USDT) | ÷ 1e12 | User receives 1e12× too few rsETH |
| 8 (WBTC) | ÷ 1e10 | User receives 1e10× too few rsETH |
| 20 (hypothetical) | × 1e2 | User receives 100× too many rsETH |

`addSupportedToken` performs no decimal check:

```solidity
function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
    ...
    if (IOracle(oracle).getRate() == 0) revert UnsupportedOracle();
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    ...
}
```

The same structural gap exists in `LRTOracle._getTotalEthInProtocol()` on L1:

```solidity
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

where `totalAssetAmt` is in raw token units and `assetER` is the 1e18-normalised price per full token.

### Impact Explanation
**For a token with fewer than 18 decimals (e.g. USDC, 6 decimals):** A user depositing 1 000 USDC (`amountAfterFee = 1e9`) at a rate of 0.0004 ETH/USDC (`tokenToETHRate = 4e14`) and rsETH price 1.05 ETH (`rsETHToETHrate = 1.05e18`) receives:

```
1e9 * 4e14 / 1.05e18 ≈ 380 wei of rsETH
```

Correct value: `1000 * 4e14 / 1.05e18 * 1e18 ≈ 3.8e17` (0.38 rsETH). The user loses essentially all deposited value — their tokens are transferred to the pool but they receive negligible rsETH. This constitutes direct theft of user funds at-rest.

**For a token with more than 18 decimals:** The user receives orders-of-magnitude more rsETH than owed, draining the pool's rsETH reserves — direct theft from the protocol.

**Severity: Critical** (direct theft of user funds / protocol insolvency) once a non-18-decimal token is onboarded.

### Likelihood Explanation
The current supported token set (wstETH, weETH, and other ETH-correlated LSTs) all use 18 decimals, so the bug is dormant today. However:
- `addSupportedToken` imposes no decimal constraint.
- The protocol is explicitly designed to expand its supported token list.
- Common non-18-decimal tokens (USDC, USDT, WBTC) are natural candidates for future onboarding.
- No off-chain documentation or on-chain guard prevents this.

Likelihood: **Low** (requires a governance/timelock action to add a non-18-decimal token, but no technical barrier exists and the mistake is easy to make).

### Recommendation
1. In every `addSupportedToken` function, enforce that the token has exactly 18 decimals:
   ```solidity
   require(IERC20Metadata(token).decimals() == 18, "Token must have 18 decimals");
   ```
   or, alternatively, store the token's decimals and normalise `amountAfterFee` in `viewSwapRsETHAmountAndFee`:
   ```solidity
   uint8 d = IERC20Metadata(token).decimals();
   uint256 normalizedAmount = d <= 18
       ? amountAfterFee * 10 ** (18 - d)
       : amountAfterFee / 10 ** (d - 18);
   rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
   ```
2. Apply the same fix to `LRTOracle._getTotalEthInProtocol()` and `LRTDepositPool.getRsETHAmountToMint()`.

### Proof of Concept
Assume `RSETHPoolV3` has USDC (6 decimals) added as a supported token with a Chainlink oracle returning `4e14` (0.0004 ETH per USDC) and rsETH price `1.05e18`.

1. Attacker calls `deposit(usdcAddress, 1_000e6, "")` — deposits 1 000 USDC.
2. `viewSwapRsETHAmountAndFee(1_000e6, usdcAddress)` executes:
   - `fee = 1_000e6 * feeBps / 10_000` (negligible)
   - `amountAfterFee ≈ 1_000e6 = 1e9`
   - `tokenToETHRate = 4e14`
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1e9 * 4e14 / 1.05e18 ≈ 380`
3. User receives 380 wei of wrsETH instead of the correct `≈ 3.8e17` (0.38 rsETH).
4. The 1 000 USDC remain locked in the pool; the user has lost ~$400 of value.

Conversely, for a 20-decimal token at the same oracle rate, the user would receive `≈ 3.8e19` wrsETH instead of `3.8e17`, draining the pool's rsETH reserves 100× over. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L541-555)
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
    }
```

**File:** contracts/pools/RSETHPool.sol (L326-347)
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
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-312)
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

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```
