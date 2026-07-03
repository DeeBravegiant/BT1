Audit Report

## Title
Missing L2 Sequencer Uptime Check in Chainlink Oracle Enables Stale-Price Exploitation After Sequencer Downtime - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

## Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` but performs no check against the Chainlink L2 Sequencer Uptime Feed. When an L2 sequencer restarts after downtime, the feed returns the last pre-downtime price — passing all three existing guards — allowing an unprivileged attacker to deposit collateral at a stale inflated price and receive more `wrsETH` than the deposited collateral is worth, causing direct loss to the protocol's backing pool.

## Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` applies three guards after calling `latestRoundData()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

None of these detect L2 sequencer downtime. When the sequencer restarts, the last pre-downtime round satisfies all three: `answeredInRound == roundID` (the round was answered before downtime), `timestamp != 0`, and `ethPrice > 0`. The stale price passes unchallenged. [1](#0-0) 

This oracle is consumed by `RSETHPoolV3.viewSwapRsETHAmountAndFee(amount, token)`, which calls `IOracle(supportedTokenOracle[token]).getRate()` to price the deposited token in ETH terms, then divides by the rsETH/ETH rate to compute how many `wrsETH` to mint: [2](#0-1) 

The public `deposit(address token, uint256 amount, string memory referralId)` function is callable by any unprivileged user with no access restriction beyond `whenNotPaused` and `onlySupportedToken`: [3](#0-2) 

`RSETHPoolV3WithNativeChainBridge` is explicitly an L2 contract — it holds `l1VaultETHForL2Chain`, uses `IL2TokenBridge`, and bridges assets back to L1 — and contains an identical `deposit(address token, ...)` path with the same oracle dependency: [4](#0-3) [5](#0-4) 

Chainlink's own documentation explicitly requires L2 deployments to check the Sequencer Uptime Feed before consuming price data. The contract omits this check entirely.

## Impact Explanation

**Critical — Direct theft of user/protocol funds.**

An attacker deposits collateral tokens immediately after sequencer restart at the stale inflated pre-downtime price. They receive `wrsETH` minted at the old (higher) collateral valuation. Since `wrsETH` is redeemable for ETH at the true rsETH/ETH rate, the attacker extracts more ETH value than they deposited. The loss is borne by all existing rsETH holders through dilution of the backing pool. The daily mint limit caps per-day exposure but does not prevent the attack; it resets each day and the vulnerability persists until patched.

## Likelihood Explanation

L2 sequencer outages are documented, recurring events on Arbitrum, Optimism, and other OP-stack chains. The attack window opens the moment the sequencer resumes and closes when Chainlink's L2 feed updates (typically minutes to hours). The attacker requires only a standard EOA and knowledge of the sequencer restart — no privileged access, no governance capture. The attack is trivially automatable by monitoring the Chainlink Sequencer Uptime Feed and submitting a deposit transaction in the first block after restart.

## Recommendation

Add a Chainlink L2 Sequencer Uptime Feed check inside `ChainlinkOracleForRSETHPoolCollateral.getRate()`, reverting if the sequencer is down or has been back online for less than a grace period (e.g., 1 hour):

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 1 hours;

function getRate() public view returns (uint256) {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (answer != 0) revert SequencerDown();
    if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();

    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();
    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    // ...
}
```

Additionally, add a maximum staleness check (`block.timestamp - timestamp > MAX_STALENESS`) to catch feeds stale for reasons other than sequencer downtime.

## Proof of Concept

1. Protocol deploys `RSETHPoolV3WithNativeChainBridge` on Arbitrum with `wstETH` as a supported token, oracle set to `ChainlinkOracleForRSETHPoolCollateral` pointing at the `wstETH/ETH` Chainlink feed.
2. `wstETH/ETH` Chainlink feed on Arbitrum last updated at `P_old = 1.15 ETH` before sequencer downtime.
3. During sequencer downtime, `wstETH` market price drops to `P_new = 1.05 ETH`.
4. Sequencer restarts. Chainlink L2 feed still returns `P_old = 1.15 ETH`. `answeredInRound == roundID`, `timestamp != 0`, `ethPrice > 0` — all three guards in `getRate()` pass.
5. Attacker calls `deposit(wstETH, 1000e18, "")`:
   - `getRate()` returns `1.15e18` (stale)
   - `rsETHAmount = 1000e18 * 1.15e18 / rsETHPrice` — attacker receives wrsETH backed by 1150 ETH
   - Attacker deposited only 1000 wstETH worth 1050 ETH at true market price
6. Attacker redeems/bridges `wrsETH` for ~1150 ETH equivalent, netting ~100 ETH profit at the expense of the protocol's backing pool.

**Foundry fork test plan:** Fork Arbitrum mainnet at a block just after a historical sequencer restart. Deploy `ChainlinkOracleForRSETHPoolCollateral` pointing at the `wstETH/ETH` feed. Call `getRate()` and confirm it returns the pre-restart stale price without reverting. Confirm all three guards pass. Then simulate a `deposit` call and verify the attacker receives more `wrsETH` than the true market value of the deposited collateral.

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

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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

**File:** contracts/pools/RSETHPoolV3.sol (L330-334)
```text
        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L74-78)
```text
    /// @notice The corresponding L1Vault contract for the L2 chain
    address public l1VaultETHForL2Chain;

    /// @notice The mapping of token addresses to their respective token bridges
    mapping(address token => address bridge) public tokenBridge;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L307-329)
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
