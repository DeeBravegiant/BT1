### Title
Unbounded `rateReceivers` Array in `MultiChainRateProvider.updateRate()` Causes Permanent DoS on Cross-Chain Rate Propagation - (File: contracts/cross-chain/MultiChainRateProvider.sol)

### Summary
`MultiChainRateProvider.updateRate()` iterates over an unbounded `rateReceivers` storage array, making two expensive LayerZero external calls per receiver. The function has no access control and no cap on the number of receivers. As the protocol expands to additional L2 chains, the array grows without bound, eventually causing `updateRate()` to run out of gas and permanently break rsETH rate propagation to all L2 pools.

### Finding Description
`updateRate()` is declared `external payable nonReentrant` with no role restriction — any caller may invoke it. [1](#0-0) 

Inside the function, the loop iterates over every element of `rateReceivers` and, for each entry, calls `ILayerZeroEndpoint.estimateFees()` and `ILayerZeroEndpoint.send()` — both of which are gas-intensive cross-chain operations: [2](#0-1) 

The `rateReceivers` array is grown by the owner via `addRateReceiver()` with no upper bound enforced: [3](#0-2) 

There is no maximum-receiver cap, no pagination, and no mechanism to update a subset of receivers. The array is a plain storage array that can grow indefinitely: [4](#0-3) 

### Impact Explanation
When `rateReceivers.length` grows large enough that the cumulative gas cost of all `estimateFees` + `send` calls exceeds the block gas limit, every call to `updateRate()` will revert. L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, etc.) that rely on the propagated rate will receive permanently stale rsETH exchange rates. Users depositing or withdrawing on those L2 pools will receive incorrect rsETH amounts, constituting a permanent freezing of the rate-update mechanism and potential mispricing of user funds across all supported L2 chains.

**Impact: Medium — Unbounded gas consumption / permanent freezing of cross-chain rate updates.**

### Likelihood Explanation
The Kelp DAO protocol already operates on multiple L2 networks (Arbitrum, Optimism, Unichain, and others referenced in the wiki). Each new L2 deployment adds at least one entry to `rateReceivers`. The owner adds receivers through normal protocol expansion — not through any malicious action. Because `updateRate()` is publicly callable, any user can trigger the out-of-gas condition once the array is large enough. The growth path is realistic and does not require any compromise of privileged keys.

### Recommendation
1. **Cap the receiver count**: enforce a `MAX_RATE_RECEIVERS` constant and revert in `addRateReceiver()` if the limit is exceeded.
2. **Paginated updates**: add an overload `updateRate(uint256 startIndex, uint256 endIndex)` so callers can update a subset of receivers per transaction.
3. **Pull-based model**: have each L2 receiver independently request the latest rate rather than pushing to all chains in one transaction.

### Proof of Concept
1. Owner calls `addRateReceiver()` repeatedly as the protocol expands to N L2 chains.
2. Any external caller invokes `updateRate{value: totalFee}()`.
3. The loop executes `estimateFees` + `send` for each of the N receivers.
4. Once N is large enough (each LayerZero `send` costs ~80 000–150 000 gas), the cumulative gas exceeds the block limit.
5. Every subsequent call to `updateRate()` reverts with out-of-gas.
6. All L2 `RSETHPool` contracts receive no further rate updates; the rsETH exchange rate on every L2 is permanently frozen at the last successfully propagated value.

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L27-27)
```text
    RateReceiver[] public rateReceivers;
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L72-76)
```text
    function addRateReceiver(uint16 _chainId, address _contract) external onlyOwner {
        rateReceivers.push(RateReceiver({ _chainId: _chainId, _contract: _contract }));

        emit RateReceiverAdded(_chainId, _contract);
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-137)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        uint256 rateReceiversLength = rateReceivers.length;

        for (uint256 i; i < rateReceiversLength;) {
            uint16 dstChainId = uint16(rateReceivers[i]._chainId);

            bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );

            unchecked {
                ++i;
            }
        }

        emit RateUpdated(rate);
    }
```
