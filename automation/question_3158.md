# Q3158: getETHDistributionData Reentrant Token Callback eth Accounting daily P3158

## Question
Can an unprivileged ETH depositor or ETH sender enter through `depositETH and updateRSETHPrice indirectly read getETHDistributionData()` while controlling direct ETH sends, deposit size, and price-update ordering and use an ERC777-like or malicious token callback during safeTransferFrom/safeTransfer to reenter another public protocol path, causing `contracts/LRTDepositPool.sol::getETHDistributionData` to break the invariant that nonReentrant and state ordering prevent double mint, double claim, or liquidity theft; specifically, eth accounting must not violate backing, queue, yield, or liquidity accounting for getETHDistributionData, leading to permanent freezing of funds? Probe condition: daily fee mint limit route; amount case available liquidity plus 1 wei; timing one second after daily reset; caller model EOA caller.

## Target
- File/function: contracts/LRTDepositPool.sol::getETHDistributionData
- Entrypoint: depositETH and updateRSETHPrice indirectly read getETHDistributionData()
- Attacker controls: direct ETH sends, deposit size, and price-update ordering; scenario: use an ERC777-like or malicious token callback during safeTransferFrom/safeTransfer to reenter another public protocol path; validation style: two transactions before and after updateRSETHPrice; probe condition: daily fee mint limit route; amount case available liquidity plus 1 wei; timing one second after daily reset; caller model EOA caller
- Exploit idea: Use two-step sequence to exercise the reentrant token callback path against getETHDistributionData and look for eth accounting breaking value conservation or liveness.
- Invariant to test: nonReentrant and state ordering prevent double mint, double claim, or liquidity theft; specifically, eth accounting must not violate backing, queue, yield, or liquidity accounting for getETHDistributionData
- Expected Immunefi impact: Critical. Permanent freezing of funds
- Fast validation: track raw ETH balances across pool, NDC, converter, vault, and oracle TVL reads Use probe condition: daily fee mint limit route; amount case available liquidity plus 1 wei; timing one second after daily reset; caller model EOA caller.
