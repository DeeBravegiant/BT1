### Title
Missing Zero Address Validation in Constructor for Immutable Oracle Addresses - (File: contracts/oracles/RSETHPriceFeed.sol)

### Summary
`RSETHPriceFeed.sol` sets two immutable oracle addresses — `ETH_TO_USD` and `RS_ETH_ORACLE` — in its constructor without any zero address validation. Because both variables are declared `immutable`, there is no mechanism to correct them after deployment. If either is set to `address(0)`, every price-reading function permanently reverts, rendering the price feed non-functional.

### Finding Description
The constructor of `RSETHPriceFeed` accepts `ethToUSDAggregatorAddress` and `rsETHOracle` and assigns them directly to `immutable` state variables without calling `UtilLib.checkNonZeroAddress` or any equivalent guard:

```solidity
// contracts/oracles/RSETHPriceFeed.sol L38-43
constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
    ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress); // no zero check
    RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);                     // no zero check
    description = description_;
}
```

Every public function that constitutes the Chainlink `AggregatorV3Interface` — `decimals()`, `version()`, `getRoundData()`, and `latestRoundData()` — delegates to one or both of these addresses:

```solidity
// L45-46
function decimals() external view returns (uint8) {
    return ETH_TO_USD.decimals(); // reverts if ETH_TO_USD == address(0)
}
// L58-60
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18; // reverts if RS_ETH_ORACLE == address(0)
```

Because both variables are `immutable`, no setter exists and no upgrade path is available. The contract must be redeployed from scratch.

Contrast this with every other address-accepting initializer in the codebase, which consistently calls `UtilLib.checkNonZeroAddress`:

```solidity
// contracts/utils/UtilLib.sol L11-13
function checkNonZeroAddress(address address_) internal pure {
    if (address_ == address(0)) revert ZeroAddressNotAllowed();
}
```

The same omission exists in `RSETHRateProvider` and `RSETHMultiChainRateProvider`, which also store `rsETHPriceOracle` and `layerZeroEndpoint` as `immutable` without zero-address guards.

### Impact Explanation
If `RSETHPriceFeed` is deployed with either oracle address as `address(0)`, all calls to `decimals()`, `version()`, `getRoundData()`, and `latestRoundData()` revert unconditionally. The contract permanently fails to deliver its promised price feed. Any external protocol (e.g., a lending market on Morph) that registered this feed as its rsETH/USD price source would be unable to read prices, blocking collateral valuation and potentially freezing user positions. The README confirms a live deployment: `RSETHPriceFeed (Morph) | 0x4B9C66c2C0d3706AabC6d00D2a6ffD2B68A4E383`.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation
Deployment mistakes (copy-paste errors, misconfigured scripts, wrong argument ordering) are a realistic operational risk, especially for a contract with three constructor arguments of mixed types. The absence of a guard means the error is not caught at deployment time and cannot be corrected afterward. Likelihood is low but non-zero given the operational complexity of multi-chain deployments.

### Recommendation
Add zero address validation for both oracle parameters in the constructor, consistent with the pattern used throughout the rest of the codebase:

```solidity
constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
    UtilLib.checkNonZeroAddress(ethToUSDAggregatorAddress);
    UtilLib.checkNonZeroAddress(rsETHOracle);
    ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
    RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);
    description = description_;
}
```

Apply the same fix to `RSETHRateProvider` and `RSETHMultiChainRateProvider` constructors for `_rsETHPriceOracle` and `_layerZeroEndpoint`.

### Proof of Concept

1. Deploy `RSETHPriceFeed` with `ethToUSDAggregatorAddress = address(0)` and any valid `rsETHOracle`.
2. Call `decimals()`. The call reverts because `ETH_TO_USD` is `address(0)` and EVM reverts on calls to the zero address.
3. Call `latestRoundData()`. Same revert.
4. Observe that `ETH_TO_USD` is `immutable` — no admin function exists to update it.
5. The contract must be redeployed; any protocol that already registered the broken address as its price feed must also be reconfigured. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/oracles/RSETHPriceFeed.sol (L45-70)
```text
    function decimals() external view returns (uint8) {
        return ETH_TO_USD.decimals();
    }

    function version() external view returns (uint256) {
        return ETH_TO_USD.version();
    }

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

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L13-14)
```text
    constructor(address _rsETHPriceOracle, uint16 _dstChainId, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L12-13)
```text
    constructor(address _rsETHPriceOracle, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;
```
