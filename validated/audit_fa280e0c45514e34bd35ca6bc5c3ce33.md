### Title
Single-Step Ownership Transfer Permanently Locks Critical Configuration in Cross-Chain Rate Contracts - (File: `contracts/cross-chain/CrossChainRateProvider.sol`, `contracts/cross-chain/CrossChainRateReceiver.sol`, `contracts/cross-chain/MultiChainRateProvider.sol`)

---

### Summary

`RSETHRateProvider`, `RSETHRateReceiver`, and `RSETHMultiChainRateProvider` are non-upgradeable contracts that inherit from `CrossChainRateProvider`, `CrossChainRateReceiver`, and `MultiChainRateProvider` respectively. All three abstract bases inherit OpenZeppelin's single-step `Ownable`, meaning `transferOwnership` immediately and irrevocably replaces the owner in one transaction. If an incorrect address is supplied, all `onlyOwner`-gated configuration functions are permanently locked with no recovery path.

---

### Finding Description

`CrossChainRateProvider`, `CrossChainRateReceiver`, and `MultiChainRateProvider` each import and inherit from `@openzeppelin/contracts/access/Ownable.sol`: [1](#0-0) [2](#0-1) [3](#0-2) 

The OZ `Ownable.transferOwnership` immediately writes the new address as owner in a single transaction: [4](#0-3) 

There is no `pendingOwner` / `acceptOwnership` two-step mechanism. The concrete production contracts `RSETHRateProvider`, `RSETHRateReceiver`, and `RSETHMultiChainRateProvider` are all non-upgradeable: [5](#0-4) [6](#0-5) [7](#0-6) 

The following `onlyOwner` functions become permanently inaccessible if ownership is transferred to a wrong address:

- `CrossChainRateProvider`: `updateLayerZeroEndpoint`, `updateRateReceiver`, `updateDstChainId` [8](#0-7) 

- `CrossChainRateReceiver`: `updateLayerZeroEndpoint`, `updateRateProvider`, `updateSrcChainId` [9](#0-8) 

- `MultiChainRateProvider`: `updateLayerZeroEndpoint`, `addRateReceiver`, `removeRateReceiver` [10](#0-9) 

Because these contracts are not upgradeable (no proxy, no `UUPSUpgradeable`, no `_authorizeUpgrade`), there is no administrative recovery path once ownership is lost.

---

### Impact Explanation

If ownership is accidentally transferred to an uncontrolled address:

1. `updateLayerZeroEndpoint` is locked — if the LayerZero endpoint address ever changes (as has occurred historically with LayerZero upgrades), the contracts can no longer send or receive cross-chain messages.
2. `updateRateReceiver` / `updateRateProvider` / `addRateReceiver` / `removeRateReceiver` are locked — the rsETH rate oracle cannot be re-pointed to new or corrected receiver/provider contracts.
3. `updateDstChainId` / `updateSrcChainId` are locked — chain routing cannot be corrected.

The result is that the cross-chain rsETH rate oracle infrastructure is permanently broken: the rsETH/ETH exchange rate can no longer be propagated to destination chains, and the receiver contracts on L2 chains can no longer be reconfigured. All downstream DeFi integrations on L2 that consume the rsETH rate via `RSETHRateReceiver.getRate()` will receive a permanently stale value.

**Impact: Low. Contract fails to deliver promised returns, but doesn't lose value.** The rate oracle ceases to function; user funds in the LRT-rsETH core protocol are not directly at risk, but the cross-chain rate feed — a core protocol promise — is permanently disabled with no on-chain recovery.

---

### Likelihood Explanation

Ownership transfers are infrequent but operationally necessary events (e.g., multisig rotation, protocol governance migration). A single typo or clipboard error in the target address during such a transfer is sufficient to trigger the issue. Because the contracts are non-upgradeable, the error is unrecoverable. Likelihood is **Low** but the consequence is permanent.

---

### Recommendation

Replace the single-step `Ownable` with OpenZeppelin's `Ownable2Step` (already present in the repository at `lib/openzeppelin-contracts/contracts/access/Ownable2Step.sol`) in all three abstract base contracts:

```solidity
// Before
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
contract CrossChainRateProvider is Ownable, ReentrancyGuard { ... }

// After
import { Ownable2Step } from "@openzeppelin/contracts/access/Ownable2Step.sol";
contract CrossChainRateProvider is Ownable2Step, ReentrancyGuard { ... }
```

`Ownable2Step` requires the nominated address to call `acceptOwnership()` before the transfer completes, making an accidental transfer to a wrong address recoverable by simply nominating the correct address again.

---

### Proof of Concept

1. Deploy `RSETHRateProvider` (or `RSETHRateReceiver` / `RSETHMultiChainRateProvider`).
2. Owner calls `transferOwnership(0xDeAdBeEf...)` with a mistyped address.
3. OZ `Ownable._transferOwnership` immediately sets `_owner = 0xDeAdBeEf...`. [11](#0-10) 
4. Any subsequent call to `updateLayerZeroEndpoint`, `updateRateReceiver`, `updateDstChainId`, `addRateReceiver`, or `removeRateReceiver` reverts with `"Ownable: caller is not the owner"`.
5. Because the contract is non-upgradeable, there is no mechanism to recover ownership or call these functions again. The cross-chain rate oracle is permanently misconfigured.

### Citations

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L4-12)
```text
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { ReentrancyGuard } from "@openzeppelin/contracts/security/ReentrancyGuard.sol";

import { ILayerZeroEndpoint } from "contracts/external/layerzero/interfaces/ILayerZeroEndpoint.sol";

/// @title Cross chain rate provider. By witherblock reference: https://github.com/witherblock/gyarados
/// @notice Provides a rate to a receiver contract on a different chain than the one this contract is deployed on
/// @dev Powered using LayerZero
abstract contract CrossChainRateProvider is Ownable, ReentrancyGuard {
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L57-79)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }

    /// @notice Updates the RateReceiver address
    /// @dev Can only be called by owner
    /// @param _rateReceiver the new rate receiver address
    function updateRateReceiver(address _rateReceiver) external onlyOwner {
        rateReceiver = _rateReceiver;

        emit RateReceiverUpdated(_rateReceiver);
    }

    /// @notice Updates the destination chainId
    /// @dev Can only be called by owner
    /// @param _dstChainId the destination chainId
    function updateDstChainId(uint16 _dstChainId) external onlyOwner {
        dstChainId = _dstChainId;

        emit DstChainIdUpdated(_dstChainId);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L4-11)
```text
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";

import { ILayerZeroReceiver } from "contracts/external/layerzero/interfaces/ILayerZeroReceiver.sol";

/// @title Cross chain rate receiver. By witherblock reference: https://github.com/witherblock/gyarados
/// @notice Receives a rate from a provider contract on a different chain than the one this contract is deployed on
/// @dev Powered using LayerZero
abstract contract CrossChainRateReceiver is ILayerZeroReceiver, Ownable {
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L54-76)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }

    /// @notice Updates the RateProvider address
    /// @dev Can only be called by owner
    /// @param _rateProvider the new rate provider address
    function updateRateProvider(address _rateProvider) external onlyOwner {
        rateProvider = _rateProvider;

        emit RateProviderUpdated(_rateProvider);
    }

    /// @notice Updates the source chainId
    /// @dev Can only be called by owner
    /// @param _srcChainId the source chainId
    function updateSrcChainId(uint16 _srcChainId) external onlyOwner {
        srcChainId = _srcChainId;

        emit SrcChainIdUpdated(_srcChainId);
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L4-13)
```text
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { ReentrancyGuard } from "@openzeppelin/contracts/security/ReentrancyGuard.sol";

import { ILayerZeroEndpoint } from "contracts/external/layerzero/interfaces/ILayerZeroEndpoint.sol";

/// @title Multi chain rate provider. By witherblock reference: https://github.com/witherblock/gyarados
/// @notice Provides a rate to a multiple receiver contracts on a different chain than the one this contract is deployed
/// on
/// @dev Powered using LayerZero, all chainId(s) references are for LayerZero chainIds and not blockchain chainIds
abstract contract MultiChainRateProvider is Ownable, ReentrancyGuard {
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L62-102)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }

    /// @notice Adds a rate receiver
    /// @dev Can only be called by owner
    /// @param _chainId rate receiver chainId
    /// @param _contract rate receiver address
    function addRateReceiver(uint16 _chainId, address _contract) external onlyOwner {
        rateReceivers.push(RateReceiver({ _chainId: _chainId, _contract: _contract }));

        emit RateReceiverAdded(_chainId, _contract);
    }

    /// @notice Removes a rate receiver
    /// @dev Can only be called by owner
    /// @param _index the index of the rate receiver
    function removeRateReceiver(uint256 _index) external onlyOwner {
        // Store the rate receiver in a memory var
        RateReceiver memory _rateReceiverToBeRemoved = rateReceivers[_index];

        // Get the current length of all the rate receivers
        uint256 rateReceiversLength = rateReceivers.length;

        // Get the last index of the all the rate receivers
        uint256 lastIndex = rateReceiversLength - 1;

        if (lastIndex != _index) {
            // Get the last rate receiver
            RateReceiver memory lastValue = rateReceivers[lastIndex];

            // Replace the index value with the last index value
            rateReceivers[_index] = lastValue;
        }

        rateReceivers.pop();

        emit RateReceiverRemoved(_rateReceiverToBeRemoved._chainId, _rateReceiverToBeRemoved._contract);
    }
```

**File:** lib/openzeppelin-contracts/contracts/access/Ownable.sol (L69-77)
```text
    function transferOwnership(address newOwner) public virtual onlyOwner {
        require(newOwner != address(0), "Ownable: new owner is the zero address");
        _transferOwnership(newOwner);
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`).
     * Internal function without access restriction.
     */
```

**File:** lib/openzeppelin-contracts/contracts/access/Ownable.sol (L78-87)
```text
    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner;
        _owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }
}
```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L10-24)
```text
contract RSETHRateProvider is CrossChainRateProvider {
    address public immutable rsETHPriceOracle;

    constructor(address _rsETHPriceOracle, uint16 _dstChainId, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;

        rateInfo = RateInfo({
            tokenSymbol: "rsETH",
            tokenAddress: 0xA1290d69c65A6Fe4DF752f95823fae25cB99e5A7, // rsETH token address on ETH mainnet
            baseTokenSymbol: "ETH",
            baseTokenAddress: address(0) // Address 0 for native tokens
        });
        dstChainId = _dstChainId;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L9-15)
```text
contract RSETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L9-23)
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
```
