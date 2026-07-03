### Title
Single Chainlink Oracle for L2 Bridged wstETH Pricing Without Depeg Protection — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

The L2 deposit pools (`RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV3`) accept wstETH deposits and price them using a single Chainlink oracle wrapped by `ChainlinkOracleForRSETHPoolCollateral`. This oracle reports the L1 canonical wstETH/ETH exchange rate. If the L2 wstETH bridge is exploited and L2 wstETH depegs from its L1 counterpart, the oracle continues to report the full L1 price, allowing depositors to receive wrsETH at inflated value for worthless L2 wstETH — directly diluting all existing rsETH holders.

---

### Finding Description

The `ChainlinkOracleForRSETHPoolCollateral` contract wraps a single Chainlink aggregator and exposes `getRate()`:

```solidity
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();
    ...
    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
``` [1](#0-0) 

This oracle is assigned to wstETH in L2 pools via `supportedTokenOracle[token]`. The Chainlink wstETH/ETH feeds deployed on L2 chains (Arbitrum, Optimism, Base, etc.) derive their answer from the L1 wstETH contract's canonical `stEthPerToken()` rate — not from the L2 secondary market price of the bridged token. There is no secondary on-chain liquidity check (e.g., a Uniswap TWAP on the L2) to detect a bridge-level depeg.

The L2 pools use this rate directly in `viewSwapRsETHAmountAndFee`:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

The wstETH support in L2 pools is confirmed by the `RSETHPoolV3ExternalBridge` reinitializer(5) comment ("Added a new supported token (wstETH), along with the oracle and the native bridging logic for it") and by the existence of dedicated bridge adapters `LidoBridge` and `ArbitrumLidoBridge`. [3](#0-2) [4](#0-3) 

---

### Impact Explanation

If the canonical L2 wstETH bridge (Lido's OP/Arbitrum bridge) is exploited and L2 wstETH becomes worthless or significantly devalued:

1. An attacker acquires large amounts of devalued L2 wstETH cheaply.
2. They call `deposit(token, amount, referralId)` on the L2 pool.
3. `viewSwapRsETHAmountAndFee` queries `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which returns the unaffected L1 canonical price.
4. The attacker receives wrsETH at the full L1 wstETH value.
5. All existing rsETH holders are diluted — the protocol has issued wrsETH backed by worthless collateral.

This constitutes **protocol insolvency**: the rsETH backing ratio drops below 1:1 and cannot be recovered without a governance intervention.

**Impact**: Critical — Protocol insolvency / permanent dilution of existing rsETH holders.

---

### Likelihood Explanation

L2 canonical bridges have been exploited historically (Ronin ~$625M, Wormhole ~$320M, Nomad ~$190M). The Lido wstETH bridge on Optimism and Arbitrum is a high-value target. The Chainlink wstETH/ETH feeds on L2 chains use the L1 canonical rate and would not reflect a bridge-level depeg. The daily mint limit provides partial mitigation but does not prevent the attack across multiple days or if the limit is set high.

**Likelihood**: Low-Medium (bridge exploits are rare but precedented and high-impact).

---

### Recommendation

Implement a double oracle setup for L2 wstETH pricing:

1. **Primary**: Chainlink wstETH/ETH feed (current `ChainlinkOracleForRSETHPoolCollateral`).
2. **Secondary**: On-chain Uniswap V3 TWAP for the wstETH/ETH pair on the respective L2.

Before accepting a wstETH deposit, compare the two prices. If the on-chain TWAP deviates more than a threshold (e.g., 2%) below the Chainlink price, revert or halt deposits. This mirrors the recommendation from M-4 and protects against bridge-level depegs without relying solely on the L1 canonical rate.

---

### Proof of Concept

1. Assume L2 wstETH bridge is exploited; L2 wstETH trades at 0.1 ETH while Chainlink still reports 1.15 ETH (L1 canonical rate).
2. Attacker buys 1000 L2 wstETH for ~100 ETH.
3. Attacker calls `deposit(wstETH_L2, 1000e18, "")` on `RSETHPoolV3ExternalBridge`.
4. `viewSwapRsETHAmountAndFee` computes `tokenToETHRate = 1.15e18` (from `ChainlinkOracleForRSETHPoolCollateral`).
5. Attacker receives `1000 * 1.15 / rsETHRate` wrsETH — equivalent to ~1150 ETH of wrsETH for a 100 ETH investment.
6. Attacker redeems wrsETH on L1 for ~1150 ETH worth of real LSTs, draining the protocol. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L331-334)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L232-235)
```text
     * - reinitializer(5): Added a new supported token (wstETH), along with the oracle and the native bridging logic for
     * it.
     * - reinitializer(6): This upgrade enables native bridging of ETH from L2 to L1.
     */
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L390-412)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L433-453)
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

**File:** contracts/bridges/LidoBridge.sol (L15-22)
```text
contract LidoBridge is IL2TokenBridge, ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice Address of the wstETH token
    IERC20 public immutable wstETH;

    /// @notice Address of the Lido canonical bridge contract
    IL2ERC20Bridge public immutable lidoBridge;
```
