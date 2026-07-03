### Title
Immutable `RSETHPriceFeed` Contract with Hardcoded Chainlink Dependency Cannot Be Replaced if Feed Is Deprecated - (File: contracts/oracles/RSETHPriceFeed.sol)

### Summary
`RSETHPriceFeed` is a fully immutable, non-ownable, non-upgradeable contract that hardcodes both a Chainlink ETH/USD aggregator and the rsETH oracle as `immutable` state variables. There is no owner, no admin, and no mechanism to replace either dependency. If Chainlink deprecates the ETH/USD feed (as they have done with other feeds and as they did with VRF 2.0), the contract is permanently bricked with no recovery path.

### Finding Description
`RSETHPriceFeed` implements `AggregatorV3Interface` and is designed to serve as a Chainlink-compatible rsETH/USD price feed for consumption by external protocols (lending markets, DEXes, etc.) that integrate rsETH. The contract stores both its Chainlink dependency and its rsETH oracle as `immutable`:

```solidity
AggregatorV3Interface public immutable ETH_TO_USD;
IRSETHOracle public immutable RS_ETH_ORACLE;
```

These are set once in the constructor and can never be changed:

```solidity
constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
    ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
    RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);
    description = description_;
}
```

The contract has no `owner`, no `Ownable`, no proxy, no `Initializable`, and no setter functions. Every call to `latestRoundData()` and `getRoundData()` delegates directly to `ETH_TO_USD.latestRoundData()` / `ETH_TO_USD.getRoundData()`:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

If Chainlink deprecates or bricks the ETH/USD feed at `ETH_TO_USD`, every call to `latestRoundData()` and `getRoundData()` will revert or return stale/zero data permanently. There is no admin path to point the contract at a replacement feed.

By contrast, `ChainlinkPriceOracle` (the upgradeable oracle used internally by LRT-rsETH) stores price feeds in a mutable mapping and exposes `updatePriceFeedFor()` to replace them. `RSETHPriceFeed` has no equivalent.

### Impact Explanation
**Medium — Temporary freezing of funds.**

`RSETHPriceFeed` is the rsETH/USD price oracle consumed by external lending protocols and DEXes that integrate rsETH as collateral. If the underlying Chainlink ETH/USD feed is deprecated and `latestRoundData()` begins reverting:

- Lending protocols using `RSETHPriceFeed` as their rsETH/USD oracle will have their oracle calls revert, causing the protocol to freeze all rsETH-collateralized positions (no new borrows, no repayments, no withdrawals of rsETH collateral).
- rsETH holders with collateral locked in those protocols cannot withdraw their rsETH until the lending protocol itself is updated — which may require governance processes outside the control of LRT-rsETH or its users.
- The `RSETHPriceFeed` contract itself cannot be fixed; a new contract must be deployed and every downstream protocol must be migrated, which is a coordination problem with no guaranteed resolution timeline.

### Likelihood Explanation
**Medium.** Chainlink has a documented history of deprecating price feeds and entire VRF versions. The external report explicitly cites Chainlink bricking VRF 2.0 for existing subscriptions. Chainlink has also sunset legacy ETH/USD feeds on various networks when migrating to newer aggregator versions. The `RSETHPriceFeed` contract has no protection against this scenario and no recovery mechanism.

### Recommendation
Replace `RSETHPriceFeed` with an upgradeable (UUPS or Transparent proxy) variant, or make `ETH_TO_USD` and `RS_ETH_ORACLE` mutable with an owner-controlled setter, analogous to how `ChainlinkPriceOracle` exposes `updatePriceFeedFor()`. This mirrors the recommended fix in the external report: insulate the immutable-facing interface from the external dependency by introducing a replaceable handler layer.

### Proof of Concept

1. `RSETHPriceFeed` is deployed with a specific Chainlink ETH/USD aggregator address hardcoded as `immutable ETH_TO_USD`. [1](#0-0) 

2. Both `latestRoundData()` and `getRoundData()` unconditionally delegate to `ETH_TO_USD` with no fallback. [2](#0-1) 

3. The contract has no owner, no proxy, and no setter — there is no code path to replace `ETH_TO_USD`. [3](#0-2) 

4. Compare with `ChainlinkPriceOracle`, which stores feeds in a mutable mapping and exposes `updatePriceFeedFor()` — the pattern that `RSETHPriceFeed` should follow. [4](#0-3) 

5. If Chainlink deprecates the ETH/USD feed, every downstream protocol consuming `RSETHPriceFeed` as its rsETH/USD oracle will have oracle calls revert, freezing rsETH collateral positions in those protocols with no recovery path within LRT-rsETH. [5](#0-4)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L26-43)
```text
contract RSETHPriceFeed is AggregatorV3Interface {
    /// @notice Price feed for (ETH / USD) pair
    AggregatorV3Interface public immutable ETH_TO_USD;

    /// @notice rsETH oracle contract
    IRSETHOracle public immutable RS_ETH_ORACLE;

    string public description;

    /// @param ethToUSDAggregatorAddress the address of ETH / USD feed
    /// @param rsETHOracle the address of rsETHOracle contract
    /// @param description_ priceFeed description (RSETH / USD)
    constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
        ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
        RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);

        description = description_;
    }
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L53-70)
```text
    function getRoundData(uint80 _roundId)
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);

        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L30-65)
```text
    mapping(address asset => address priceFeed) public override assetPriceFeed;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /// @dev Initializes the contract
    /// @param lrtConfig_ LRT config address
    function initialize(address lrtConfig_) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfig_);

        lrtConfig = ILRTConfig(lrtConfig_);
        emit UpdatedLRTConfig(lrtConfig_);
    }

    /// @notice Fetches Asset/ETH exchange rate
    /// @param asset the asset for which exchange rate is required
    /// @return assetPrice exchange rate of asset
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }

    /// @dev add/update the price oracle of any supported asset
    /// @dev only LRTManager is allowed
    /// @param asset asset address for which oracle price feed needs to be added/updated
    /// @param priceFeed chainlink price feed contract which contains exchange rate info
    function updatePriceFeedFor(address asset, address priceFeed) external onlyLRTManager onlySupportedAsset(asset) {
        UtilLib.checkNonZeroAddress(priceFeed);
        assetPriceFeed[asset] = priceFeed;
        emit AssetPriceFeedUpdate(asset, priceFeed);
    }
```
