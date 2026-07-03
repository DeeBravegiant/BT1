### Title
Incomplete Chainlink Staleness Check Allows Stale Collateral Prices in L2 Pools - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

### Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates Chainlink round data using only `answeredInRound < roundID`, omitting the time-based heartbeat check. This is directly analogous to the external report's `isFailed()` only inspecting the LSB of a packed `ValidationData` — both are incomplete validations of a composite value. A price that has not been updated for hours passes unchallenged, allowing L2 pool depositors to receive incorrect rsETH/wrsETH amounts.

### Finding Description
`getRate()` performs three checks on Chainlink round