### Title
Unpermissioned `updateRSETHPrice()` Allows Any Caller to Spam Expensive Oracle Computations - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard and no fee requirement. Any unprivileged caller can invoke it an unlimited number of times at zero protocol cost, triggering a chain of expensive external calls on every invocation.

### Finding Description
`updateRSETHPrice()` carries no access control and no fee mechanism:

```solidity
// LRTOracle.sol line 87
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` is computationally heavy. It calls `_getTotalEthInProtocol()`, which iterates over every supported asset and, for each one, calls an external price oracle (`IPriceFetcher.getAssetPrice`) and `ILRTDepositPool.getTotalAssetDeposits`, which in turn iterates over every `NodeDelegator` and makes additional external calls into EigenLayer (`getAssetBalance`, `getAssetUnstaking`, `getEffectivePodShares`). After that, the function may mint rsETH as a protocol fee and emit multiple events. All of this work is triggered on every call with no cost to the caller beyond base gas.

The contrast with the privileged variant is explicit in the codebase:

```solidity
// LRTOracle.sol line 94
function updateRSETHPriceAsManager() external onlyLRTManager {
    _updateRsETHPrice();
}
```

The manager-gated path exists precisely because the operation is sensitive, yet the public path imposes no equivalent barrier.

### Impact Explanation
**Medium – Unbounded gas consumption / block stuffing.**

A malicious actor can submit a flood of `updateRSETHPrice()` transactions in rapid succession. Each transaction forces the EVM to execute multiple cross-contract calls (price oracles × supported assets + EigenLayer queries × NodeDelegators). With several supported assets and multiple NodeDelegators, each call consumes a significant and growing amount of gas. Sustained spam can fill blocks, delay legitimate oracle updates, and degrade overall protocol responsiveness. The cost to the attacker is only the gas price; there is no protocol-level fee to deter the attack.

### Likelihood Explanation
**High.** The function is `public`, requires no token balance, no role, and no ETH value. Any EOA or contract can call it at any time the protocol is unpaused. The attack requires no special knowledge or setup.

### Recommendation
Add a minimum fee requirement (analogous to the `chargeFee` modifier in the referenced report) or restrict `updateRSETHPrice()` to a permissioned role (e.g., `onlyLRTOperator`), keeping `updateRSETHPriceAsManager()` for the manager override path. Alternatively, implement a cooldown period (e.g., minimum blocks between calls) enforced on-chain to rate-limit unpermissioned invocations.

### Proof of Concept
1. Deploy or interact with the live `LRTOracle` proxy.
2. From any EOA (no role, no ETH balance required), call `updateRSETHPrice()` in a tight loop across multiple transactions.
3. Each call executes `_getTotalEthInProtocol()`, which iterates over all supported assets and all NodeDelegators, making external calls to price oracles and EigenLayer on every iteration.
4. Observe that blocks fill with these calls, legitimate oracle updates are delayed, and the attacker pays only standard gas with no protocol-imposed cost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-250)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```
