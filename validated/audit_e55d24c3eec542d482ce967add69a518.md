### Title
Missing Zero-Address Checks in Constructor Allow Permanent Price Feed Failure - (File: contracts/oracles/RSETHPriceFeed.sol)

### Summary
The `RSETHPriceFeed` constructor assigns two critical immutable address variables — `ETH_TO_USD` and `RS_ETH_ORACLE` — without any zero-address validation. Because both are declared `immutable`, a deployment with either set to `address(0)` permanently bricks every function in the contract with no recovery path.

### Finding Description
In `contracts/oracles/RSETHPriceFeed.sol`, the constructor at lines 38–43 accepts `ethToUSDAggregatorAddress` and `rsETHOracle` and assigns them directly to the immutable state variables `ETH_TO_USD` and `RS_ETH_ORACLE`:

```solidity
constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
    ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
    RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);
    description = description_;
}
```

Neither parameter is validated against `address(0)`. Compare this with every other address-accepting constructor in the codebase — `LidoBridge`, `ArbitrumLidoBridge`, `SonicChainNativeTokenBridge`, `HashStorage`, `InterimRSETHOracle`, etc. — all of which call `UtilLib.checkNonZeroAddress()` before assigning.

The same pattern is present in `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` (line 22–24), where `_oracle` is assigned to the immutable `oracle` field with no zero-address check.

### Impact Explanation
If `ETH_TO_USD` or `RS_ETH_ORACLE` is `address(0)` at deployment:

- `decimals()` (line 46) reverts — calls `ETH_TO_USD.decimals()` on `address(0)`.
- `version()` (line 50) reverts.
- `latestRoundData()` (lines 63–70) reverts — calls both `ETH_TO_USD.latestRoundData()` and `RS_ETH_ORACLE.rsETHPrice()`.
- `getRoundData()` (lines 53–61) reverts.

Because both variables are `immutable`, there is no setter, no upgrade path, and no recovery. The deployed contract is permanently non-functional. Any external integrator or on-chain component that registered this address as its rsETH/USD price feed will receive permanent reverts on every price query.

**Impact: Low — Contract fails to deliver promised returns, but does not lose value.**

### Likelihood Explanation
The root cause is a missing input guard at construction time. The deployer must supply a zero address for the bug to manifest, which is an operational mistake rather than an adversarial action. However, the codebase's own convention (`UtilLib.checkNonZeroAddress`) exists precisely to catch such mistakes, and its absence here is an inconsistency that makes the mistake possible with no on-chain safety net. Likelihood is low but non-zero, and the consequence is irreversible.

### Recommendation
Apply `UtilLib.checkNonZeroAddress` to both address parameters before assignment, consistent with every other constructor in the codebase:

```solidity
constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
    UtilLib.checkNonZeroAddress(ethToUSDAggregatorAddress);
    UtilLib.checkNonZeroAddress(rsETHOracle);
    ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
    RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);
    description = description_;
}
```

Apply the same fix to `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
constructor(address _oracle) {
    UtilLib.checkNonZeroAddress(_oracle);
    oracle = _oracle;
}
```

### Proof of Concept

1. Deploy `RSETHPriceFeed` with `ethToUSDAggregatorAddress = address(0)` and any valid `rsETHOracle`.
2. Call `latestRoundData()`.
3. The call reverts because `ETH_TO_USD` is `address(0)` and the EVM cannot dispatch to it.
4. The contract is permanently broken — `ETH_TO_USD` is `immutable` and cannot be updated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L38-43)
```text
    constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
        ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
        RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);

        description = description_;
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L22-24)
```text
    constructor(address _oracle) {
        oracle = _oracle;
    }
```

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
