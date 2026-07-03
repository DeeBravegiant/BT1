### Title
Missing Zero Address Checks for Immutable Oracle Variables in Constructor - (File: contracts/oracles/RSETHPriceFeed.sol)

---

### Summary
`RSETHPriceFeed.sol` sets two immutable address variables — `ETH_TO_USD` and `RS_ETH_ORACLE` — in its constructor without any zero address validation. Because both are `immutable`, a deployment with either set to `address(0)` permanently bricks the price feed with no recovery path. An identical pattern exists in `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` (`oracle`), `contracts/cross-chain/RSETHMultiChainRateProvider.sol` (`rsETHPriceOracle`), `contracts/cross-chain/RSETHRateProvider.sol` (`rsETHPriceOracle`), and `contracts/agETH/AGETHMultiChainRateProvider.sol` (`agETHPriceOracle`).

---

### Finding Description

In `RSETHPriceFeed.sol`, the constructor accepts `ethToUSDAggregatorAddress` and `rsETHOracle` and assigns them directly to the `immutable` state variables `ETH_TO_USD` and `RS_ETH_ORACLE` with no zero address guard:

```solidity
constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
    ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
    RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);
    description = description_;
}
``` [1](#0-0) 

Every public function in the contract calls through one or both of these variables:

- `decimals()` and `version()` call `ETH_TO_USD.decimals()` / `ETH_TO_USD.version()` [2](#0-1) 
- `latestRoundData()` calls both `ETH_TO_USD.latestRoundData()` and `RS_ETH_ORACLE.rsETHPrice()` [3](#0-2) 
- `getRoundData()` calls both as well [4](#0-3) 

Because both variables are `immutable`, there is no setter and no upgrade path. A zero address deployment is irreversible.

The same pattern is present in `ChainlinkOracleForRSETHPoolCollateral.sol`:

```solidity
constructor(address _oracle) {
    oracle = _oracle;   // no zero address check; oracle is immutable
}
``` [5](#0-4) 

And in the rate provider contracts: [6](#0-5) [7](#0-6) [8](#0-7) 

The project's own `UtilLib.checkNonZeroAddress` utility exists precisely for this purpose and is used consistently in every other constructor in the codebase (e.g., `ArbitrumLidoBridge`, `SonicChainNativeTokenBridge`, `LidoBridge`, `TACWETHBridge`), but is absent here. [9](#0-8) 

---

### Impact Explanation

If `RSETHPriceFeed` is deployed with `address(0)` for either `ETH_TO_USD` or `RS_ETH_ORACLE`, every call to `latestRoundData()`, `getRoundData()`, `decimals()`, or `version()` reverts permanently. The contract can never be repaired — it must be redeployed. Any downstream protocol (lending market, pool, keeper) that has already registered this address as its Chainlink-compatible price feed will be unable to read the rsETH/USD price, causing those integrations to fail until they are reconfigured to point at a new deployment.

**Impact**: Low — Contract fails to deliver its promised returns (oracle price data), but no user funds held in `RSETHPriceFeed` itself are lost or frozen.

---

### Likelihood Explanation

This is a deployment-time misconfiguration risk. It requires the deployer to pass `address(0)` for one of the constructor arguments. While unlikely in a careful deployment, the absence of a guard means there is no on-chain safety net. The original audit finding in the analogous protocol was rated Medium precisely because the variables are immutable and the mistake is unrecoverable. The same reasoning applies here.

---

### Recommendation

Add `UtilLib.checkNonZeroAddress` guards for every address parameter that is assigned to an `immutable` variable in a constructor, mirroring the pattern already used throughout the rest of the codebase:

```solidity
// RSETHPriceFeed.sol
constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
    UtilLib.checkNonZeroAddress(ethToUSDAggregatorAddress);
    UtilLib.checkNonZeroAddress(rsETHOracle);
    ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
    RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);
    description = description_;
}
```

Apply the same fix to:
- `ChainlinkOracleForRSETHPoolCollateral.sol` — `_oracle`
- `RSETHMultiChainRateProvider.sol` — `_rsETHPriceOracle`, `_layerZeroEndpoint`
- `RSETHRateProvider.sol` — `_rsETHPriceOracle`, `_layerZeroEndpoint`
- `AGETHMultiChainRateProvider.sol` — `_agETHPriceOracle`, `_layerZeroEndpoint`

---

### Proof of Concept

1. Deploy `RSETHPriceFeed` with `ethToUSDAggregatorAddress = address(0)` and any valid `rsETHOracle`.
2. Call `latestRoundData()`.
3. The EVM attempts a call to `address(0)`, which returns empty data; the ABI decode of the return value reverts (or the call silently returns zero bytes, causing a decode revert).
4. The contract is now permanently non-functional. No admin function exists to update `ETH_TO_USD` because it is `immutable`.
5. Any protocol that registered this address as its rsETH/USD Chainlink feed will be unable to obtain a price, breaking all price-dependent operations. [10](#0-9) [1](#0-0) [11](#0-10)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L28-31)
```text
    AggregatorV3Interface public immutable ETH_TO_USD;

    /// @notice rsETH oracle contract
    IRSETHOracle public immutable RS_ETH_ORACLE;
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L38-43)
```text
    constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
        ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
        RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);

        description = description_;
    }
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L45-50)
```text
    function decimals() external view returns (uint8) {
        return ETH_TO_USD.decimals();
    }

    function version() external view returns (uint256) {
        return ETH_TO_USD.version();
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L53-61)
```text
    function getRoundData(uint80 _roundId)
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);

        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L16-24)
```text
    address public immutable oracle;

    error StalePrice();
    error IncompleteRound();
    error InvalidPrice();

    constructor(address _oracle) {
        oracle = _oracle;
    }
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L12-13)
```text
    constructor(address _rsETHPriceOracle, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;
```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L13-14)
```text
    constructor(address _rsETHPriceOracle, uint16 _dstChainId, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;
```

**File:** contracts/agETH/AGETHMultiChainRateProvider.sol (L15-16)
```text
    constructor(address _agETHPriceOracle, address _layerZeroEndpoint) {
        agETHPriceOracle = _agETHPriceOracle;
```

**File:** contracts/utils/UtilLib.sol (L10-13)
```text
    /// @param address_ address to check
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
