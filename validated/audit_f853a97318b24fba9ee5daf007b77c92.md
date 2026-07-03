### Title
Unprotected `updateRate()` Allows Anyone to Drain Contract ETH, DOSing Cross-Chain Rate Propagation - (File: contracts/cross-chain/MultiChainRateProvider.sol)

---

### Summary

`MultiChainRateProvider.updateRate()` has no access control and is `payable`. The function pays LayerZero fees by drawing from the contract's own ETH balance (`{ value: estimatedFee }`). If the protocol funds the contract with ETH for automated rate propagation (the intended operational design), any unprivileged caller can drain that ETH by calling `updateRate()` repeatedly with `msg.value = 0`, consuming the contract's balance across all registered receiver chains. Once drained, all future rate updates revert, leaving every L2 pool permanently relying on a stale rsETH/ETH exchange rate.

---

### Finding Description

`MultiChainRateProvider` is the abstract base for `RSETHMultiChainRateProvider`, which propagates the rsETH/ETH price to receiver contracts on multiple L2 chains via LayerZero v1. [1](#0-0) 

The function signature is:

```solidity
function updateRate() external payable nonReentrant {
```

There is no `onlyOwner`, no role check, and no minimum `msg.value` requirement. Inside the loop, for every registered receiver the function calls:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [2](#0-1) 

The `{ value: estimatedFee }` call draws from `address(this).balance` — the contract's own ETH — not exclusively from `msg.value`. There is no assertion that `msg.value >= sum(estimatedFees)`. If the contract holds any ETH (from protocol funding or accumulated surplus from prior calls), an attacker calling with `msg.value = 0` will silently consume that ETH across all receivers.

The concrete deployed contract is `RSETHMultiChainRateProvider`, which inherits this logic unchanged: [3](#0-2) 

---

### Impact Explanation

When the contract's ETH is drained:

1. Every subsequent call to `updateRate()` reverts (insufficient balance for `estimatedFee`).
2. All L2 `RSETHPool` / `RSETHPoolV3` variants that depend on the rate receiver stop receiving fresh rsETH/ETH prices.
3. L2 pools mint `wrsETH` using a stale rate — users depositing ETH receive an incorrect amount of `wrsETH`, either overpaying or underpaying relative to the true rsETH price.
4. The protocol cannot restore rate propagation without re-funding the contract and redeploying or patching it.

**Impact classification**: Medium — temporary (potentially permanent until re-funded) freezing of the rate-update mechanism, with secondary risk of incorrect wrsETH minting on all connected L2 chains.

---

### Likelihood Explanation

The protocol's own comment documents the intended funding model:

```
/// @dev This function is set to payable to pay for gas on execute lzReceive (on the receiver contract)
/// on the destination chain.
``` [4](#0-3) 

This confirms the contract is expected to hold ETH. The attack requires only a public call with `msg.value = 0` — no special privileges, no capital at risk for the attacker, and no complex setup. With multiple registered receivers, each call drains a non-trivial amount of ETH (one LayerZero fee per receiver per call). Likelihood is **high** given zero barrier to entry.

---

### Recommendation

1. **Add access control** to `updateRate()` — at minimum `onlyOwner`, or a dedicated `UPDATER_ROLE` for automation bots:
   ```solidity
   function updateRate() external payable onlyOwner nonReentrant {
   ```
2. **Alternatively, require the caller to cover fees**: assert `msg.value >= estimateTotalFee()` at the top of the function so the contract never subsidises callers from its own balance.
3. **Refund excess `msg.value`** after the loop to prevent ETH accumulation that could be drained in future calls.

---

### Proof of Concept

1. Protocol deploys `RSETHMultiChainRateProvider` and funds it with 1 ETH to cover automated rate updates across 3 L2 chains (each LayerZero send costs ~0.01 ETH → 0.03 ETH per call).
2. Attacker calls `updateRate()` with `msg.value = 0` in a loop (~33 times).
3. Each call draws `estimatedFee * 3` from the contract's balance; the attacker pays only L1 gas.
4. After ~33 calls the contract balance reaches 0.
5. The next legitimate `updateRate()` call (from the protocol's automation bot) reverts with an out-of-funds error on the first `ILayerZeroEndpoint.send`.
6. All registered L2 rate receivers stop updating; L2 pools mint `wrsETH` at a stale rsETH/ETH rate indefinitely.

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L104-137)
```text
    /// @notice Updates rate in this contract and on the receivers
    /// @dev This function is set to payable to pay for gas on execute lzReceive (on the receiver contract)
    /// on the destination chain. To compute the correct value to send check here -
    /// https://layerzero.gitbook.io/docs/evm-guides/code-examples/estimating-message-fees
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

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L9-28)
```text
contract RSETHMultiChainRateProvider is MultiChainRateProvider {
    address public immutable rsETHPriceOracle;

    constructor(address _rsETHPriceOracle, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;

        rateInfo = RateInfo({
            tokenSymbol: "rsETH",
            tokenAddress: 0xA1290d69c65A6Fe4DF752f95823fae25cB99e5A7, // rsETH token address on ETH mainnet
            baseTokenSymbol: "ETH",
            baseTokenAddress: address(0) // Address 0 for native tokens
        });

        layerZeroEndpoint = _layerZeroEndpoint;
    }

    /// @notice Returns the latest rate from the rsETH contract
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```
