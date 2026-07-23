I was unable to read the actual source files in this repository due to tool call limitations. I explored the directory structure and identified the relevant files:

- `metric-core/contracts/MetricOmmPool.sol`
- `metric-core/contracts/libraries/LiquidityLib.sol`
- `metric-core/contracts/libraries/SwapMath.sol`
- `metric-core/contracts/libraries/PoolActions.sol`
- `metric-core/contracts/libraries/PoolStateLibrary.sol`
- `metric-core/contracts/libraries/Slot0Library.sol`

However, I was not able to read their contents before hitting the tool call limit. Without reading the actual code, I cannot:

1. Confirm whether `removeLiquidity` or `swap` paths have a surrogate/external-contract analog where tokens are held externally but accounting is updated as if they were already in the pool
2. Verify whether `_executeSwap` or callback settlement correctly transfers all owed amounts (both user portion and fee/protocol portion) when tokens are stored in an extension or external contract
3. Check whether `rewardTokenBalance`-equivalent state variables (e.g., protocol fee accumulators) are incremented before the actual transfer occurs from an external holding contract

**To get a reliable answer, please start a Devin session** so the full file contents of `MetricOmmPool.sol`, `LiquidityLib.sol`, `PoolActions.sol`, and `SwapMath.sol` can be read and traced end-to-end against the invariant from the external report (tokens held externally, only partial transfer executed, but full accounting credited).